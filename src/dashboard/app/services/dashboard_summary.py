"""Bounded, read-only inventory for the asset-first Dashboard home.

Each metric names its own authority and counting unit.  There is intentionally
no grand asset total: a completed task, a Lesson and a report may describe the
same work.  Activity/usage belongs to the existing AI-assets APIs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
from datetime import date, datetime, timezone
from typing import Any

from data_foundation.db import connect
from data_foundation.diary_markdown import read_diary_markdown_documents
from data_foundation.memory_corpus import _lesson_key, canonical_lessons_path
from data_foundation.paths import RuntimePaths, load_paths
from data_foundation.settings import is_nova_task_enabled

from . import archive, skill_assets
from .dashboard_state import attach_dashboard_state, source_error


logger = logging.getLogger(__name__)
SCHEMA_VERSION = "actanara.dashboard-summary.v1"
MAX_LEDGER_DAYS = 90
MAX_LEDGER_BYTES = 16 * 1024 * 1024
MAX_RECENT_PER_SOURCE = 8
MAX_RECENT_ITEMS = 20
MAX_DOCUMENT_CHARS = 200_000


class DashboardDocumentQueryError(ValueError):
    pass


class DashboardDocumentNotFound(LookupError):
    pass


class DashboardDocumentAmbiguous(LookupError):
    pass


class DashboardSkillNotFound(LookupError):
    pass


class DashboardSkillsUnavailable(RuntimeError):
    pass


def _metric(count: int | None, *, source: str, unit: str, status: str | None = None,
            scope: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "count": count,
        "status": status or ("unavailable" if count is None else "empty" if count == 0 else "ready"),
        "source": source,
        "unit": unit,
        "scope": scope or {"kind": "all-persisted", "complete": True},
    }


def _opaque_id(kind: str, identity: str) -> str:
    return kind + "-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _safe_date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def _recent_item(*, kind: str, identity: str, title: Any, business_date: Any,
                 status: str, source: str, paths: RuntimePaths,
                 href: str, **metadata: Any) -> dict[str, Any]:
    return {
        "id": _opaque_id(kind, identity),
        "type": kind,
        "title": archive._public_text(title, paths=paths, maximum=240) or "Untitled",
        "businessDate": _safe_date(business_date),
        "status": status,
        "source": source,
        "href": href,
        **metadata,
    }


def _tasks(paths: RuntimePaths) -> tuple[dict, dict, list, list]:
    source = "nova-task-v2-sqlite"
    scope = {
        "kind": "all-persisted", "complete": True,
        "definition": "task-nodes-in-completed-done-settled",
        "excludes": ["track", "workstream", "subtask", "step", "archived"],
    }
    try:
        enabled = is_nova_task_enabled(paths)
        # Saved outcomes remain assets when task management is disabled.
        with connect(paths, read_only=True) as connection:
            count = int(connection.execute(
                "SELECT COUNT(*) FROM nova_task_nodes "
                "WHERE node_type = 'task' AND status IN ('completed', 'done', 'settled')"
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT node_id, title, status, completed_at, updated_at FROM nova_task_nodes "
                "WHERE node_type = 'task' AND status IN ('completed', 'done', 'settled') "
                "ORDER BY COALESCE(completed_at, updated_at) DESC, node_id LIMIT ?",
                (MAX_RECENT_PER_SOURCE,),
            ).fetchall()
            candidates = int(connection.execute(
                "SELECT COUNT(*) FROM nova_task_candidates WHERE status = 'pending_review'"
            ).fetchone()[0])
        recent = [
            _recent_item(
                kind="task", identity=str(row["node_id"]), title=row["title"],
                business_date=row["completed_at"] or row["updated_at"], status=str(row["status"]),
                source=source, paths=paths, href="/tasks", nodeType="task",
            ) for row in rows
        ]
        tasks = _metric(count, source=source, unit="tasks", scope=scope)
        tasks["enabled"] = enabled
        review = _metric(candidates, source=source, unit="proposals")
        review["enabled"] = enabled
        return tasks, review, recent, []
    except Exception:
        logger.warning("Dashboard completed-task inventory read failed")
        error = source_error(source)
        return (
            _metric(None, source=source, unit="tasks", scope={**scope, "complete": False}),
            _metric(None, source=source, unit="proposals", scope={"kind": "all-persisted", "complete": False}),
            [], [error],
        )


def _lessons_and_drafts(paths: RuntimePaths) -> tuple[dict, dict, list, list]:
    source = "skill-pass-ledger"
    scope: dict[str, Any] = {
        "kind": "latest-business-days", "limit": MAX_LEDGER_DAYS,
        "definition": "skill-pass-lessons-only",
        "versionPolicy": "newest-prompt-version-per-business-date",
        "includedDays": 0, "availableDays": None, "complete": False,
        "from": None, "through": None,
    }
    errors: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    counts = {"lesson": 0, "skill": 0}
    try:
        # This is the same current-ledger policy used by Skill Asset Memory.
        # Do not merge v21/v22 rows or deduplicate unrelated days by title.
        candidates = skill_assets._ledger_candidates(paths, None)
        by_date: dict[str, tuple[str, int, str, str]] = {}
        for candidate in candidates:
            if candidate[0] not in by_date:
                by_date[candidate[0]] = candidate
        scope["availableDays"] = len(by_date)
        selected = list(by_date.values())[:MAX_LEDGER_DAYS]
        scope["complete"] = len(selected) == len(by_date)
        consumed = 0
        for business_date, _version, prompt_version, filename in selected:
            if consumed >= MAX_LEDGER_BYTES:
                scope["complete"] = False
                scope["byteLimitReached"] = True
                break
            try:
                raw = skill_assets._read_ledger_bytes(skill_assets._ledger_directory(paths), filename)
                consumed += len(raw)
                private_rows = skill_assets._decode_private_rows(raw)
                rows = [skill_assets._project_row(
                    row, business_date=business_date, prompt_version=prompt_version,
                ) for row in private_rows]
                if len({row["reviewId"] for row in rows}) != len(rows):
                    raise skill_assets.SkillAssetLedgerError("duplicate review identifier")
                # Commit a day's count only after its entire authority validates.
                for row in rows:
                    asset_class = row["assetClass"]
                    if asset_class not in counts:
                        continue
                    counts[asset_class] += 1
                    if sum(item["type"] == asset_class for item in recent) < MAX_RECENT_PER_SOURCE:
                        recent.append(_recent_item(
                            kind=asset_class,
                            identity=f"{business_date}:{prompt_version}:{row['reviewId']}",
                            title=row["title"], business_date=business_date,
                            status="documented" if asset_class == "lesson" else "draft",
                            source=source, paths=paths, href="#page-static",
                            promptVersion=prompt_version, reviewId=row["reviewId"],
                            summary=archive._public_text(row.get("summary") or row.get("reason"), paths=paths, maximum=1600),
                        ))
                scope["includedDays"] += 1
                scope["through"] = scope["through"] or business_date
                scope["from"] = business_date
            except Exception:
                errors.append(source_error(source, code="ledger-read-failed"))
                scope["complete"] = False
    except Exception:
        logger.warning("Dashboard Skill Pass inventory read failed")
        errors.append(source_error(source))
    # Failed reads are unknown, never silently reported as an empty inventory.
    status = "degraded" if errors else None
    lessons = _metric(None if errors else counts["lesson"], source=source, unit="lessons",
                      status=status, scope=dict(scope))
    drafts = _metric(None if errors else counts["skill"], source=source, unit="proposals",
                     status=status, scope={**scope, "definition": "finalized-ledger-proposals-not-registration-backlog"})
    if errors:
        lessons["availableCount"] = counts["lesson"]
        drafts["availableCount"] = counts["skill"]
    return lessons, drafts, recent, errors


def _skills(paths: RuntimePaths) -> tuple[dict, list]:
    inventory = archive._canonical_skill_inventory(paths)
    # The existing inventory reader is bounded to 400 valid canonical assets.
    from diary_generator.skill_pass_minimal_harness import MAX_SKILL_LIBRARY_ASSETS

    count = inventory["count"]
    return _metric(
        count, source="canonical-skill-library", unit="canonicalSkills",
        status=inventory["status"],
        scope={
            "kind": "valid-canonical-library-assets", "limit": MAX_SKILL_LIBRARY_ASSETS,
            "complete": count is not None and count < MAX_SKILL_LIBRARY_ASSETS,
            "definition": "canonical-skills-excluding-agent-installed-copies",
        },
    ), inventory["sourceErrors"]


def _learning_notes(paths: RuntimePaths) -> tuple[dict, list, list]:
    """Read the canonical Lessons authority, independently of Skill Pass.

    Identity follows the canonical writer.  A usable text field follows the
    existing memory-corpus Lesson reader; arbitrary JSON objects are not assets.
    """
    source = "canonical-learning-lessons"
    scope = {
        "kind": "canonical-lesson-records", "complete": True,
        "definition": "canonical-lessons-with-text-excluding-skill-pass",
        "byteLimit": MAX_LEDGER_BYTES,
        "excludes": ["legacy-lesson-files", "skill-pass-ledgers", "records-without-text"],
    }
    path = canonical_lessons_path(paths)
    try:
        # Directory/file handles forbid symlinks.  Canonical writer defaults can
        # produce 0644 files, so unlike private Skill Pass ledgers that mode is
        # accepted here; only sanitized titles leave this projection.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        directory = os.open(path.parent, flags | getattr(os, "O_DIRECTORY", 0))
        try:
            if os.fstat(directory).st_uid != os.geteuid():
                raise ValueError("unowned Lessons directory")
            descriptor = os.open(path.name, flags, dir_fd=directory)
            try:
                before = os.fstat(descriptor)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                        or before.st_nlink != 1 or before.st_size > MAX_LEDGER_BYTES):
                    raise ValueError("unsafe or oversized Lessons authority")
                chunks = []
                remaining = before.st_size
                while remaining:
                    chunk = os.read(descriptor, min(remaining, 131_072))
                    if not chunk:
                        raise ValueError("Lessons changed during read")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                after = os.fstat(descriptor)
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns
                ) or os.read(descriptor, 1):
                    raise ValueError("Lessons changed during read")
                text = b"".join(chunks).decode("utf-8")
            finally:
                os.close(descriptor)
        finally:
            os.close(directory)
    except FileNotFoundError:
        return _metric(0, source=source, unit="lessons", scope=scope), [], []
    except Exception:
        return _metric(None, source=source, unit="lessons", scope={**scope, "complete": False}), [], [source_error(source)]

    known: dict[str, dict] = {}
    malformed = False
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line, object_pairs_hook=skill_assets._no_duplicate_object)
            if not isinstance(row, dict):
                raise ValueError("Lesson must be an object")
            if not isinstance(row.get("text"), str) or not row["text"].strip():
                continue
            identity = _lesson_key(row)
            known.setdefault(identity, _recent_item(
                kind="lesson", identity=f"canonical:{identity}", title=row.get("title") or row["text"],
                business_date=row.get("date"), status="documented", source=source, paths=paths,
                href="#page-static", summary=archive._public_text(row["text"], paths=paths, maximum=1600),
            ))
        except (ValueError, TypeError):
            malformed = True
    errors = [source_error(source, code="invalid-lesson-record")] if malformed else []
    metric = _metric(None if malformed else len(known), source=source, unit="lessons",
                     status="degraded" if malformed else None,
                     scope={**scope, "complete": not malformed})
    if malformed:
        metric["availableCount"] = len(known)
    recent = sorted(known.values(), key=lambda item: (item["businessDate"] or "", item["id"]), reverse=True)
    return metric, recent[:MAX_RECENT_PER_SOURCE], errors


def _reports(paths: RuntimePaths) -> tuple[dict, list, list]:
    source = "foundation-report-inventory"
    scope = {
        "kind": "all-persisted", "complete": True,
        "definition": "ready-narrative-technical-learning-documents",
        "excludes": ["period-metric-projections", "stale-documents"],
    }
    try:
        with connect(paths, read_only=True) as connection:
            groups = connection.execute(
                "SELECT report_type, COUNT(*) AS count FROM diary_markdown_documents "
                "WHERE status = 'ready' AND report_type IN ('narrative', 'technical', 'learning') "
                "GROUP BY report_type"
            ).fetchall()
            rows = connection.execute(
                "SELECT document_key, business_date, report_type, title FROM diary_markdown_documents "
                "WHERE status = 'ready' AND report_type IN ('narrative', 'technical', 'learning') "
                "ORDER BY business_date DESC, parsed_at DESC, document_key LIMIT ?",
                (MAX_RECENT_PER_SOURCE,),
            ).fetchall()
        by_type = {kind: 0 for kind in ("narrative", "technical", "learning")}
        by_type.update({str(row["report_type"]): int(row["count"]) for row in groups})
        metric = _metric(sum(by_type.values()), source=source, unit="documents", scope=scope)
        metric["byType"] = by_type
        recent = [
            _recent_item(
                kind="report", identity=str(row["document_key"]),
                title=row["title"] or f"{row['business_date']} · {row['report_type']}",
                business_date=row["business_date"], status="ready", source=source, paths=paths,
                href="/dashboard", reportType=str(row["report_type"]),
            ) for row in rows
        ]
        return metric, recent, []
    except Exception:
        logger.warning("Dashboard report inventory read failed")
        return _metric(None, source=source, unit="documents", scope={**scope, "complete": False}), [], [source_error(source)]


def get_summary(*, paths: RuntimePaths | None = None) -> dict[str, Any]:
    selected = paths or load_paths()
    tasks, task_candidates, task_recent, task_errors = _tasks(selected)
    lessons, skill_drafts, skill_recent, lesson_errors = _lessons_and_drafts(selected)
    learning_notes, learning_recent, learning_errors = _learning_notes(selected)
    skills, skill_errors = _skills(selected)
    reports, report_recent, report_errors = _reports(selected)
    errors = [*task_errors, *lesson_errors, *learning_errors, *skill_errors, *report_errors]
    assets = {"completedTasks": tasks, "lessons": lessons, "learningNotes": learning_notes, "skills": skills, "reports": reports}
    review = {"skillDrafts": skill_drafts, "taskCandidates": task_candidates}
    recent = sorted(
        [*task_recent, *skill_recent, *learning_recent, *report_recent],
        key=lambda item: (item["businessDate"] or "", item["id"]), reverse=True,
    )[:MAX_RECENT_ITEMS]
    known_counts = [item["count"] for item in [*assets.values(), *review.values()] if item["count"] is not None]
    status = "degraded" if errors else "ready" if any(known_counts) else "empty"
    sources = [
        {"id": metric["source"], "status": metric["status"], "scope": metric["scope"], "readOnly": True}
        for metric in assets.values()
    ]
    return attach_dashboard_state({
        "schemaVersion": SCHEMA_VERSION,
        "status": status,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "assets": assets,
        "review": review,
        "recent": recent,
        "recentLimit": MAX_RECENT_ITEMS,
        "sources": sources,
        "sourceErrors": errors,
    }, status=status, source_errors=errors)


def get_document(business_date: str, document_type: str, *, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Open a generated document by its business date and actual report type."""
    try:
        selected_date = date.fromisoformat(business_date)
        if selected_date.isoformat() != business_date:
            raise ValueError("noncanonical business date")
    except (TypeError, ValueError) as error:
        raise DashboardDocumentQueryError("businessDate must be YYYY-MM-DD") from error
    if document_type not in {"narrative", "technical", "learning"}:
        raise DashboardDocumentQueryError("type must be narrative, technical, or learning")
    selected = paths or load_paths()
    documents = read_diary_markdown_documents(selected, selected_date, selected_date)
    matches = [document for document in documents if document.get("report_type") == document_type]
    if not matches:
        raise DashboardDocumentNotFound("The requested saved document was not found.")
    if len(matches) != 1:
        raise DashboardDocumentAmbiguous("More than one saved document matches this date and type.")
    document = matches[0]
    from .diary import _document_to_markdown

    content = _public_markdown(_document_to_markdown(document), selected)
    truncated = len(content) > MAX_DOCUMENT_CHARS
    return attach_dashboard_state({
        "status": "ready", "businessDate": business_date, "date": business_date,
        "type": document_type,
        "title": archive._public_text(document.get("title"), paths=selected, maximum=240)
                 or f"{business_date} · {document_type}",
        "content": content[:MAX_DOCUMENT_CHARS], "truncated": truncated,
        "source": "foundation-diary-markdown-documents",
    })


def _public_markdown(content: str, paths: RuntimePaths) -> str:
    # Preserve Markdown whitespace while using the same redaction policy as
    # summary titles.  Do not return document keys or relative filesystem paths.
    private_paths = (paths.home, paths.db_path, paths.diary_dir, paths.reports_dir,
                     paths.archives_dir, paths.state_dir)
    for private_path in sorted({str(item) for item in private_paths if item}, key=len, reverse=True):
        content = content.replace(private_path, "[local path]")
    content = archive._COMMON_LOCAL_PATH_RE.sub("[local path]", content)
    content = archive._WINDOWS_PATH_RE.sub("[local path]", content)
    content = archive._SECRET_ASSIGNMENT_RE.sub(r"\1\2[redacted]", content)
    content = archive._BEARER_RE.sub("Bearer [redacted]", content)
    return content


def _read_canonical_library(paths: RuntimePaths):
    """Use the same owner-controlled library and bounded reader as Skill Pass."""
    from diary_generator.skill_pass_minimal_harness import load_existing_skill_library

    root = paths.home / "assets" / "skills"
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise DashboardSkillsUnavailable("The canonical Skill library is unavailable.") from error
    if (not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid() or not os.access(root, os.R_OK | os.X_OK)):
        raise DashboardSkillsUnavailable("The canonical Skill library is unavailable.")
    try:
        return load_existing_skill_library(paths)
    except Exception as error:
        raise DashboardSkillsUnavailable("The canonical Skill library is unavailable.") from error


def get_skills(*, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """List saved canonical Skills without requiring a usage snapshot."""
    from diary_generator.skill_pass_minimal_harness import MAX_SKILL_LIBRARY_ASSETS

    selected = paths or load_paths()
    library = _read_canonical_library(selected)
    items = [{
        "id": asset.asset_id,
        "name": asset.name,
        "description": archive._public_text(asset.description, paths=selected, maximum=1200),
    } for asset in library]
    return attach_dashboard_state({
        "status": "ready" if items else "empty", "items": items, "count": len(items),
        "scope": {
            "kind": "valid-canonical-library-assets", "limit": MAX_SKILL_LIBRARY_ASSETS,
            "complete": len(items) < MAX_SKILL_LIBRARY_ASSETS,
            "definition": "canonical-skills-excluding-agent-installed-copies",
        },
        "source": "canonical-skill-library",
    }, empty=not items)


def get_skill(asset_id: str, *, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Resolve an opaque library identity, never a caller-controlled path."""
    if not isinstance(asset_id, str) or re.fullmatch(r"asset-[0-9a-f]{12}", asset_id) is None:
        raise DashboardSkillNotFound("The requested saved Skill was not found.")
    selected = paths or load_paths()
    asset = next((item for item in _read_canonical_library(selected) if item.asset_id == asset_id), None)
    if asset is None:
        raise DashboardSkillNotFound("The requested saved Skill was not found.")
    content = _public_markdown(asset.body, selected)
    return attach_dashboard_state({
        "status": "ready", "id": asset.asset_id, "name": asset.name, "title": asset.name,
        "description": archive._public_text(asset.description, paths=selected, maximum=1200),
        "content": content[:MAX_DOCUMENT_CHARS], "truncated": len(content) > MAX_DOCUMENT_CHARS,
        "source": "canonical-skill-library",
    })

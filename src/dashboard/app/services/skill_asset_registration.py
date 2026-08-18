"""Register finalized procedural Skills without owning Skill discovery.

Crystallization decides whether an asset is create/extend/covered/conflict/reject.
This module accepts only finalized create/extend drafts, writes the Actanara
cross-agent canonical copy, and mirrors it to detected supported local agents.
Customized external files are never overwritten.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services import skill_assets
from data_foundation.external_tool_catalog import detected_external_tool_ids
from data_foundation.external_tool_definitions import TOOL_CATALOG
from data_foundation.paths import RuntimePaths, load_paths
from data_foundation.settings import external_tool_path
from diary_generator import skill_pass_minimal_support as support


_MAX_SELECTED = 8
_MAX_SKILL_BYTES = 128 * 1024
_REGISTER_LOCK = threading.Lock()
_MANAGED_PLACEHOLDER = "__ACTANARA_SKILL_SHA256__"
_MANAGED_RE = re.compile(
    r"(?m)^<!-- actanara-managed-procedural-skill "
    r"name=(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*) "
    r"content-sha256=(?P<digest>[0-9a-f]{64}) -->$"
)
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)


def _canonical_asset_id(name: str) -> str:
    identity = f"actanara\0{name}"
    return "asset-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _frontmatter_value(body: str, key: str) -> str:
    rows = re.findall(
        rf"(?mi)^{re.escape(key)}[ \t]*:[ \t]*(?P<value>[^\n]+?)[ \t]*$",
        body,
    )
    if len(rows) != 1:
        return ""
    value = rows[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return value


def _managed_content(markdown: str, *, name: str, description: str) -> str:
    source = str(markdown or "").strip() + "\n"
    if _MANAGED_RE.search(source) or _MANAGED_PLACEHOLDER in source:
        raise skill_assets.SkillAssetActionError(
            "The Skill draft contains a reserved management marker.",
            code="skill-registration-invalid-draft",
            status_code=409,
        )
    frontmatter = _FRONTMATTER_RE.match(source)
    if frontmatter is None:
        raise skill_assets.SkillAssetActionError(
            "The Skill draft is missing valid frontmatter.",
            code="skill-registration-invalid-draft",
            status_code=409,
        )
    if (
        support._NAME_RE.fullmatch(name) is None
        or _frontmatter_value(frontmatter.group("body"), "name") != name
        or _frontmatter_value(frontmatter.group("body"), "description") != description
    ):
        raise skill_assets.SkillAssetActionError(
            "The Skill draft identity does not match its ledger authority.",
            code="skill-registration-invalid-draft",
            status_code=409,
        )
    marker = (
        "<!-- actanara-managed-procedural-skill "
        f"name={name} content-sha256={_MANAGED_PLACEHOLDER} -->"
    )
    prepared = source[: frontmatter.end()] + "\n" + marker + "\n" + source[frontmatter.end() :]
    digest = hashlib.sha256(prepared.encode("utf-8")).hexdigest()
    return prepared.replace(_MANAGED_PLACEHOLDER, digest, 1)


def _verified_managed(text: str, *, name: str) -> bool:
    matches = list(_MANAGED_RE.finditer(str(text or "")))
    if len(matches) != 1 or matches[0].group("name") != name:
        return False
    match = matches[0]
    normalized = (
        text[: match.start("digest")]
        + _MANAGED_PLACEHOLDER
        + text[match.end("digest") :]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() == match.group("digest")


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else expanded.absolute()


def _ensure_safe_directory(path: Path) -> Path:
    target = _absolute(path)
    current = Path(target.anchor)
    for part in target.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise skill_assets.SkillAssetActionError(
                "A Skill registration target has an unsafe directory chain.",
                code="skill-registration-unsafe-target",
                status_code=409,
            )
    if target.lstat().st_uid != os.geteuid():
        raise skill_assets.SkillAssetActionError(
            "A Skill registration target is not owned by the current user.",
            code="skill-registration-unsafe-target",
            status_code=409,
        )
    return target


def _safe_read(path: Path) -> str | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or before.st_size <= 0
        or before.st_size > _MAX_SKILL_BYTES
    ):
        raise skill_assets.SkillAssetActionError(
            "An existing Skill file is unsafe or unreadable.",
            code="skill-registration-unsafe-existing",
            status_code=409,
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise skill_assets.SkillAssetActionError(
                "An existing Skill changed while it was being inspected.",
                code="skill-registration-existing-changed",
                status_code=409,
            )
        payload = b""
        while len(payload) <= _MAX_SKILL_BYTES:
            chunk = os.read(descriptor, min(65536, _MAX_SKILL_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        after = os.fstat(descriptor)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise skill_assets.SkillAssetActionError(
                "An existing Skill changed while it was being inspected.",
                code="skill-registration-existing-changed",
                status_code=409,
            )
        if len(payload) != before.st_size or len(payload) > _MAX_SKILL_BYTES:
            raise skill_assets.SkillAssetActionError(
                "An existing Skill file exceeds the safe registration limit.",
                code="skill-registration-unsafe-existing",
                status_code=409,
            )
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise skill_assets.SkillAssetActionError(
            "An existing Skill is not UTF-8.",
            code="skill-registration-unsafe-existing",
            status_code=409,
        ) from exc
    finally:
        os.close(descriptor)


def _write_atomic(path: Path, content: str) -> None:
    parent = _ensure_safe_directory(path.parent)
    if path.parent != parent:
        raise skill_assets.SkillAssetActionError(
            "Skill registration path normalization failed.",
            code="skill-registration-unsafe-target",
            status_code=409,
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        if _safe_read(path) != content:
            raise OSError("registered Skill failed post-write verification")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _classify(path: Path, desired: str, *, name: str) -> tuple[str, str | None]:
    current = _safe_read(path)
    if current is None:
        return "create", None
    if current == desired:
        return "current", current
    if _verified_managed(current, name=name):
        return "update", current
    return "preserve-customized", current


def _backup(paths: RuntimePaths, *, scope: str, name: str, content: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = (
        paths.state_dir
        / "backups"
        / "procedural-skill-registration"
        / stamp
        / scope
        / name
        / "SKILL.md"
    )
    _write_atomic(target, content)


def _apply(
    path: Path,
    desired: str,
    *,
    name: str,
    state: str,
    previous: str | None,
    paths: RuntimePaths,
    scope: str,
) -> str:
    if state == "current":
        return "already-current"
    if state == "preserve-customized":
        return "preserved-customized"
    if state == "update" and previous is not None:
        _backup(paths, scope=scope, name=name, content=previous)
    try:
        _write_atomic(path, desired)
    except Exception:
        if _safe_read(path) == desired:
            _rollback_write(path, previous=previous, desired=desired)
        raise
    return "updated" if state == "update" else "created"


def _rollback_write(path: Path, *, previous: str | None, desired: str) -> None:
    current = _safe_read(path)
    if current != desired:
        raise OSError("registered Skill changed before rollback")
    if previous is None:
        path.unlink()
    else:
        _write_atomic(path, previous)


def _supported_detected_tools(paths: RuntimePaths) -> tuple[str, ...]:
    detected = set(detected_external_tool_ids(paths))
    return tuple(
        tool
        for tool in TOOL_CATALOG
        if tool in detected
        and "skillsRoot"
        in set(
            (TOOL_CATALOG[tool].get("globalSkillRegistration") or {}).get("targets")
            or ()
        )
    )


def register_finalized_skill_assets(
    payload: Any,
    *,
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    """Install selected finalized drafts into canonical and external Skill roots."""

    if not isinstance(payload, dict):
        raise skill_assets.SkillAssetActionError(
            "A Skill registration request must be an object.",
            code="skill-registration-invalid",
            status_code=400,
        )
    business_date = str(payload.get("businessDate") or "").strip()
    prompt_version = str(payload.get("promptVersion") or "").strip()
    review_ids = payload.get("reviewIds")
    if (
        not re.fullmatch(r"\d{4}-\d{2}-\d{2}", business_date)
        or not re.fullmatch(r"minimal-v\d+", prompt_version)
        or not isinstance(review_ids, list)
        or not 1 <= len(review_ids) <= _MAX_SELECTED
        or any(not isinstance(item, str) or not re.fullmatch(r"review-\d{3}", item) for item in review_ids)
        or len(set(review_ids)) != len(review_ids)
    ):
        raise skill_assets.SkillAssetActionError(
            "Select between one and eight finalized Skill review IDs.",
            code="skill-registration-invalid",
            status_code=400,
        )

    selected_paths = paths or load_paths()
    with skill_assets._FORCE_LOCK, _REGISTER_LOCK:
        selected_date, selected_prompt, filename = skill_assets._selected_ledger(
            selected_paths, business_date, prompt_version
        )
        root = skill_assets._ledger_directory(selected_paths)
        raw = skill_assets._read_ledger_bytes(root, filename)
        rows = skill_assets._decode_private_rows(raw)
        rows_by_review: dict[str, dict[str, Any]] = {}
        for row in rows:
            review_id = str(row.get("reviewId") or "")
            if review_id in rows_by_review:
                raise skill_assets.SkillAssetLedgerError("duplicate review identifier")
            rows_by_review[review_id] = row
        selected_rows = [rows_by_review.get(review_id) for review_id in review_ids]
        drafts: list[dict[str, str]] = []
        for review_id, row in zip(review_ids, selected_rows):
            if row is None:
                raise skill_assets.SkillAssetActionError(
                    "A selected Skill is no longer present in the current ledger.",
                    code="skill-registration-stale-selection",
                    status_code=409,
                )
            public = skill_assets._project_row(
                row, business_date=selected_date, prompt_version=selected_prompt
            )
            action = str(public.get("libraryAction") or "")
            if public.get("assetClass") != "skill" or action not in {"create", "extend"}:
                raise skill_assets.SkillAssetActionError(
                    "Only finalized create/extend Skill drafts may be registered.",
                    code="skill-registration-not-finalized",
                    status_code=409,
                )
            name = str(public.get("skillName") or "")
            description = str(public.get("skillDescription") or "")
            desired = _managed_content(
                str(public.get("skillMarkdown") or ""),
                name=name,
                description=description,
            )
            existing_name = str(row.get("existingSkillName") or "")
            existing_asset_id = str(row.get("existingAssetId") or "")
            if action == "create" and (existing_name or existing_asset_id):
                raise skill_assets.SkillAssetActionError(
                    "A create proposal unexpectedly references an existing Skill.",
                    code="skill-registration-invalid-authority",
                    status_code=409,
                )
            if action == "extend" and (
                existing_name != name or existing_asset_id != _canonical_asset_id(name)
            ):
                raise skill_assets.SkillAssetActionError(
                    "An extend proposal no longer matches canonical Skill authority.",
                    code="skill-registration-invalid-authority",
                    status_code=409,
                )
            drafts.append(
                {"reviewId": review_id, "name": name, "action": action, "content": desired}
            )
        names = [item["name"] for item in drafts]
        if len(names) != len(set(names)):
            raise skill_assets.SkillAssetActionError(
                "Selected proposals collide on the same Skill name.",
                code="skill-registration-name-collision",
                status_code=409,
            )

        canonical_root = _ensure_safe_directory(selected_paths.home / "assets" / "skills")
        canonical_plans: list[tuple[dict[str, str], Path, str, str | None]] = []
        for draft in drafts:
            skill_dir = _ensure_safe_directory(canonical_root / draft["name"])
            target = skill_dir / "SKILL.md"
            state, previous = _classify(target, draft["content"], name=draft["name"])
            if state == "preserve-customized" or (
                state == "update" and draft["action"] != "extend"
            ):
                raise skill_assets.SkillAssetActionError(
                    "The canonical Skill changed after crystallization and was preserved.",
                    code="skill-registration-canonical-conflict",
                    status_code=409,
                )
            canonical_plans.append((draft, target, state, previous))

        tools = _supported_detected_tools(selected_paths)
        external_plans: dict[str, list[tuple[str, Path, str, str | None]]] = {}
        for draft in drafts:
            planned: list[tuple[str, Path, str, str | None]] = []
            for tool in tools:
                tool_root = _ensure_safe_directory(
                    external_tool_path(tool, "skillsRoot", selected_paths)
                )
                target = _ensure_safe_directory(tool_root / draft["name"]) / "SKILL.md"
                state, previous = _classify(target, draft["content"], name=draft["name"])
                planned.append((tool, target, state, previous))
            external_plans[draft["reviewId"]] = planned

        if skill_assets._read_ledger_bytes(root, filename) != raw:
            raise skill_assets.SkillAssetActionError(
                "The Skill asset ledger changed during registration; retry the review.",
                code="skill-registration-ledger-changed",
                status_code=409,
            )

        results: list[dict[str, Any]] = []
        registered_tools: set[str] = set()
        applied: list[tuple[Path, str | None, str]] = []
        try:
            for draft, target, state, previous in canonical_plans:
                canonical_result = _apply(
                    target,
                    draft["content"],
                    name=draft["name"],
                    state=state,
                    previous=previous,
                    paths=selected_paths,
                    scope="actanara",
                )
                if canonical_result in {"created", "updated"}:
                    applied.append((target, previous, draft["content"]))
                external_results = []
                for tool, external_target, external_state, external_previous in external_plans[
                    draft["reviewId"]
                ]:
                    result = _apply(
                        external_target,
                        draft["content"],
                        name=draft["name"],
                        state=external_state,
                        previous=external_previous,
                        paths=selected_paths,
                        scope=tool,
                    )
                    if result in {"created", "updated"}:
                        applied.append(
                            (external_target, external_previous, draft["content"])
                        )
                    if result != "preserved-customized":
                        registered_tools.add(tool)
                    external_results.append({"tool": tool, "result": result})
                results.append(
                    {
                        "reviewId": draft["reviewId"],
                        "name": draft["name"],
                        "libraryAction": draft["action"],
                        "canonicalResult": canonical_result,
                        "externalResults": external_results,
                    }
                )
        except Exception:
            rollback_errors = []
            for target, previous, desired in reversed(applied):
                try:
                    _rollback_write(target, previous=previous, desired=desired)
                except Exception as exc:
                    rollback_errors.append(str(exc))
            if rollback_errors:
                raise OSError("procedural Skill registration rollback failed")
            raise

        return {
            "status": "completed",
            "registrationRequested": True,
            "registeredTools": sorted(registered_tools),
            "eligibleTools": list(tools),
            "results": results,
        }

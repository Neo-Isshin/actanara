"""Dashboard review and explicit human actions for Skill Pass assets.

The Skill Pass JSONL ledger is private Runtime authority.  This service exposes
only the fields needed for human review.  The sole mutation supported here is
an explicit human request to crystallize selected Lessons; it preserves the
upstream decision and never grants registration authority.
"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from data_foundation.paths import RuntimePaths, load_paths


LEDGER_SCHEMA = "actanara.skill-asset-ledger.v1"
_LEDGER_FILE_RE = re.compile(
    r"^skill-harness-(?P<prompt>minimal-v\d+)-assets-"
    r"(?P<date>\d{4}-\d{2}-\d{2})\.jsonl$"
)
_CANDIDATE_ID_RE = re.compile(r"^candidate-\d{3}$")
_REVIEW_ID_RE = re.compile(r"^review-\d{3}$")
_ASSET_CLASSES = frozenset({"skill", "lesson", "reference", "discard"})
_ORIGINAL_DECISIONS = _ASSET_CLASSES
_LIBRARY_ACTIONS = frozenset({"create", "extend", "covered", "conflict", "reject"})
_COMPLETIONS = frozenset({"verified", "incomplete", "bundled"})
_MAX_LEDGER_BYTES = 4 * 1024 * 1024
_MAX_LEDGER_ROWS = 400
_MAX_TEXT = 8_000
_MAX_SKILL_MARKDOWN = 32_000
_MAX_FORCED_LESSONS = 8
_FORCE_LOCK = threading.Lock()


class SkillAssetLedgerError(ValueError):
    """Raised when a private ledger cannot be projected safely."""


class SkillAssetActionError(ValueError):
    """Raised when an explicit human Skill asset action cannot be completed."""

    def __init__(self, message: str, *, code: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SkillAssetLedgerError("duplicate JSON key")
        value[key] = item
    return value


def _bounded_text(value: Any, *, maximum: int = _MAX_TEXT, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise SkillAssetLedgerError("expected text field")
    text = value.strip()
    if not text and not optional:
        raise SkillAssetLedgerError("required text field is empty")
    if len(text) > maximum or "\x00" in text:
        raise SkillAssetLedgerError("text field exceeds public projection contract")
    return text or None


def _enum(value: Any, allowed: frozenset[str], *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _bounded_text(value, maximum=80, optional=optional)
    if text is None:
        return None
    if text not in allowed:
        raise SkillAssetLedgerError("unknown ledger enum")
    return text


def _score_projection(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {"evidence", "value", "reuse", "program"}:
        raise SkillAssetLedgerError("invalid score contract")
    scores: dict[str, int] = {}
    for key in ("evidence", "value", "reuse", "program"):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0 or item > 5:
            raise SkillAssetLedgerError("score outside 0..5")
        scores[key] = item
    return scores


def _project_row(value: Any, *, business_date: str, prompt_version: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SkillAssetLedgerError("ledger row must be an object")
    if value.get("schema") != LEDGER_SCHEMA:
        raise SkillAssetLedgerError("ledger schema mismatch")
    if value.get("businessDate") != business_date or value.get("promptVersion") != prompt_version:
        raise SkillAssetLedgerError("ledger file context mismatch")

    asset_class = _enum(value.get("assetClass"), _ASSET_CLASSES)
    original_decision = _enum(value.get("originalDecision"), _ORIGINAL_DECISIONS)
    candidate_id = _bounded_text(value.get("candidateId"), maximum=32)
    review_id = _bounded_text(value.get("reviewId"), maximum=32)
    if not _CANDIDATE_ID_RE.fullmatch(candidate_id or "") or not _REVIEW_ID_RE.fullmatch(review_id or ""):
        raise SkillAssetLedgerError("invalid candidate or review identifier")

    evidence = value.get("evidence")
    evidence_records = value.get("evidenceRecords")
    if not isinstance(evidence, list) or not isinstance(evidence_records, list):
        raise SkillAssetLedgerError("invalid evidence contract")
    # A compact locator may cover several source records.  These collections
    # therefore have independent cardinalities and must not be zip-aligned.
    if len(evidence) > 128 or len(evidence_records) > 128:
        raise SkillAssetLedgerError("evidence cardinality exceeds the public contract")
    for locator in evidence:
        _bounded_text(locator, maximum=256)
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in evidence_records):
        raise SkillAssetLedgerError("invalid evidence record identifier")

    library_action = _enum(value.get("libraryAction"), _LIBRARY_ACTIONS, optional=True)
    completion = _enum(value.get("completion"), _COMPLETIONS, optional=True)
    skill_name = _bounded_text(value.get("skillName"), maximum=64, optional=True)
    skill_description = _bounded_text(value.get("skillDescription"), maximum=1_000, optional=True)
    skill_markdown = _bounded_text(
        value.get("skillMarkdown"), maximum=_MAX_SKILL_MARKDOWN, optional=True
    )
    selectable = (
        asset_class == "skill"
        and library_action in {"create", "extend"}
        and bool(skill_name and skill_description and skill_markdown)
    )
    if asset_class == "skill" and not selectable:
        raise SkillAssetLedgerError("Skill proposal is missing its draft contract")
    if asset_class != "skill" and any((skill_name, skill_description, skill_markdown)):
        raise SkillAssetLedgerError("non-Skill asset contains a Skill draft")
    human_override = value.get("humanOverride", False)
    if type(human_override) is not bool:
        raise SkillAssetLedgerError("invalid human override marker")
    human_override_from = value.get("humanOverrideFrom")
    if human_override:
        if human_override_from != "lesson":
            raise SkillAssetLedgerError("invalid human override origin")
    elif human_override_from is not None:
        raise SkillAssetLedgerError("unexpected human override origin")

    return {
        "reviewId": review_id,
        "candidateId": candidate_id,
        "assetClass": asset_class,
        "originalDecision": original_decision,
        "disposition": _bounded_text(value.get("disposition"), maximum=120),
        "title": _bounded_text(value.get("title"), maximum=500),
        "summary": _bounded_text(value.get("summary"), optional=True),
        "reason": _bounded_text(value.get("reason"), optional=True),
        "scores": _score_projection(value.get("scores")),
        "completion": completion,
        "completionReason": _bounded_text(value.get("completionReason"), optional=True),
        "portfolioReason": _bounded_text(value.get("portfolioReason"), optional=True),
        "libraryAction": library_action,
        "existingSkillName": _bounded_text(value.get("existingSkillName"), maximum=128, optional=True),
        "evidenceCount": len(evidence_records),
        "skillName": skill_name,
        "skillDescription": skill_description,
        "skillMarkdown": skill_markdown,
        "selectedByDefault": selectable,
        "humanOverride": human_override,
        "humanOverrideFrom": human_override_from,
    }


def _selected_ledger(
    paths: RuntimePaths,
    business_date: str | None,
    prompt_version: str | None = None,
) -> tuple[str, str, str]:
    candidates = _ledger_candidates(paths, business_date)
    if prompt_version is not None:
        candidates = [item for item in candidates if item[2] == prompt_version]
    if not candidates:
        raise SkillAssetActionError(
            "The selected Skill asset ledger is unavailable.",
            code="skill-ledger-unavailable",
            status_code=404,
        )
    selected_date, _version_number, selected_prompt, filename = candidates[0]
    return selected_date, selected_prompt, filename


def _decode_private_rows(raw: bytes) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillAssetLedgerError("Skill asset ledger is not UTF-8") from exc
    lines = text.splitlines()
    if len(lines) > _MAX_LEDGER_ROWS or any(not line.strip() for line in lines):
        raise SkillAssetLedgerError("invalid Skill asset ledger row count")
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line, object_pairs_hook=_no_duplicate_object)
        except (json.JSONDecodeError, SkillAssetLedgerError) as exc:
            raise SkillAssetLedgerError("malformed Skill asset ledger") from exc
        if not isinstance(value, dict):
            raise SkillAssetLedgerError("ledger row must be an object")
        rows.append(value)
    return rows


def _ledger_directory(paths: RuntimePaths) -> Path:
    return paths.home / "artifacts" / "skills"


def _ledger_candidates(paths: RuntimePaths, business_date: str | None) -> list[tuple[str, int, str, str]]:
    root = _ledger_directory(paths)
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return []
    if root.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise SkillAssetLedgerError("unsafe Skill asset directory")
    candidates = []
    try:
        names = os.listdir(root)
    except OSError as exc:
        raise SkillAssetLedgerError("Skill asset directory is unavailable") from exc
    for name in names:
        match = _LEDGER_FILE_RE.fullmatch(name)
        if not match or (business_date and match.group("date") != business_date):
            continue
        try:
            date.fromisoformat(match.group("date"))
        except ValueError:
            continue
        prompt_version = match.group("prompt")
        candidates.append((match.group("date"), int(prompt_version.rsplit("v", 1)[1]), prompt_version, name))
    return sorted(candidates, reverse=True)


def _read_ledger_bytes(root: Path, filename: str) -> bytes:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(root, directory_flags | nofollow)
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow
        fd = os.open(filename, flags, dir_fd=directory_fd)
        try:
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or metadata.st_mode & 0o077
                or metadata.st_size > _MAX_LEDGER_BYTES
            ):
                raise SkillAssetLedgerError("unsafe Skill asset ledger")
            chunks: list[bytes] = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(fd, min(remaining, 131_072))
                if not chunk:
                    raise SkillAssetLedgerError("Skill asset ledger changed while reading")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(fd, 1):
                raise SkillAssetLedgerError("Skill asset ledger grew while reading")
            after = os.fstat(fd)
            if (
                (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            ):
                raise SkillAssetLedgerError("Skill asset ledger changed while reading")
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)


def get_skill_asset_review(
    business_date: str | None = None,
    *,
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    """Return the latest safe public review projection for one business day."""
    if business_date:
        try:
            date.fromisoformat(business_date)
        except (TypeError, ValueError) as exc:
            raise SkillAssetLedgerError("businessDate must be YYYY-MM-DD") from exc
    selected_paths = paths or load_paths()
    candidates = _ledger_candidates(selected_paths, business_date)
    if not candidates:
        return {
            "status": "empty",
            "businessDate": business_date,
            "promptVersion": None,
            "counts": {key: 0 for key in ("skill", "lesson", "reference", "discard")},
            "items": [],
        }
    selected_date, _version_number, prompt_version, filename = candidates[0]
    try:
        text = _read_ledger_bytes(_ledger_directory(selected_paths), filename).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillAssetLedgerError("Skill asset ledger is not UTF-8") from exc
    lines = text.splitlines()
    if len(lines) > _MAX_LEDGER_ROWS or any(not line.strip() for line in lines):
        raise SkillAssetLedgerError("invalid Skill asset ledger row count")
    items = []
    seen_review_ids: set[str] = set()
    for line in lines:
        try:
            value = json.loads(line, object_pairs_hook=_no_duplicate_object)
        except (json.JSONDecodeError, SkillAssetLedgerError) as exc:
            raise SkillAssetLedgerError("malformed Skill asset ledger") from exc
        item = _project_row(value, business_date=selected_date, prompt_version=prompt_version)
        review_id = str(item["reviewId"])
        if review_id in seen_review_ids:
            raise SkillAssetLedgerError("duplicate review identifier")
        seen_review_ids.add(review_id)
        items.append(item)
    counts = Counter(str(item["assetClass"]) for item in items)
    return {
        "status": "ready",
        "businessDate": selected_date,
        "promptVersion": prompt_version,
        "counts": {key: counts.get(key, 0) for key in ("skill", "lesson", "reference", "discard")},
        "items": items,
    }


def force_crystallize_lessons(
    payload: Any,
    *,
    paths: RuntimePaths | None = None,
    llm_call: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Crystallize explicitly selected Lessons without granting registration authority."""

    if not isinstance(payload, dict):
        raise SkillAssetActionError(
            "A Skill crystallization request must be an object.",
            code="skill-crystallization-invalid",
            status_code=400,
        )
    business_date = str(payload.get("businessDate") or "").strip()
    prompt_version = str(payload.get("promptVersion") or "").strip()
    review_ids = payload.get("reviewIds")
    try:
        date.fromisoformat(business_date)
    except ValueError as exc:
        raise SkillAssetActionError(
            "businessDate must be YYYY-MM-DD.",
            code="skill-crystallization-invalid",
            status_code=400,
        ) from exc
    if re.fullmatch(r"minimal-v\d+", prompt_version) is None:
        raise SkillAssetActionError(
            "promptVersion is invalid.",
            code="skill-crystallization-invalid",
            status_code=400,
        )
    if (
        not isinstance(review_ids, list)
        or not 1 <= len(review_ids) <= _MAX_FORCED_LESSONS
        or any(not isinstance(item, str) or _REVIEW_ID_RE.fullmatch(item) is None for item in review_ids)
        or len(set(review_ids)) != len(review_ids)
    ):
        raise SkillAssetActionError(
            "Select between one and eight unique Lesson review IDs.",
            code="skill-crystallization-invalid",
            status_code=400,
        )

    selected_paths = paths or load_paths()
    with _FORCE_LOCK:
        selected_date, selected_prompt, filename = _selected_ledger(
            selected_paths,
            business_date,
            prompt_version,
        )
        root = _ledger_directory(selected_paths)
        original = _read_ledger_bytes(root, filename)
        rows = _decode_private_rows(original)
        rows_by_review: dict[str, dict[str, Any]] = {}
        for row in rows:
            projected = _project_row(
                row,
                business_date=selected_date,
                prompt_version=selected_prompt,
            )
            review_id = str(projected["reviewId"])
            if review_id in rows_by_review:
                raise SkillAssetLedgerError("duplicate review identifier")
            rows_by_review[review_id] = row
        selected_rows = [rows_by_review.get(review_id) for review_id in review_ids]
        if any(row is None or row.get("assetClass") != "lesson" for row in selected_rows):
            raise SkillAssetActionError(
                "Only current Lesson rows may use forced crystallization.",
                code="skill-crystallization-not-lesson",
                status_code=409,
            )

        from data_foundation.settings import resolve_llm_provider
        from diary_generator import skill_pass_minimal_harness as harness
        from diary_generator import skill_pass_minimal_support as support

        if selected_prompt != harness.PROMPT_VERSION:
            raise SkillAssetActionError(
                "This historical prompt version cannot be crystallized by the current writer.",
                code="skill-crystallization-version-mismatch",
                status_code=409,
            )
        entries = support.load_filtered_stream(selected_paths, selected_date)
        stream = support.render_filtered_stream(entries)
        if not stream:
            raise SkillAssetActionError(
                "The evidence stream for this Lesson is unavailable.",
                code="skill-crystallization-evidence-unavailable",
                status_code=409,
            )
        discovery_name = (
            f"skill-harness-{selected_prompt}-raw-discovery-{selected_date}.md"
        )
        adjudication_name = (
            f"skill-harness-{selected_prompt}-raw-adjudication-{selected_date}.md"
        )
        discovery_raw = _read_ledger_bytes(root, discovery_name).decode("utf-8")
        adjudication_raw = _read_ledger_bytes(root, adjudication_name).decode("utf-8")
        discoveries, _rejected_discoveries = harness.parse_discovery_output(
            discovery_raw,
            stream=stream,
        )
        adjudication_stream = support.select_cited_records(stream, discoveries)
        decisions, _rejected_decisions = harness.parse_adjudication_output(
            adjudication_raw,
            stream=adjudication_stream,
            candidates=discoveries,
        )
        decision_by_review = {item.review_id: item for item in decisions}
        selected_decisions = [
            decision_by_review.get(review_id) for review_id in review_ids
        ]
        if any(
            item is None or not item.action_records or not item.verification_records
            for item in selected_decisions
        ):
            raise SkillAssetActionError(
                "At least one Lesson lacks grounded action and verification records.",
                code="skill-crystallization-insufficient-procedure",
                status_code=409,
            )
        forced_decisions = [item for item in selected_decisions if item is not None]
        source_candidates = [
            support.DiscoveryCandidate(
                title=item.title,
                goal="",
                obstacle="",
                effective_turn="",
                observed_result="",
                reuse_hypothesis="",
                evidence=item.evidence,
                record_ids=item.evidence_records,
                summary=item.reason,
            )
            for item in forced_decisions
        ]
        crystallization_stream = support.select_cited_records(stream, source_candidates)
        library_assets = harness.load_existing_skill_library(selected_paths)
        selected_matches = harness.select_library_matches(
            forced_decisions,
            library_assets,
            evidence_stream=crystallization_stream,
        )
        library_text, effective_matches = harness.prepare_library_prompt(selected_matches)
        prompt = harness.build_crystallization_prompt(
            forced_decisions,
            evidence_stream=crystallization_stream,
            library_text=library_text,
            library_matches=effective_matches,
            forced_review_ids=review_ids,
        )
        provider = resolve_llm_provider(selected_paths, redact_secrets=True)
        gate = support.single_call_gate_tokens(
            int(provider.get("pipelineGateTokens") or 30000)
        )
        if support.token_count(prompt) > gate:
            raise SkillAssetActionError(
                "The selected Lesson evidence exceeds the crystallization gate.",
                code="skill-crystallization-gate-exceeded",
                status_code=409,
            )
        invoke = llm_call or harness.call_harness_llm
        raw_output = invoke(
            prompt,
            system=harness.HUMAN_OVERRIDE_CRYSTALLIZATION_SYSTEM,
            stage="human-override-crystallization",
            source=crystallization_stream,
            paths=selected_paths,
        )
        safe_output = support.redact_discovery_output(raw_output)
        output_path = harness.raw_output_path(
            selected_paths,
            selected_date,
            "human-override-crystallization",
        )
        support._persist_raw(output_path, safe_output)
        proposals, _rejected = harness.parse_crystallization_proposals(
            safe_output,
            stream=crystallization_stream,
            decisions=forced_decisions,
            library_matches=effective_matches,
            forced_review_ids=review_ids,
        )
        proposals_by_review = {item.review_id: item for item in proposals}
        crystallized: list[str] = []
        not_crystallized: list[dict[str, str]] = []
        for review_id in review_ids:
            proposal = proposals_by_review.get(review_id)
            if (
                proposal is None
                or proposal.action not in {"create", "extend"}
                or proposal.skill is None
            ):
                not_crystallized.append(
                    {
                        "reviewId": review_id,
                        "action": proposal.action if proposal is not None else "invalid-output",
                    }
                )
                continue
            row = rows_by_review[review_id]
            row.update(
                {
                    "assetClass": "skill",
                    "disposition": f"human-override-{proposal.action}",
                    "libraryAction": proposal.action,
                    "existingAssetId": proposal.existing_asset_id,
                    "existingSkillName": proposal.existing_skill_name,
                    "skillName": proposal.skill.name,
                    "skillDescription": proposal.skill.description,
                    "skillMarkdown": proposal.skill.markdown(),
                    "humanOverride": True,
                    "humanOverrideFrom": "lesson",
                    "humanOverrideAt": datetime.now(timezone.utc).isoformat(),
                }
            )
            crystallized.append(review_id)

        if crystallized:
            current = _read_ledger_bytes(root, filename)
            if current != original:
                raise SkillAssetActionError(
                    "The Skill asset ledger changed during crystallization; retry the review.",
                    code="skill-crystallization-ledger-changed",
                    status_code=409,
                )
            rendered = "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            )
            support._write_text_atomic(root / filename, rendered)
            os.chmod(root / filename, 0o600)

        return {
            "status": "ready",
            "crystallizedReviewIds": crystallized,
            "notCrystallized": not_crystallized,
            "registrationRequested": False,
            "review": get_skill_asset_review(selected_date, paths=selected_paths),
        }

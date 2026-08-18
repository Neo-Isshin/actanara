"""Safe nova-RAG projection for non-Skill procedural assets.

Skill Pass keeps its full private review ledger under ``artifacts/skills``.
This module reads only the newest prompt-version ledger for each business day
and projects Lesson, Reference, and Discard rows into bounded retrieval text.
Skill drafts are deliberately excluded because they are distributed through
the Skill library instead of nova-RAG.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .paths import RuntimePaths


LEDGER_SCHEMA = "actanara.skill-asset-ledger.v1"
LEDGER_FILE_RE = re.compile(
    r"^skill-harness-(?P<prompt>minimal-v(?P<version>\d+))-assets-"
    r"(?P<date>\d{4}-\d{2}-\d{2})\.jsonl$"
)
REVIEW_ID_RE = re.compile(r"^review-\d{3}$")
RETRIEVAL_CLASSES = frozenset({"lesson", "reference", "discard"})
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_LEDGER_ROWS = 400
MAX_TEXT_CHARS = 8_000
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|private[_-]?key|"
    r"authorization|cookie)(\s*[:=]\s*)([^\s,;]+)"
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


@dataclass(frozen=True)
class SkillAssetMemoryRecord:
    record_id: str
    business_date: str
    prompt_version: str
    asset_class: str
    disposition: str
    review_id: str
    title: str
    text: str
    source_path: Path
    line_number: int


def collect_skill_asset_memory_records(
    paths: RuntimePaths,
) -> tuple[tuple[SkillAssetMemoryRecord, ...], tuple[Path, ...]]:
    """Return bounded current non-Skill assets and their source ledgers."""

    root = paths.home / "artifacts" / "skills"
    selected = _latest_ledgers(root)
    records: list[SkillAssetMemoryRecord] = []
    sources: list[Path] = []
    for business_date, prompt_version, filename in selected:
        raw = _read_safe_ledger(root, filename)
        if raw is None:
            continue
        decoded = _decode_rows(raw)
        if decoded is None:
            continue
        file_records: list[SkillAssetMemoryRecord] = []
        seen_review_ids: set[str] = set()
        valid = True
        for line_number, row in enumerate(decoded, 1):
            projected = _project_row(
                row,
                business_date=business_date,
                prompt_version=prompt_version,
                source_path=root / filename,
                line_number=line_number,
            )
            if projected is None:
                if row.get("assetClass") == "skill":
                    continue
                valid = False
                break
            if projected.review_id in seen_review_ids:
                valid = False
                break
            seen_review_ids.add(projected.review_id)
            file_records.append(projected)
        if not valid:
            continue
        records.extend(file_records)
        sources.append(root / filename)
    return tuple(records), tuple(sources)


def skill_asset_ledger_ready(paths: RuntimePaths, business_date: str) -> bool:
    """Return whether a safe, context-bound current ledger exists for a day."""

    root = paths.home / "artifacts" / "skills"
    selected = [item for item in _latest_ledgers(root) if item[0] == business_date]
    if not selected:
        return False
    selected_date, prompt_version, filename = selected[0]
    raw = _read_safe_ledger(root, filename)
    rows = _decode_rows(raw) if raw is not None else None
    if rows is None:
        return False
    return all(
        row.get("schema") == LEDGER_SCHEMA
        and row.get("businessDate") == selected_date
        and row.get("promptVersion") == prompt_version
        and row.get("assetClass") in {"skill", *RETRIEVAL_CLASSES}
        for row in rows
    )


def _latest_ledgers(root: Path) -> tuple[tuple[str, str, str], ...]:
    try:
        metadata = root.lstat()
    except (FileNotFoundError, OSError):
        return ()
    if (
        root.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
    ):
        return ()
    newest: dict[str, tuple[int, str, str]] = {}
    try:
        names = os.listdir(root)
    except OSError:
        return ()
    for name in names:
        match = LEDGER_FILE_RE.fullmatch(name)
        if match is None:
            continue
        business_date = match.group("date")
        try:
            date.fromisoformat(business_date)
        except ValueError:
            continue
        candidate = (int(match.group("version")), match.group("prompt"), name)
        if candidate > newest.get(business_date, (-1, "", "")):
            newest[business_date] = candidate
    return tuple(
        (business_date, prompt_version, filename)
        for business_date, (_version, prompt_version, filename) in sorted(newest.items())
    )


def _read_safe_ledger(root: Path, filename: str) -> bytes | None:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(root, directory_flags | nofollow)
    except OSError:
        return None
    try:
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow,
                dir_fd=directory_fd,
            )
        except OSError:
            return None
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or before.st_mode & 0o077
                or before.st_size > MAX_LEDGER_BYTES
            ):
                return None
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 131_072))
                if not chunk:
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                return None
            after = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                return None
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_fd)


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _decode_rows(raw: bytes) -> list[dict[str, Any]] | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    if len(lines) > MAX_LEDGER_ROWS or any(not line.strip() for line in lines):
        return None
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line, object_pairs_hook=_no_duplicate_object)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(row, dict):
            return None
        rows.append(row)
    return rows


def _text(value: Any, *, required: bool = False, maximum: int = MAX_TEXT_CHARS) -> str | None:
    if not isinstance(value, str):
        return None if not required and value is None else None
    selected = value.strip()
    if (required and not selected) or len(selected) > maximum or "\x00" in selected:
        return None
    return selected


def _project_row(
    row: dict[str, Any],
    *,
    business_date: str,
    prompt_version: str,
    source_path: Path,
    line_number: int,
) -> SkillAssetMemoryRecord | None:
    if (
        row.get("schema") != LEDGER_SCHEMA
        or row.get("businessDate") != business_date
        or row.get("promptVersion") != prompt_version
    ):
        return None
    asset_class = row.get("assetClass")
    if asset_class not in RETRIEVAL_CLASSES:
        return None
    review_id = _text(row.get("reviewId"), required=True, maximum=32)
    title = _text(row.get("title"), required=True, maximum=500)
    disposition = _text(row.get("disposition"), required=True, maximum=120)
    if (
        row.get("summary") is not None
        and not isinstance(row.get("summary"), str)
    ) or (
        row.get("reason") is not None
        and not isinstance(row.get("reason"), str)
    ):
        return None
    summary = _text(row.get("summary"), maximum=MAX_TEXT_CHARS)
    reason = _text(row.get("reason"), maximum=MAX_TEXT_CHARS)
    if (
        review_id is None
        or REVIEW_ID_RE.fullmatch(review_id) is None
        or title is None
        or disposition is None
        or not (summary or reason)
    ):
        return None
    title = _redact_retrieval_text(title)
    summary = _redact_retrieval_text(summary or "")
    reason = _redact_retrieval_text(reason or "")
    sections = [title, f"Asset class: {asset_class}"]
    if summary:
        sections.append(f"Experience: {summary}")
    if reason:
        sections.append(f"Judgment: {reason}")
    text = "\n".join(sections)
    identity = f"{business_date}|{prompt_version}|{review_id}|{asset_class}"
    record_id = "skill-asset-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return SkillAssetMemoryRecord(
        record_id=record_id,
        business_date=business_date,
        prompt_version=prompt_version,
        asset_class=asset_class,
        disposition=disposition,
        review_id=review_id,
        title=title,
        text=text,
        source_path=source_path,
        line_number=line_number,
    )


def _redact_retrieval_text(value: str) -> str:
    text = BEARER_RE.sub("Bearer [redacted]", str(value or ""))
    return SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[redacted]",
        text,
    )

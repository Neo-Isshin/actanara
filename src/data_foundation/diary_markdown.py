"""Structured Foundation ingestion for generated diary Markdown artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from .db import connect
from .diary_paths import diary_report_paths, diary_report_type_for_filename
from .paths import RuntimePaths
from .settings import ensure_settings

DIARY_MARKDOWN_PROJECTION = "diary-markdown-structured-v1"
DIARY_PERIOD_PAGE_PROJECTION = "diary-period-page-v1"
_DIARY_MARKDOWN_STALE_REASON = "absent-from-authoritative-source-inventory"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_TRAILING_JSON_FENCE_RE = re.compile(r"\n```json\s*\n(?P<json>.*?)\n```\s*$", re.DOTALL)
_LESSON_RE = re.compile(r"^-\s+\*\*【([^】]+)】\*\*[：:]\s*(.+)$")
_SUMMARY_HEADINGS = {"今日概要", "Daily Overview"}
_LESSON_ROOT_HEADINGS = {"黄金教训", "Lessons"}
_LESSON_FIELD_HEADINGS = {
    "问题": "problem",
    "Problem": "problem",
    "根因": "rootCause",
    "Root Cause": "rootCause",
    "建议": "suggestion",
    "Recommendation": "suggestion",
}


@dataclass(frozen=True)
class MarkdownSection:
    ordinal: int
    heading_level: int
    heading: str
    heading_path: tuple[str, ...]
    body_markdown: str


@dataclass(frozen=True)
class ParsedDiaryMarkdown:
    title: str | None
    sections: tuple[MarkdownSection, ...]
    embedded_json: dict | None
    content_without_embedded_json: str


@dataclass(frozen=True)
class _PreparedDiaryMarkdown:
    document_key: str
    business_date: date
    report_type: str
    relative_path: str
    parsed: ParsedDiaryMarkdown
    content_sha256: str
    byte_size: int
    modified_at: str


def _report_type_for(path: Path) -> str:
    return diary_report_type_for_filename(path.name, language_profile="mixed")


def _pipeline_language_profile(paths: RuntimePaths) -> str:
    settings = ensure_settings(paths)
    pipeline = settings.get("pipeline") if isinstance(settings.get("pipeline"), dict) else {}
    value = str(pipeline.get("languageProfile") or "zh").lower()
    return "en" if value.startswith("en") else "zh"


def _active_diary_markdown_paths(root: Path, business_date: date, *, language_profile: str) -> tuple[Path, ...]:
    selected: list[Path] = []
    seen: set[Path] = set()
    for report_type in ("narrative", "technical", "learning"):
        for path in diary_report_paths(root, business_date, report_type, language_profile=language_profile):
            if path not in seen:
                selected.append(path)
                seen.add(path)
    return tuple(selected)


def _authoritative_diary_markdown_paths(
    root: Path,
    business_date: date,
    *,
    language_profile: str,
    markdown_paths: Iterable[Path] | None = None,
) -> tuple[Path, ...]:
    if not root.is_dir():
        raise FileNotFoundError(f"generated diary root is unavailable for {business_date.isoformat()}")
    candidates = (
        tuple(markdown_paths)
        if markdown_paths is not None
        else _active_diary_markdown_paths(root, business_date, language_profile=language_profile)
    )
    selected: list[Path] = []
    seen_paths: set[Path] = set()
    by_report_type: dict[str, Path] = {}
    for path in candidates:
        relative_path = path.relative_to(root).as_posix()
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if not path.is_file():
            raise FileNotFoundError(f"diary Markdown source disappeared during inventory: {relative_path}")
        report_type = diary_report_type_for_filename(path.name, language_profile=language_profile)
        if report_type not in {"narrative", "technical", "learning"}:
            raise ValueError(f"diary Markdown source is outside the active {language_profile} profile: {relative_path}")
        previous = by_report_type.get(report_type)
        if previous is not None:
            previous_relative = previous.relative_to(root).as_posix()
            raise ValueError(
                "duplicate diary Markdown sources for "
                f"{business_date.isoformat()} {language_profile} {report_type}: "
                f"{previous_relative}, {relative_path}"
            )
        by_report_type[report_type] = path
        selected.append(path)
    return tuple(selected)


def _document_key(business_date: date, report_type: str, relative_path: str) -> str:
    return f"{DIARY_MARKDOWN_PROJECTION}:{business_date.isoformat()}:{report_type}:{relative_path}"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _split_embedded_json(content: str) -> tuple[str, dict | None]:
    match = _TRAILING_JSON_FENCE_RE.search(content)
    if match is None:
        return content, None
    try:
        embedded = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return content, None
    return content[: match.start()].rstrip() + "\n", embedded if isinstance(embedded, dict) else None


def parse_diary_markdown(content: str) -> ParsedDiaryMarkdown:
    """Parse generated Markdown without changing its on-disk contract."""
    human_content, embedded_json = _split_embedded_json(content)
    matches = list(_HEADING_RE.finditer(human_content))
    title = None
    section_matches = matches
    if matches and matches[0].group(1) == "#" and human_content[: matches[0].start()].strip() == "":
        title = matches[0].group(2).strip()
        section_matches = matches[1:]

    sections: list[MarkdownSection] = []
    heading_stack: list[tuple[int, str]] = []
    for index, match in enumerate(section_matches):
        level = len(match.group(1))
        heading = match.group(2).strip()
        next_start = section_matches[index + 1].start() if index + 1 < len(section_matches) else len(human_content)
        body = human_content[match.end() : next_start].strip()
        heading_stack = [(item_level, item_heading) for item_level, item_heading in heading_stack if item_level < level]
        heading_stack.append((level, heading))
        sections.append(
            MarkdownSection(
                ordinal=len(sections),
                heading_level=level,
                heading=heading,
                heading_path=tuple(item_heading for _, item_heading in heading_stack),
                body_markdown=body,
            )
        )

    return ParsedDiaryMarkdown(
        title=title,
        sections=tuple(sections),
        embedded_json=embedded_json,
        content_without_embedded_json=human_content,
    )


def _prepare_diary_markdown_document(root: Path, markdown_path: Path, business_date: date) -> _PreparedDiaryMarkdown:
    relative_path = markdown_path.relative_to(root).as_posix()
    report_type = _report_type_for(markdown_path)
    stat_before = markdown_path.stat()
    content = markdown_path.read_text(encoding="utf-8")
    parsed = parse_diary_markdown(content)
    stat_after = markdown_path.stat()
    before_signature = (stat_before.st_dev, stat_before.st_ino, stat_before.st_size, stat_before.st_mtime_ns)
    after_signature = (stat_after.st_dev, stat_after.st_ino, stat_after.st_size, stat_after.st_mtime_ns)
    if before_signature != after_signature:
        raise RuntimeError(f"diary Markdown source changed during inventory: {relative_path}")
    document_key = _document_key(business_date, report_type, relative_path)
    return _PreparedDiaryMarkdown(
        document_key=document_key,
        business_date=business_date,
        report_type=report_type,
        relative_path=relative_path,
        parsed=parsed,
        content_sha256=_content_hash(content),
        byte_size=stat_after.st_size,
        modified_at=datetime.fromtimestamp(stat_after.st_mtime).astimezone().isoformat(),
    )


def _upsert_diary_markdown_document(
    connection,
    document: _PreparedDiaryMarkdown,
    *,
    source_run_id: int | None,
    status: str,
) -> None:
    connection.execute("DELETE FROM diary_markdown_sections WHERE document_key = ?", (document.document_key,))
    connection.execute(
        """
        INSERT INTO diary_markdown_documents(
            document_key, business_date, report_type, relative_path, title,
            embedded_json, content_sha256, byte_size, modified_at, parsed_at,
            source_run_id, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_key) DO UPDATE SET
            business_date=excluded.business_date,
            report_type=excluded.report_type,
            relative_path=excluded.relative_path,
            title=excluded.title,
            embedded_json=excluded.embedded_json,
            content_sha256=excluded.content_sha256,
            byte_size=excluded.byte_size,
            modified_at=excluded.modified_at,
            parsed_at=excluded.parsed_at,
            source_run_id=excluded.source_run_id,
            status=excluded.status
        """,
        (
            document.document_key,
            document.business_date.isoformat(),
            document.report_type,
            document.relative_path,
            document.parsed.title,
            json.dumps(document.parsed.embedded_json, ensure_ascii=False, sort_keys=True)
            if document.parsed.embedded_json is not None
            else None,
            document.content_sha256,
            document.byte_size,
            document.modified_at,
            datetime.now().astimezone().isoformat(),
            source_run_id,
            status,
        ),
    )
    connection.executemany(
        """
        INSERT INTO diary_markdown_sections(
            document_key, ordinal, heading_level, heading, heading_path_json, body_markdown
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                document.document_key,
                section.ordinal,
                section.heading_level,
                section.heading,
                json.dumps(list(section.heading_path), ensure_ascii=False),
                section.body_markdown,
            )
            for section in document.parsed.sections
        ],
    )


def _record_diary_reconciliation_metadata(
    connection,
    source_run_id: int | None,
    *,
    business_date: date,
    language_profile: str,
    ready_count: int,
    stale_count: int,
    reactivated_count: int,
) -> None:
    if source_run_id is None:
        return
    row = connection.execute(
        "SELECT adapter_versions_json FROM ingestion_runs WHERE id = ?",
        (source_run_id,),
    ).fetchone()
    if row is None:
        return
    try:
        metadata = json.loads(row["adapter_versions_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    current = metadata.get("diaryMarkdownReconciliation")
    if not isinstance(current, dict):
        current = {}
    metadata["diaryMarkdownReconciliation"] = {
        "contract": "ready-stale-v1",
        "staleReason": _DIARY_MARKDOWN_STALE_REASON,
        "lastBusinessDate": business_date.isoformat(),
        "lastLanguageProfile": language_profile,
        "daysScanned": int(current.get("daysScanned") or 0) + 1,
        "readyDocuments": int(current.get("readyDocuments") or 0) + ready_count,
        "staledDocuments": int(current.get("staledDocuments") or 0) + stale_count,
        "reactivatedDocuments": int(current.get("reactivatedDocuments") or 0) + reactivated_count,
    }
    connection.execute(
        "UPDATE ingestion_runs SET adapter_versions_json = ? WHERE id = ?",
        (json.dumps(metadata, sort_keys=True), source_run_id),
    )


def write_diary_markdown_document(
    paths: RuntimePaths,
    markdown_path: Path,
    business_date: date,
    *,
    source_run_id: int | None,
    diary_root: Path | None = None,
    status: str = "ready",
) -> str:
    root = diary_root or paths.diary_dir
    document = _prepare_diary_markdown_document(root, markdown_path, business_date)
    with connect(paths) as connection:
        _upsert_diary_markdown_document(connection, document, source_run_id=source_run_id, status=status)
    return document.document_key


def materialize_diary_markdown_day(
    paths: RuntimePaths,
    business_date: date,
    *,
    source_run_id: int | None,
    diary_root: Path | None = None,
    markdown_paths: Iterable[Path] | None = None,
) -> dict:
    root = diary_root or paths.diary_dir
    language_profile = _pipeline_language_profile(paths)
    selected_paths = _authoritative_diary_markdown_paths(
        root,
        business_date,
        language_profile=language_profile,
        markdown_paths=markdown_paths,
    )
    documents = tuple(_prepare_diary_markdown_document(root, path, business_date) for path in selected_paths)
    keys = [document.document_key for document in documents]
    with connect(paths) as connection:
        existing_rows = connection.execute(
            "SELECT document_key, status FROM diary_markdown_documents WHERE business_date = ?",
            (business_date.isoformat(),),
        ).fetchall()
        existing_status = {str(row["document_key"]): str(row["status"]) for row in existing_rows}
        stale_keys = [
            document_key
            for document_key, status in existing_status.items()
            if status == "ready" and document_key not in keys
        ]
        reactivated_keys = [document_key for document_key in keys if existing_status.get(document_key) == "stale"]
        for document in documents:
            _upsert_diary_markdown_document(
                connection,
                document,
                source_run_id=source_run_id,
                status="ready",
            )
        if keys:
            placeholders = ",".join("?" for _ in keys)
            connection.execute(
                f"""
                UPDATE diary_markdown_documents
                SET status = 'stale'
                WHERE business_date = ? AND status = 'ready'
                  AND document_key NOT IN ({placeholders})
                """,
                [business_date.isoformat(), *keys],
            )
        else:
            connection.execute(
                "UPDATE diary_markdown_documents SET status = 'stale' WHERE business_date = ? AND status = 'ready'",
                (business_date.isoformat(),),
            )
        _record_diary_reconciliation_metadata(
            connection,
            source_run_id,
            business_date=business_date,
            language_profile=language_profile,
            ready_count=len(keys),
            stale_count=len(stale_keys),
            reactivated_count=len(reactivated_keys),
        )
    return {"businessDate": business_date.isoformat(), "documents": len(keys), "documentKeys": keys}


def materialize_diary_markdown_period_documents(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    source_run_id: int | None,
    diary_root: Path | None = None,
) -> dict:
    keys: list[str] = []
    current = start_date
    while current <= end_date:
        result = materialize_diary_markdown_day(
            paths,
            current,
            source_run_id=source_run_id,
            diary_root=diary_root,
        )
        keys.extend(result["documentKeys"])
        current += timedelta(days=1)
    return {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "documents": len(keys),
        "documentKeys": keys,
    }


def _document_matches_language_profile(document, language_profile: str) -> bool:
    relative_path = str(document["relative_path"] or "")
    report_type = diary_report_type_for_filename(Path(relative_path).name, language_profile=language_profile)
    return report_type == str(document["report_type"] or "")


def read_diary_markdown_document(paths: RuntimePaths, document_key: str) -> dict | None:
    language_profile = _pipeline_language_profile(paths)
    with connect(paths, read_only=True) as connection:
        document = connection.execute(
            """
            SELECT document_key, business_date, report_type, relative_path, title,
                   embedded_json, content_sha256, byte_size, modified_at, parsed_at,
                   source_run_id, status
            FROM diary_markdown_documents
            WHERE document_key = ? AND status = 'ready'
            """,
            (document_key,),
        ).fetchone()
        if document is None or not _document_matches_language_profile(document, language_profile):
            return None
        sections = connection.execute(
            """
            SELECT ordinal, heading_level, heading, heading_path_json, body_markdown
            FROM diary_markdown_sections
            WHERE document_key = ?
            ORDER BY ordinal
            """,
            (document_key,),
        ).fetchall()
    result = dict(document)
    result["embeddedJson"] = json.loads(result.pop("embedded_json")) if result["embedded_json"] else None
    result["sections"] = [
        {
            "ordinal": row["ordinal"],
            "headingLevel": row["heading_level"],
            "heading": row["heading"],
            "headingPath": json.loads(row["heading_path_json"]),
            "bodyMarkdown": row["body_markdown"],
        }
        for row in sections
    ]
    return result


def read_diary_markdown_documents(paths: RuntimePaths, start_date: date, end_date: date) -> list[dict]:
    language_profile = _pipeline_language_profile(paths)
    with connect(paths, read_only=True) as connection:
        ready_documents = connection.execute(
            """
            SELECT document_key, business_date, report_type, relative_path, title,
                   embedded_json, content_sha256, byte_size, modified_at, parsed_at,
                   source_run_id, status
            FROM diary_markdown_documents
            WHERE business_date >= ? AND business_date <= ? AND status = 'ready'
            ORDER BY business_date, report_type, relative_path
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
        documents = [
            row for row in ready_documents if _document_matches_language_profile(row, language_profile)
        ]
        sections_by_document: dict[str, list[dict]] = {}
        if documents:
            placeholders = ",".join("?" for _ in documents)
            sections = connection.execute(
                f"""
                SELECT document_key, ordinal, heading_level, heading, heading_path_json, body_markdown
                FROM diary_markdown_sections
                WHERE document_key IN ({placeholders})
                ORDER BY document_key, ordinal
                """,
                [row["document_key"] for row in documents],
            ).fetchall()
            for row in sections:
                sections_by_document.setdefault(row["document_key"], []).append(
                    {
                        "ordinal": row["ordinal"],
                        "headingLevel": row["heading_level"],
                        "heading": row["heading"],
                        "headingPath": json.loads(row["heading_path_json"]),
                        "bodyMarkdown": row["body_markdown"],
                    }
                )
    result = []
    for row in documents:
        item = dict(row)
        item["embeddedJson"] = json.loads(item.pop("embedded_json")) if item["embedded_json"] else None
        item["sections"] = sections_by_document.get(item["document_key"], [])
        result.append(item)
    return result


def _markdown_list_items(body: str) -> list[str]:
    items = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        item = stripped[2:].strip()
        item = re.sub(r"^\*\*(.+?)\*\*[：:]\s*", r"\1: ", item)
        if item:
            items.append(item)
    return items


def _clean_markdown_item(value: str) -> str:
    item = value.strip()
    item = re.sub(r"^\*\*(.+?)\*\*[：:]\s*", r"\1: ", item)
    item = item.replace("**", "").strip()
    item = re.sub(r"^\[([^\]]+)\]([：:])", r"\1\2", item)
    return item


def _summary_topics_from_body(document: dict, body: str) -> list[dict]:
    topics = []
    current: dict | None = None
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        is_top_level_bullet = line == line.lstrip()
        if stripped.startswith("* ") or (is_top_level_bullet and re.match(r"^-\s+\*\*.+?\*\*[：:]", stripped)):
            if current is not None:
                topics.append(current)
            item = stripped[2:].strip()
            current = {
                "date": document["business_date"],
                "title": _clean_markdown_item(item),
                "items": [],
                "sourceDocumentKey": document["document_key"],
            }
            continue
        if stripped.startswith("- ") and current is not None:
            item = _clean_markdown_item(stripped[2:].strip())
            if item:
                current["items"].append(item)
            continue
    if current is not None:
        topics.append(current)
    return [topic for topic in topics if topic["title"]]


def _period_summary_topics(document: dict) -> list[dict]:
    topics = []
    for section in document["sections"]:
        heading_path = section["headingPath"]
        if not heading_path or heading_path[0] not in _SUMMARY_HEADINGS:
            continue
        if section["heading"] in _SUMMARY_HEADINGS:
            structured = _summary_topics_from_body(document, section["bodyMarkdown"])
            if structured:
                topics.extend(structured)
                continue
            for item in _markdown_list_items(section["bodyMarkdown"]):
                topics.append(
                    {
                        "date": document["business_date"],
                        "title": item,
                        "items": [],
                        "sourceDocumentKey": document["document_key"],
                    }
                )
            continue
        topics.append(
            {
                "date": document["business_date"],
                "title": _clean_markdown_item(section["heading"]),
                "items": [_clean_markdown_item(item) for item in _markdown_list_items(section["bodyMarkdown"])],
                "sourceDocumentKey": document["document_key"],
            }
        )
    return topics


def _period_lessons(document: dict) -> list[dict]:
    lessons = []
    current: dict | None = None

    def flush_current() -> None:
        nonlocal current
        if not current:
            return
        problem = current.get("problem") or current.get("title") or ""
        root_cause = current.get("rootCause") or ""
        suggestion = current.get("suggestion") or ""
        if problem or root_cause or suggestion:
            lessons.append(
                {
                    "date": document["business_date"],
                    "agent": current.get("agent") or "unknown",
                    "problem": problem,
                    "rootCause": root_cause,
                    "suggestion": suggestion,
                    "sourceDocumentKey": document["document_key"],
                }
            )
        current = None

    for section in document["sections"]:
        if not any(any(root in heading for root in _LESSON_ROOT_HEADINGS) for heading in section["headingPath"]):
            continue
        if section["headingLevel"] == 3:
            flush_current()
            title = section["heading"]
            match = re.match(r"^【([^】]+)】\s*(.*)$", title)
            if match is None:
                match = re.match(r"^\[([^\]]+)\]\s*(.*)$", title)
            current = {
                "agent": match.group(1).strip() if match else "unknown",
                "title": match.group(2).strip() if match else title,
            }
            continue
        if current is not None and section["headingLevel"] == 4:
            field = _LESSON_FIELD_HEADINGS.get(str(section["heading"]).strip())
            if field:
                current[field] = section["bodyMarkdown"].strip()
            continue
        for line in section["bodyMarkdown"].splitlines():
            match = _LESSON_RE.match(line.strip())
            if not match:
                continue
            rest = match.group(2).strip()
            sep_idx = rest.find("解决建议")
            if sep_idx >= 0:
                problem = rest[:sep_idx].rstrip("。：:")
                suggestion = rest[sep_idx + len("解决建议") :].lstrip("：: ")
            else:
                problem = rest
                suggestion = ""
            lessons.append(
                {
                    "date": document["business_date"],
                    "agent": match.group(1).strip(),
                    "problem": problem,
                    "suggestion": suggestion,
                    "sourceDocumentKey": document["document_key"],
                }
            )
    flush_current()
    return lessons


def build_diary_period_page_payload(paths: RuntimePaths, start_date: date, end_date: date) -> dict:
    documents = read_diary_markdown_documents(paths, start_date, end_date)
    days: dict[str, dict] = {
        (start_date + timedelta(days=offset)).isoformat(): {"date": (start_date + timedelta(days=offset)).isoformat(), "documents": []}
        for offset in range((end_date - start_date).days + 1)
    }
    summary_topics = []
    lessons = []
    section_count = 0
    for document in documents:
        compact = {
            "documentKey": document["document_key"],
            "reportType": document["report_type"],
            "relativePath": document["relative_path"],
            "title": document["title"],
            "contentSha256": document["content_sha256"],
            "sections": document["sections"],
        }
        days.setdefault(document["business_date"], {"date": document["business_date"], "documents": []})["documents"].append(compact)
        section_count += len(document["sections"])
        if document["report_type"] == "narrative":
            summary_topics.extend(_period_summary_topics(document))
        elif document["report_type"] == "learning":
            lessons.extend(_period_lessons(document))
    return {
        "projection": DIARY_PERIOD_PAGE_PROJECTION,
        "period": f"{start_date.isoformat()} ~ {end_date.isoformat()}",
        "documentProjection": DIARY_MARKDOWN_PROJECTION,
        "documentCount": len(documents),
        "sectionCount": section_count,
        "days": list(days.values()),
        "summaryTopics": summary_topics,
        "lessons": lessons,
    }


def materialize_diary_period_page_snapshot(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    source_run_id: int | None,
) -> str:
    from .reports import write_period_projection

    return write_period_projection(
        paths,
        start_date,
        end_date,
        build_diary_period_page_payload(paths, start_date, end_date),
        source_run_id=source_run_id,
        projection_type=DIARY_PERIOD_PAGE_PROJECTION,
    )

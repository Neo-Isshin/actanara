"""Read-only discovery and parsing for operator-configured nova-RAG sources.

Discovery never writes source files, settings, the legacy index, or the nova-RAG
store.  Candidate indexing may consume the returned chunks and source records;
that existing path is the only writer and remains restricted to the v2 store.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import html
import io
import json
import os
import re
import stat
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from xml.etree import ElementTree

from .rag_memory_governance import governance_for_source
from .rag_settings import ExternalSourceSettings, RagSettings, effective_indexing_source_sets, resolve_rag_settings


SOURCE_SET = "external-content"
MAX_CHUNK_CHARACTERS = 4_000
MAX_DOCX_EXPANSION_RATIO = 20
PARSER_VERSIONS = {
    ".md": "markdown-stdlib-v1",
    ".markdown": "markdown-stdlib-v1",
    ".txt": "text-stdlib-v1",
    ".log": "text-stdlib-v1",
    ".docx": "docx-ooxml-stdlib-v1",
    ".pdf": "pdf-pypdf-v1",
    ".html": "html-stdlib-v1",
    ".htm": "html-stdlib-v1",
    ".rtf": "rtf-stdlib-v1",
    ".csv": "csv-stdlib-v1",
    ".tsv": "tsv-stdlib-v1",
    ".json": "json-stdlib-v1",
    ".jsonl": "jsonl-stdlib-v1",
}
UNSUPPORTED_DOC_SUGGESTION = (
    "Legacy .doc files are not supported. Convert the file to .docx, PDF, Markdown, or plain text, then retry."
)
PDF_DEPENDENCY_SUGGESTION = "Install the declared nova-RAG PDF dependency: pypdf."


class ExternalSourceError(RuntimeError):
    """An expected, source-local discovery or parsing failure."""

    def __init__(self, code: str, message: str, *, suggestion: str | None = None):
        self.code = code
        self.suggestion = suggestion
        super().__init__(message)


@dataclass(frozen=True)
class _DiscoveredFile:
    path: Path
    read_path: Path
    root: Path
    boundary: Path
    relative_path: str
    stat_result: os.stat_result
    symlink: bool


@dataclass(frozen=True)
class _ParsedUnit:
    label: str
    text: str
    metadata: dict[str, Any]


def collect_external_source_chunks(settings: RagSettings) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse configured files and return candidate chunks plus parser-state records."""
    config = settings.external_sources
    if not config.enabled:
        return [], []

    discovered, records = _discover_files(config)
    active_sources, active_chunks = _load_active_external_cache(settings)
    chunks: list[dict[str, Any]] = []
    content_owner: dict[str, str] = {}
    total_bytes = 0

    for item in discovered:
        path = item.path
        extension = path.suffix.lower()
        source_id = _source_id(path)
        if extension == ".doc":
            records.append(
                _source_record(
                    item,
                    source_id=source_id,
                    parser_status="unsupported",
                    parser_error="unsupported-legacy-doc",
                    suggestion=UNSUPPORTED_DOC_SUGGESTION,
                )
            )
            continue
        parser_version = PARSER_VERSIONS.get(extension)
        if parser_version is None:
            records.append(
                _source_record(
                    item,
                    source_id=source_id,
                    parser_status="unsupported",
                    parser_error=f"unsupported-extension:{extension or 'none'}",
                    suggestion="Convert the file to a supported nova-RAG external source format.",
                )
            )
            continue
        if item.stat_result.st_size > config.max_file_bytes:
            records.append(
                _source_record(
                    item,
                    source_id=source_id,
                    parser_version=parser_version,
                    parser_status="skipped",
                    parser_error="file-too-large",
                    suggestion=f"Reduce the file below maxFileBytes={config.max_file_bytes} or raise the explicit limit.",
                )
            )
            continue
        if total_bytes + item.stat_result.st_size > config.max_total_bytes:
            records.append(
                _source_record(
                    item,
                    source_id=source_id,
                    parser_version=parser_version,
                    parser_status="skipped",
                    parser_error="total-size-limit-exceeded",
                    suggestion=f"Reduce the selected source set below maxTotalBytes={config.max_total_bytes}.",
                )
            )
            continue

        try:
            raw, opened_stat = _read_regular_file(item, config)
            content_hash = hashlib.sha256(raw).hexdigest()
            total_bytes += len(raw)
            duplicate_of = content_owner.get(content_hash)
            if duplicate_of:
                records.append(
                    _source_record(
                        item,
                        source_id=source_id,
                        opened_stat=opened_stat,
                        content_hash=content_hash,
                        parser_version=parser_version,
                        parser_status="duplicate",
                        duplicate_of=duplicate_of,
                    )
                )
                continue
            content_owner[content_hash] = source_id

            cached_source = active_sources.get(source_id)
            cached_chunks = active_chunks.get(source_id, [])
            if (
                cached_source
                and cached_chunks
                and cached_source.get("contentHash") == content_hash
                and cached_source.get("parserVersion") == parser_version
            ):
                chunks.extend(cached_chunks)
                records.append(
                    _source_record(
                        item,
                        source_id=source_id,
                        opened_stat=opened_stat,
                        content_hash=content_hash,
                        parser_version=parser_version,
                        parser_status="unchanged",
                        chunk_count=len(cached_chunks),
                        incremental=True,
                    )
                )
                continue

            units = _parse_document(extension, raw, maximum_bytes=config.max_file_bytes)
            document_chunks = _units_to_chunks(
                item,
                units,
                content_hash=content_hash,
                parser_version=parser_version,
                mode=config.mode,
            )
            chunks.extend(document_chunks)
            records.append(
                _source_record(
                    item,
                    source_id=source_id,
                    opened_stat=opened_stat,
                    content_hash=content_hash,
                    parser_version=parser_version,
                    parser_status="parsed",
                    chunk_count=len(document_chunks),
                    incremental=False,
                )
            )
        except ExternalSourceError as exc:
            records.append(
                _source_record(
                    item,
                    source_id=source_id,
                    parser_version=parser_version,
                    parser_status="error",
                    parser_error=f"{exc.code}:{exc}",
                    suggestion=exc.suggestion,
                )
            )
        except (OSError, ValueError, UnicodeError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            records.append(
                _source_record(
                    item,
                    source_id=source_id,
                    parser_version=parser_version,
                    parser_status="error",
                    parser_error=f"parse-error:{exc.__class__.__name__}:{exc}",
                )
            )

    deduped: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        deduped.setdefault(str(chunk.get("textHash") or chunk.get("id")), chunk)
    return list(deduped.values()), records


def plan_external_sources(settings: RagSettings | None = None) -> dict[str, Any]:
    """Return a read-only parse plan. No index/store/runtime files are written."""
    resolved = settings or resolve_rag_settings()
    config = resolved.external_sources
    chunks, records = collect_external_source_chunks(resolved)
    status_counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("parserStatus") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    blockers = []
    if not config.enabled:
        blockers.append({"code": "external-sources-disabled", "reason": "External sources are disabled by settings."})
    elif not config.paths:
        blockers.append({"code": "external-source-paths-empty", "reason": "No external source paths are configured."})
    parse_errors = [record for record in records if record.get("parserStatus") == "error"]
    blocking_records = [
        record
        for record in records
        if record.get("parserStatus") in {"error", "missing", "skipped", "unsupported"}
    ]
    if blocking_records:
        blockers.append(
            {
                "code": "external-source-parse-blocked",
                "reason": f"{len(blocking_records)} external source record(s) require operator attention.",
            }
        )
    return {
        "schemaVersion": 1,
        "action": "rag-external-sources-plan",
        "dryRun": True,
        "status": "plan",
        "canExecute": not blockers and not parse_errors,
        "mode": config.mode,
        "effectiveSourceSets": list(effective_indexing_source_sets(resolved)),
        "config": config.to_dict(),
        "summary": {
            "sourceRecordCount": len(records),
            "chunkCount": len(chunks),
            "statusCounts": status_counts,
            "parseErrorCount": len(parse_errors),
            "blockingSourceCount": len(blocking_records),
        },
        "sources": records,
        "blockers": blockers,
        "wouldMutateOnIndex": {
            "v2CandidateStore": bool(config.enabled and config.paths),
            "activeV2Snapshot": False,
            "legacyIndex": False,
            "runtimeSourceFiles": False,
            "settings": False,
        },
        "mutationPolicy": {
            "planIsReadOnly": True,
            "writesRestrictedToV2StoreDuringCandidateBuild": True,
            "legacyIndexMutated": False,
        },
    }


def _discover_files(config: ExternalSourceSettings) -> tuple[list[_DiscoveredFile], list[dict[str, Any]]]:
    files: list[_DiscoveredFile] = []
    records: list[dict[str, Any]] = []
    for configured_root in config.paths:
        root = configured_root.absolute()
        try:
            root_lstat = root.lstat()
        except FileNotFoundError:
            records.append(_discovery_record(root, root, "missing", "path-not-found"))
            continue
        except OSError as exc:
            records.append(_discovery_record(root, root, "error", f"path-error:{exc.__class__.__name__}:{exc}"))
            continue
        if stat.S_ISLNK(root_lstat.st_mode) and config.symlink_policy == "reject":
            records.append(_discovery_record(root, root, "skipped", "symlink-rejected"))
            continue
        try:
            root_real = root.resolve(strict=True)
            root_stat = root_real.stat()
        except (OSError, RuntimeError) as exc:
            records.append(_discovery_record(root, root, "error", f"unresolvable-path:{exc.__class__.__name__}:{exc}"))
            continue
        boundary = root_real if stat.S_ISDIR(root_stat.st_mode) else root_real.parent
        visited_directories: set[tuple[int, int]] = set()
        _discover_path(
            root,
            root=root,
            boundary=boundary,
            relative=PurePosixPath(root.name),
            config=config,
            files=files,
            records=records,
            visited_directories=visited_directories,
            is_configured_root=True,
        )
        if len(files) >= config.max_files:
            records.append(_discovery_record(root, root, "skipped", "max-files-reached"))
            break
    files.sort(key=lambda item: (str(item.root), item.relative_path, str(item.path)))
    return files[: config.max_files], records


def _discover_path(
    path: Path,
    *,
    root: Path,
    boundary: Path,
    relative: PurePosixPath,
    config: ExternalSourceSettings,
    files: list[_DiscoveredFile],
    records: list[dict[str, Any]],
    visited_directories: set[tuple[int, int]],
    is_configured_root: bool = False,
) -> None:
    if len(files) >= config.max_files:
        return
    try:
        lstat_result = path.lstat()
    except OSError as exc:
        records.append(_discovery_record(root, path, "error", f"lstat-error:{exc.__class__.__name__}:{exc}"))
        return
    is_symlink = stat.S_ISLNK(lstat_result.st_mode)
    read_path = path
    target_stat = lstat_result
    if is_symlink:
        if config.symlink_policy == "reject":
            records.append(_discovery_record(root, path, "skipped", "symlink-rejected"))
            return
        try:
            read_path = path.resolve(strict=True)
            if not _is_within(read_path, boundary):
                records.append(_discovery_record(root, path, "skipped", "symlink-target-outside-root"))
                return
            target_stat = read_path.stat()
        except (OSError, RuntimeError) as exc:
            records.append(_discovery_record(root, path, "error", f"symlink-error:{exc.__class__.__name__}:{exc}"))
            return

    if stat.S_ISDIR(target_stat.st_mode):
        key = (target_stat.st_dev, target_stat.st_ino)
        if key in visited_directories:
            records.append(_discovery_record(root, path, "skipped", "symlink-loop-or-directory-cycle"))
            return
        visited_directories.add(key)
        if not config.recursive and not is_configured_root:
            return
        try:
            children = sorted(os.scandir(read_path), key=lambda item: item.name)
        except OSError as exc:
            records.append(_discovery_record(root, path, "error", f"scandir-error:{exc.__class__.__name__}:{exc}"))
            return
        for child in children:
            child_path = path / child.name
            child_relative = PurePosixPath(child.name) if is_configured_root else relative / child.name
            _discover_path(
                child_path,
                root=root,
                boundary=boundary,
                relative=child_relative,
                config=config,
                files=files,
                records=records,
                visited_directories=visited_directories,
            )
        return

    if not stat.S_ISREG(target_stat.st_mode):
        records.append(_discovery_record(root, path, "skipped", "not-a-regular-file"))
        return
    relative_text = path.name if is_configured_root else relative.as_posix()
    if not _matches_patterns(relative_text, config.include, config.exclude):
        return
    files.append(
        _DiscoveredFile(
            path=path.absolute(),
            read_path=read_path.resolve(strict=True),
            root=root.absolute(),
            boundary=boundary.resolve(strict=True),
            relative_path=relative_text,
            stat_result=target_stat,
            symlink=is_symlink,
        )
    )


def _matches_patterns(relative_path: str, include: tuple[str, ...], exclude: tuple[str, ...]) -> bool:
    normalized = relative_path.replace("\\", "/")
    name = PurePosixPath(normalized).name
    included = any(fnmatch.fnmatchcase(normalized, pattern) or fnmatch.fnmatchcase(name, pattern) for pattern in include)
    excluded = any(fnmatch.fnmatchcase(normalized, pattern) or fnmatch.fnmatchcase(name, pattern) for pattern in exclude)
    return included and not excluded


def _read_regular_file(item: _DiscoveredFile, config: ExternalSourceSettings) -> tuple[bytes, os.stat_result]:
    try:
        resolved_before = item.path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ExternalSourceError("path-changed-before-open", str(exc)) from exc
    if resolved_before != item.read_path or not _is_within(resolved_before, item.boundary):
        raise ExternalSourceError(
            "path-escaped-before-open",
            "The source path changed or resolved outside its configured root after discovery.",
        )
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(item.read_path, flags)
    except OSError as exc:
        raise ExternalSourceError("secure-open-failed", str(exc)) from exc
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise ExternalSourceError("not-a-regular-file", "File type changed before it could be read safely.")
        if (opened_stat.st_dev, opened_stat.st_ino) != (item.stat_result.st_dev, item.stat_result.st_ino):
            raise ExternalSourceError(
                "file-identity-changed",
                "The source file device/inode changed after discovery; retry with a stable source tree.",
            )
        try:
            resolved_after = item.path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ExternalSourceError("path-changed-after-open", str(exc)) from exc
        if resolved_after != item.read_path or not _is_within(resolved_after, item.boundary):
            raise ExternalSourceError(
                "path-escaped-after-open",
                "The source path changed or resolved outside its configured root while being opened.",
            )
        if opened_stat.st_size > config.max_file_bytes:
            raise ExternalSourceError(
                "file-too-large",
                f"File exceeds maxFileBytes={config.max_file_bytes}.",
            )
        pieces: list[bytes] = []
        remaining = config.max_file_bytes + 1
        while remaining > 0:
            piece = os.read(descriptor, min(1024 * 1024, remaining))
            if not piece:
                break
            pieces.append(piece)
            remaining -= len(piece)
        raw = b"".join(pieces)
        if len(raw) > config.max_file_bytes:
            raise ExternalSourceError("file-grew-too-large", "File exceeded the size limit while being read.")
        return raw, opened_stat
    finally:
        os.close(descriptor)


def _parse_document(extension: str, raw: bytes, *, maximum_bytes: int) -> list[_ParsedUnit]:
    parsers: dict[str, Callable[[bytes], list[_ParsedUnit]]] = {
        ".md": _parse_markdown,
        ".markdown": _parse_markdown,
        ".txt": _parse_plain_text,
        ".log": _parse_plain_text,
        ".docx": lambda value: _parse_docx(value, maximum_bytes=maximum_bytes),
        ".pdf": _parse_pdf,
        ".html": _parse_html,
        ".htm": _parse_html,
        ".rtf": _parse_rtf,
        ".csv": lambda value: _parse_delimited(value, delimiter=","),
        ".tsv": lambda value: _parse_delimited(value, delimiter="\t"),
        ".json": _parse_json,
        ".jsonl": _parse_jsonl,
    }
    units = parsers[extension](raw)
    cleaned = [unit for unit in units if _normalize_text(unit.text)]
    if not cleaned:
        raise ExternalSourceError("empty-document", "The parser found no indexable text.")
    return cleaned


def _parse_markdown(raw: bytes) -> list[_ParsedUnit]:
    text = _decode_text(raw)
    units: list[_ParsedUnit] = []
    heading = "document"
    body: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            if body:
                units.append(_ParsedUnit(heading, "\n".join(body), {"heading": heading}))
            heading = match.group(2).strip()
            body = [line]
        else:
            body.append(line)
    if body:
        units.append(_ParsedUnit(heading, "\n".join(body), {"heading": heading}))
    return units


def _parse_plain_text(raw: bytes) -> list[_ParsedUnit]:
    text = _decode_text(raw)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    return [_ParsedUnit(f"paragraph-{index}", value, {"paragraph": index}) for index, value in enumerate(paragraphs, 1)]


def _parse_docx(raw: bytes, *, maximum_bytes: int) -> list[_ParsedUnit]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ExternalSourceError("invalid-docx", "The DOCX ZIP container is invalid.") from exc
    with archive:
        members = archive.infolist()
        expanded = sum(member.file_size for member in members)
        expansion_limit = min(maximum_bytes * MAX_DOCX_EXPANSION_RATIO, 100 * 1024 * 1024)
        if expanded > expansion_limit:
            raise ExternalSourceError("docx-expansion-limit", "The DOCX expanded content exceeds the safety limit.")
        try:
            document_member = archive.getinfo("word/document.xml")
        except KeyError as exc:
            raise ExternalSourceError("invalid-docx", "word/document.xml is missing.") from exc
        if document_member.file_size > expansion_limit:
            raise ExternalSourceError("docx-document-too-large", "DOCX document.xml exceeds the safety limit.")
        xml = archive.read(document_member)
    root = ElementTree.fromstring(xml)
    body = next((item for item in root.iter() if _local_name(item.tag) == "body"), root)
    units: list[_ParsedUnit] = []
    ordinal = 0
    for child in list(body):
        kind = _local_name(child.tag)
        if kind == "p":
            value = _xml_text(child)
            if value:
                ordinal += 1
                units.append(_ParsedUnit(f"paragraph-{ordinal}", value, {"kind": "paragraph", "ordinal": ordinal}))
        elif kind == "tbl":
            for row_number, row in enumerate((item for item in child.iter() if _local_name(item.tag) == "tr"), 1):
                cells = []
                for cell in (item for item in list(row) if _local_name(item.tag) == "tc"):
                    cells.append(_xml_text(cell))
                value = " | ".join(cell for cell in cells if cell)
                if value:
                    ordinal += 1
                    units.append(
                        _ParsedUnit(
                            f"table-row-{row_number}",
                            value,
                            {"kind": "table-row", "row": row_number, "ordinal": ordinal},
                        )
                    )
    return units


def _parse_pdf(raw: bytes) -> list[_ParsedUnit]:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise ExternalSourceError(
            "missing-pdf-parser",
            "PDF parsing requires pypdf, but it is not installed.",
            suggestion=PDF_DEPENDENCY_SUGGESTION,
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(raw))
        units = []
        for page_number, page in enumerate(reader.pages, 1):
            value = str(page.extract_text() or "").strip()
            if value:
                units.append(_ParsedUnit(f"page-{page_number}", value, {"page": page_number}))
        return units
    except Exception as exc:
        raise ExternalSourceError("pdf-parse-error", str(exc)) from exc


class _StructuredHTMLParser(HTMLParser):
    block_tags = {"article", "blockquote", "caption", "dd", "div", "dt", "figcaption", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main", "p", "pre", "section", "td", "th", "title"}
    ignored_tags = {"script", "style", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.units: list[_ParsedUnit] = []
        self._blocks: list[tuple[str, list[str]]] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized in self.ignored_tags:
            self._ignored_depth += 1
        if self._ignored_depth == 0 and normalized in self.block_tags:
            self._blocks.append((normalized, []))

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self.ignored_tags:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth or normalized not in self.block_tags:
            return
        for index in range(len(self._blocks) - 1, -1, -1):
            block_tag, pieces = self._blocks[index]
            if block_tag != normalized:
                continue
            del self._blocks[index]
            value = _normalize_text(" ".join(pieces))
            if value:
                self.units.append(
                    _ParsedUnit(
                        f"{block_tag}-{len(self.units) + 1}",
                        value,
                        {"htmlTag": block_tag, "ordinal": len(self.units) + 1},
                    )
                )
            break

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = data.strip()
        if value:
            for _tag, pieces in self._blocks:
                pieces.append(value)


def _parse_html(raw: bytes) -> list[_ParsedUnit]:
    parser = _StructuredHTMLParser()
    parser.feed(_decode_text(raw))
    parser.close()
    return parser.units


def _parse_rtf(raw: bytes) -> list[_ParsedUnit]:
    source = raw.decode("latin-1")
    if not source.lstrip().startswith("{\\rtf"):
        raise ExternalSourceError("invalid-rtf", "The file does not contain an RTF header.")
    destinations = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer"}
    stack: list[tuple[bool, int]] = []
    ignored = False
    unicode_skip = 1
    output: list[str] = []
    index = 0
    while index < len(source):
        character = source[index]
        if character == "{":
            stack.append((ignored, unicode_skip))
            index += 1
            continue
        if character == "}":
            if stack:
                ignored, unicode_skip = stack.pop()
            index += 1
            continue
        if character != "\\":
            if not ignored and character not in "\r\n":
                output.append(character)
            index += 1
            continue
        index += 1
        if index >= len(source):
            break
        escaped = source[index]
        if escaped in "{}\\":
            if not ignored:
                output.append(escaped)
            index += 1
            continue
        if escaped == "'" and index + 2 < len(source):
            try:
                decoded = bytes([int(source[index + 1 : index + 3], 16)]).decode("cp1252")
            except (ValueError, UnicodeDecodeError):
                decoded = ""
            if not ignored:
                output.append(decoded)
            index += 3
            continue
        match = re.match(r"([A-Za-z]+)(-?\d+)? ?", source[index:])
        if not match:
            if escaped == "*":
                ignored = True
            elif not ignored and escaped == "~":
                output.append(" ")
            index += 1
            continue
        word = match.group(1).lower()
        parameter = int(match.group(2)) if match.group(2) is not None else None
        index += len(match.group(0))
        if word in destinations:
            ignored = True
        elif word == "uc" and parameter is not None:
            unicode_skip = max(0, parameter)
        elif word == "u" and parameter is not None and not ignored:
            output.append(chr(parameter if parameter >= 0 else parameter + 65536))
            index += min(unicode_skip, max(0, len(source) - index))
        elif not ignored and word in {"par", "line"}:
            output.append("\n")
        elif not ignored and word == "tab":
            output.append("\t")
    return _parse_plain_text("".join(output).encode("utf-8"))


def _parse_delimited(raw: bytes, *, delimiter: str) -> list[_ParsedUnit]:
    text = _decode_text(raw)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return []
    headers = [str(value).strip() or f"column_{index}" for index, value in enumerate(rows[0], 1)]
    units: list[_ParsedUnit] = []
    for row_number, row in enumerate(rows[1:], 2):
        pairs = [f"{headers[index] if index < len(headers) else f'column_{index + 1}'}: {value}" for index, value in enumerate(row)]
        units.append(_ParsedUnit(f"row-{row_number}", " | ".join(pairs), {"row": row_number, "headers": headers}))
    if not units:
        units.append(_ParsedUnit("header", " | ".join(headers), {"row": 1, "headers": headers}))
    return units


def _parse_json(raw: bytes) -> list[_ParsedUnit]:
    try:
        payload = json.loads(_decode_text(raw))
    except json.JSONDecodeError as exc:
        raise ExternalSourceError("invalid-json", str(exc)) from exc
    if isinstance(payload, list):
        values = [(f"item-{index}", value, {"index": index}) for index, value in enumerate(payload)]
    elif isinstance(payload, dict):
        values = [(str(key), value, {"key": str(key)}) for key, value in payload.items()]
    else:
        values = [("value", payload, {})]
    return [_ParsedUnit(label, _json_text(value), metadata) for label, value, metadata in values]


def _parse_jsonl(raw: bytes) -> list[_ParsedUnit]:
    units = []
    for line_number, line in enumerate(_decode_text(raw).splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ExternalSourceError("invalid-jsonl", f"line {line_number}: {exc}") from exc
        units.append(_ParsedUnit(f"line-{line_number}", _json_text(payload), {"line": line_number}))
    return units


def _units_to_chunks(
    item: _DiscoveredFile,
    units: list[_ParsedUnit],
    *,
    content_hash: str,
    parser_version: str,
    mode: str,
) -> list[dict[str, Any]]:
    from .rag_v2_indexer import _chunk_payload

    source_identity = str(item.path.absolute())
    result: list[dict[str, Any]] = []
    segment_number = 0
    for unit_number, unit in enumerate(units, 1):
        for part_number, part in enumerate(_split_text(unit.text), 1):
            segment_number += 1
            text_hash = hashlib.sha256(part.encode("utf-8")).hexdigest()
            stable_id = hashlib.sha256(
                f"{SOURCE_SET}|{source_identity}|{parser_version}|{unit.label}|{part_number}|{text_hash}".encode("utf-8")
            ).hexdigest()
            result.append(
                _chunk_payload(
                    source_set=SOURCE_SET,
                    text=part,
                    layer="external",
                    date=datetime.fromtimestamp(item.stat_result.st_mtime).astimezone().date().isoformat(),
                    agent=None,
                    source_path=item.path,
                    source_identity=source_identity,
                    line_number=segment_number,
                    stable_id=stable_id,
                    source_type=f"external-{item.path.suffix.lower().lstrip('.')}",
                    provenance={
                        "authority": "Operator-configured external local content; evidence, not Actanara authority.",
                        "rootPath": str(item.root),
                        "relativePath": item.relative_path,
                        "contentHash": content_hash,
                        "mtimeNs": item.stat_result.st_mtime_ns,
                        "parserVersion": parser_version,
                        "parserStatus": "parsed",
                        "externalMode": mode,
                        "unit": unit.label,
                        "unitNumber": unit_number,
                        "partNumber": part_number,
                        **unit.metadata,
                    },
                )
            )
    return result


def _source_record(
    item: _DiscoveredFile,
    *,
    source_id: str,
    parser_status: str,
    parser_error: str | None = None,
    suggestion: str | None = None,
    parser_version: str | None = None,
    opened_stat: os.stat_result | None = None,
    content_hash: str | None = None,
    chunk_count: int = 0,
    duplicate_of: str | None = None,
    incremental: bool | None = None,
) -> dict[str, Any]:
    source_stat = opened_stat or item.stat_result
    return {
        "sourceSet": SOURCE_SET,
        "sourceType": f"external-{item.path.suffix.lower().lstrip('.') or 'unknown'}",
        "sourceId": source_id,
        "sourceLogicalPath": str(item.path.absolute()),
        "path": str(item.path.absolute()),
        "rootPath": str(item.root.absolute()),
        "relativePath": item.relative_path,
        "exists": True,
        "regularFile": True,
        "symlink": item.symlink,
        "byteSize": source_stat.st_size,
        "mtimeNs": source_stat.st_mtime_ns,
        "modifiedTime": datetime.fromtimestamp(source_stat.st_mtime).astimezone().isoformat(),
        "contentHash": content_hash,
        "parserVersion": parser_version,
        "parserStatus": parser_status,
        "parserError": parser_error,
        "suggestion": suggestion,
        "fingerprint": (
            hashlib.sha256(f"{source_id}|{content_hash}|{parser_version}".encode("utf-8")).hexdigest()
            if content_hash and parser_version
            else None
        ),
        "chunkCount": chunk_count,
        "duplicateOf": duplicate_of,
        "incrementalReuse": incremental,
        "privacyClass": "local-private",
        "retentionPolicy": "operator-controlled",
        "governance": governance_for_source(SOURCE_SET),
    }


def _discovery_record(root: Path, path: Path, status: str, error: str) -> dict[str, Any]:
    return {
        "sourceSet": SOURCE_SET,
        "sourceType": "external-path",
        "sourceId": _source_id(path),
        "sourceLogicalPath": str(path.absolute()),
        "path": str(path.absolute()),
        "rootPath": str(root.absolute()),
        "relativePath": None,
        "exists": path.exists(),
        "regularFile": False,
        "symlink": path.is_symlink(),
        "byteSize": 0,
        "mtimeNs": None,
        "modifiedTime": None,
        "contentHash": None,
        "parserVersion": None,
        "parserStatus": status,
        "parserError": error,
        "suggestion": None,
        "fingerprint": None,
        "chunkCount": 0,
        "duplicateOf": None,
        "incrementalReuse": None,
        "privacyClass": "local-private",
        "retentionPolicy": "operator-controlled",
        "governance": governance_for_source(SOURCE_SET),
    }


def _load_active_external_cache(
    settings: RagSettings,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    manifest = _read_json(settings.v2_store_path / "manifest.json")
    if manifest.get("status") != "active":
        return {}, {}
    sources_path = _manifest_file(manifest, "sourcesPath", "sources.jsonl")
    index_path = _manifest_file(manifest, "activeIndexPath", "index.jsonl")
    sources: dict[str, dict[str, Any]] = {}
    chunks: dict[str, list[dict[str, Any]]] = {}
    for payload in _read_jsonl(sources_path):
        if payload.get("sourceSet") == SOURCE_SET and payload.get("sourceId"):
            sources[str(payload["sourceId"])] = payload
    for payload in _read_jsonl(index_path):
        if payload.get("sourceSet") == SOURCE_SET and payload.get("sourceId"):
            chunks.setdefault(str(payload["sourceId"]), []).append(payload)
    return sources, chunks


def _manifest_file(manifest: dict[str, Any], key: str, filename: str) -> Path | None:
    value = manifest.get(key)
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if path.suffix:
        return path
    return path / filename


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    values = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    values.append(payload)
    except OSError:
        return []
    return values


def _decode_text(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))


def _normalize_text(value: str) -> str:
    return re.sub(r"[ \t]+", " ", html.unescape(str(value or ""))).strip()


def _split_text(value: str) -> list[str]:
    normalized = str(value or "").strip()
    if len(normalized) <= MAX_CHUNK_CHARACTERS:
        return [normalized] if normalized else []
    pieces = []
    remaining = normalized
    while remaining:
        if len(remaining) <= MAX_CHUNK_CHARACTERS:
            pieces.append(remaining)
            break
        boundary = remaining.rfind("\n", 0, MAX_CHUNK_CHARACTERS)
        if boundary < MAX_CHUNK_CHARACTERS // 2:
            boundary = remaining.rfind(" ", 0, MAX_CHUNK_CHARACTERS)
        if boundary < MAX_CHUNK_CHARACTERS // 2:
            boundary = MAX_CHUNK_CHARACTERS
        pieces.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    return [piece for piece in pieces if piece]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_text(element: ElementTree.Element) -> str:
    return _normalize_text(" ".join(str(item) for item in element.itertext()))


def _source_id(path: Path) -> str:
    return hashlib.sha256(f"{SOURCE_SET}|{path.absolute()}".encode("utf-8")).hexdigest()[:24]


def _is_within(path: Path, boundary: Path) -> bool:
    try:
        path.relative_to(boundary)
        return True
    except ValueError:
        return path == boundary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview nova-RAG external source discovery and parsing.")
    parser.add_argument("--json", action="store_true", help="Print the complete JSON plan.")
    args = parser.parse_args(argv)
    plan = plan_external_sources()
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        summary = plan["summary"]
        print(
            f"external source plan: canExecute={str(plan['canExecute']).lower()} "
            f"sources={summary['sourceRecordCount']} chunks={summary['chunkCount']} "
            f"errors={summary['parseErrorCount']}"
        )
    return 0 if plan["canExecute"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

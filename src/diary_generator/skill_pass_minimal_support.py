#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Structural support for the active minimal Skill Pass harness.

This module deliberately contains no discovery, adjudication, portfolio, or
crystallization prompt.  It owns only filtered-stream loading, stable evidence
locators, Markdown envelope parsing, privacy checks, and atomic persistence.
Semantic decisions remain entirely in ``skill_pass_minimal_harness``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from data_foundation.filtered_dialogue import load_filtered_day
from data_foundation.paths import RuntimePaths


SKILL_MARKER = "<!-- actanara-skill -->"
DISCOVERY_MARKER = "<!-- actanara-discovery -->"
MIN_EXPERIMENT_GATE_TOKENS = 1000

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RECORD_HEADER_RE = re.compile(
    r"(?m)^\[record (?P<record>\d{6}) \| "
    r"(?:source=[^\]|]+ \| )?thread=(?P<thread>thread-\d{4}|unattributed-\d{6}) \|"
)
_EVIDENCE_LINE_RE = re.compile(
    r"(?mi)^Evidence[ \t]*:[ \t]*(?P<value>[^\n]+)[ \t]*$"
)
_EVIDENCE_REFERENCE_RE = re.compile(
    r"(?P<thread>thread-\d{4}|unattributed-\d{6}):"
    r"(?P<start>\d{6})(?:-(?P<end>\d{6}))?"
)
_SCORES_LINE_RE = re.compile(
    r"(?mi)^Scores[ \t]*:[ \t]*Evidence Closure[ \t]+(?P<closure>[0-5])/5"
    r"[ \t]*(?:[·,，;；|])[ \t]*Learning Value[ \t]+(?P<learning>[0-5])/5"
    r"[ \t]*(?:[·,，;；|])[ \t]*Transferability[ \t]+(?P<transfer>[0-5])/5[ \t]*$"
)
_FRONTMATTER_RE = re.compile(r"(?ms)^---[ \t]*\n(?P<body>.*?)\n---[ \t]*(?:\n|$)")
_PRIVATE_HOME_RE = re.compile(
    r"(?i)(?<![\w.-])/(?:Users|home)/[^/\s`]+/|\b[A-Z]:\\Users\\[^\\\s`]+\\"
)
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_SECRET_RE = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer\s+\S+|"
    r"(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[\"']?"
    r"(?!<|\[redacted\])[^\s\"']{8,})"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    re.I,
)
_THINKING_RE = re.compile(
    r"<(?:think|thinking|思考)>[\s\S]*?</(?:think|thinking|思考)>", re.I
)
_DISCOVERY_MARKER_RE = re.compile(
    rf"(?m)^{re.escape(DISCOVERY_MARKER)}[ \t]*$"
)
_RECORDS_LINE_RE = re.compile(
    r"(?mi)^Records[ \t]*:[ \t]*(?P<value>[^\n]+)[ \t]*$"
)
_GENERIC_DISCOVERY_HEADING = "# 一句话候选身份"


try:
    import tiktoken
except ImportError:  # pragma: no cover - optional runtime accelerator
    tiktoken = None


class SkillPassError(RuntimeError):
    """Raised when the pass cannot safely produce a report."""


@dataclass(frozen=True)
class FilteredEntry:
    source: str
    time: str
    role: str
    content: str
    conversation_id: str = ""
    ordinal: int = 0
    occurrence_count: int = 1


@dataclass(frozen=True)
class ScoreCard:
    evidence_closure: int
    learning_value: int
    transferability: int

    def markdown(self) -> str:
        return (
            f"Scores: Evidence Closure {self.evidence_closure}/5 · "
            f"Learning Value {self.learning_value}/5 · "
            f"Transferability {self.transferability}/5"
        )


@dataclass(frozen=True)
class SkillCandidate:
    name: str
    description: str
    title: str
    trigger: str
    procedure: str
    pitfalls: str
    verification: str
    evidence: tuple[str, ...]
    scores: ScoreCard

    def markdown(self) -> str:
        return "\n".join(
            [
                SKILL_MARKER,
                f"Evidence: {', '.join(self.evidence)}",
                self.scores.markdown(),
                "",
                "---",
                f"name: {self.name}",
                f"description: {json.dumps(self.description, ensure_ascii=False)}",
                "---",
                "",
                f"# {self.title}",
                "",
                "## Trigger",
                self.trigger.strip(),
                "",
                "## Procedure",
                self.procedure.strip(),
                "",
                "## Pitfalls",
                self.pitfalls.strip(),
                "",
                "## Verification",
                self.verification.strip(),
            ]
        ).strip()


@dataclass(frozen=True)
class DiscoveryCandidate:
    title: str
    goal: str
    obstacle: str
    effective_turn: str
    observed_result: str
    reuse_hypothesis: str
    evidence: tuple[str, ...]
    record_ids: tuple[int, ...] = ()
    summary: str = ""

    def markdown(self) -> str:
        records = self.record_ids or _record_ids_from_references(self.evidence)
        record_line = ", ".join(f"{record_id:06d}" for record_id in records)
        if self.summary:
            return "\n".join(
                [
                    DISCOVERY_MARKER,
                    f"# {self.title}",
                    f"Records: {record_line}",
                    f"Why: {self.summary.strip()}",
                ]
            ).strip()
        return "\n".join(
            [
                DISCOVERY_MARKER,
                f"# {self.title}",
                f"Records: {record_line}",
                "",
                "## Goal",
                self.goal,
                "",
                "## Non-trivial Obstacle",
                self.obstacle,
                "",
                "## Effective Turn",
                self.effective_turn,
                "",
                "## Observed Result",
                self.observed_result,
                "",
                "## Reuse Hypothesis",
                self.reuse_hypothesis,
            ]
        ).strip()


def token_count(text: str) -> int:
    if tiktoken is not None:
        try:
            return len(tiktoken.get_encoding("cl100k_base").encode(str(text)))
        except Exception:
            pass
    return max(1, len(str(text)) // 2)


def normalize_business_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise SkillPassError("business date must use YYYY-MM-DD") from exc
    if parsed.isoformat() != str(value):
        raise SkillPassError("business date must use canonical YYYY-MM-DD")
    return parsed.isoformat()


def load_filtered_stream(
    paths: RuntimePaths, business_date: str
) -> list[FilteredEntry]:
    """Load one chronological compact filtered stream across all sources."""
    root = paths.diary_dir / "__diary_daily" / business_date / "_filtered"
    if not root.exists():
        return []
    loaded: list[FilteredEntry] = []
    ordinal = 0
    for source, rows in load_filtered_day(root).items():
        for row in rows:
            content = str(row.get("content") or "").strip()
            role = str(row.get("role") or "unknown").strip().casefold()
            if not content or role not in {"user", "assistant"}:
                continue
            ordinal += 1
            loaded.append(
                FilteredEntry(
                    source=source,
                    time=str(row.get("time") or ""),
                    role=role,
                    content=content,
                    conversation_id=str(
                        row.get("conversationId") or ""
                    ).strip(),
                    ordinal=ordinal,
                    occurrence_count=max(
                        1, int(row.get("occurrenceCount") or 1)
                    ),
                )
            )
    loaded.sort(key=lambda entry: (entry.time, entry.source, entry.ordinal))
    return loaded


def render_filtered_stream(entries: Iterable[FilteredEntry]) -> str:
    """Keep global chronology and anonymize source conversation identifiers."""
    materialized = list(entries)
    aliases: dict[tuple[str, str], str] = {}
    for entry in materialized:
        if not entry.conversation_id:
            continue
        key = (entry.source, entry.conversation_id)
        if key not in aliases:
            aliases[key] = f"thread-{len(aliases) + 1:04d}"
    records: list[str] = []
    for index, entry in enumerate(materialized, start=1):
        thread = aliases.get(
            (entry.source, entry.conversation_id),
            f"unattributed-{index:06d}",
        )
        records.append(
            f"[record {index:06d} | thread={thread} | role={entry.role}]\n"
            f"{entry.content.strip()}"
        )
    return "\n\n".join(records)


def single_call_gate_tokens(
    configured_gate: int, *, override: int | None = None
) -> int:
    gate = int(override if override is not None else configured_gate)
    if gate < MIN_EXPERIMENT_GATE_TOKENS:
        raise SkillPassError(
            f"skill gate must be at least {MIN_EXPERIMENT_GATE_TOKENS} tokens"
        )
    return gate


def _contains_private_runtime_value(text: str) -> bool:
    if (
        _PRIVATE_HOME_RE.search(text)
        or _SECRET_RE.search(text)
        or _PRIVATE_KEY_RE.search(text)
    ):
        return True
    return any(
        address not in {"0.0.0.0", "127.0.0.1"}
        for address in _IPV4_RE.findall(text)
    )


def _stream_records(stream: str) -> dict[int, str]:
    matches = list(_RECORD_HEADER_RE.finditer(stream))
    records: dict[int, str] = {}
    for match in matches:
        record_id = int(match.group("record"))
        if record_id in records:
            return {}
        records[record_id] = match.group("thread")
    return records


def _parse_evidence(
    value: str, *, stream: str
) -> tuple[str, ...] | None:
    parts = [
        part.strip()
        for part in re.split(r"\s*[,，;；]\s*", value)
        if part.strip()
    ]
    records = _stream_records(stream)
    if not parts or not records:
        return None
    normalized: list[str] = []
    for part in parts:
        match = _EVIDENCE_REFERENCE_RE.fullmatch(part)
        if match is None:
            return None
        thread = match.group("thread")
        start = int(match.group("start"))
        end = int(match.group("end") or match.group("start"))
        if start > end or start not in records or end not in records:
            return None
        if records[start] != thread or records[end] != thread:
            return None
        if not any(
            start <= record_id <= end and record_thread == thread
            for record_id, record_thread in records.items()
        ):
            return None
        normalized.append(f"{thread}:{start:06d}-{end:06d}")
    return tuple(dict.fromkeys(normalized))


def _metadata(
    block: str, *, stream: str
) -> tuple[tuple[str, ...], ScoreCard] | None:
    evidence_matches = list(_EVIDENCE_LINE_RE.finditer(block))
    score_matches = list(_SCORES_LINE_RE.finditer(block))
    if len(evidence_matches) != 1 or len(score_matches) != 1:
        return None
    evidence = _parse_evidence(
        evidence_matches[0].group("value"), stream=stream
    )
    if evidence is None:
        return None
    score = score_matches[0]
    return evidence, ScoreCard(
        evidence_closure=int(score.group("closure")),
        learning_value=int(score.group("learning")),
        transferability=int(score.group("transfer")),
    )


def _parse_sections(
    body: str, names: tuple[str, ...]
) -> tuple[str, dict[str, str]] | None:
    title_match = re.search(r"(?m)^# (?P<title>[^\n]+)[ \t]*$", body)
    if title_match is None:
        return None
    title = title_match.group("title").strip()
    headings = list(re.finditer(r"(?m)^## (?P<name>[^\n]+)[ \t]*$", body))
    if (
        not title
        or len(title) > 240
        or tuple(match.group("name") for match in headings) != names
    ):
        return None
    sections: dict[str, str] = {}
    for index, match in enumerate(headings):
        end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(body)
        )
        value = body[match.end() : end].strip()
        if not value:
            return None
        sections[match.group("name")] = value
    return title, sections


def _unquote(value: str) -> str:
    raw = value.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        return decoded.strip() if isinstance(decoded, str) else ""
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].strip()
    return raw


def _parse_frontmatter(block: str) -> tuple[dict[str, str], str] | None:
    matches = list(_FRONTMATTER_RE.finditer(block))
    if len(matches) != 1:
        return None
    match = matches[0]
    values: dict[str, str] = {}
    for line in match.group("body").splitlines():
        field = re.fullmatch(
            r"([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.+)", line.strip()
        )
        if field is None or field.group(1) in values:
            return None
        values[field.group(1)] = _unquote(field.group(2))
    if set(values) != {"name", "description"} or any(
        not value for value in values.values()
    ):
        return None
    return values, block[match.end() :].strip()


def _normalize_skill_envelope(block: str) -> str:
    text = re.sub(r"\n---[ \t]*\Z", "", str(block or "").strip())
    if _FRONTMATTER_RE.search(text) is not None:
        return text
    repaired, count = re.subn(
        r"(?m)^(---[ \t]*\nname:[^\n]+\ndescription:[^\n]+)(\n+)(?=# )",
        r"\1\n---\2",
        text,
        count=1,
    )
    return repaired if count == 1 else text


def parse_skill_candidate(
    block: str, *, stream: str
) -> SkillCandidate | None:
    raw = _normalize_skill_envelope(block)
    if not raw.startswith(SKILL_MARKER) or _contains_private_runtime_value(raw):
        return None
    metadata = _metadata(raw, stream=stream)
    frontmatter = _parse_frontmatter(raw)
    if metadata is None or frontmatter is None:
        return None
    values, body = frontmatter
    if (
        not _NAME_RE.fullmatch(values["name"])
        or len(values["name"]) > 64
        or len(values["description"]) > 600
    ):
        return None
    parsed = _parse_sections(
        body, ("Trigger", "Procedure", "Pitfalls", "Verification")
    )
    if parsed is None:
        return None
    evidence, scores = metadata
    title, sections = parsed
    return SkillCandidate(
        name=values["name"],
        description=values["description"],
        title=title,
        trigger=sections["Trigger"],
        procedure=sections["Procedure"],
        pitfalls=sections["Pitfalls"],
        verification=sections["Verification"],
        evidence=evidence,
        scores=scores,
    )


def _unwrap_outer_fence(raw: str) -> str:
    text = str(raw or "").strip()
    lines = text.splitlines()
    if (
        len(lines) >= 2
        and lines[0].strip().startswith("```")
        and lines[-1].strip() == "```"
    ):
        return "\n".join(lines[1:-1]).strip()
    return text


def _parse_global_record_ids(
    value: str, *, stream: str
) -> tuple[tuple[str, ...], tuple[int, ...]] | None:
    """Resolve exact global record IDs without guessing thread identity."""
    parts = [
        part.strip()
        for part in re.split(r"\s*[,，;；]\s*", value)
        if part.strip()
    ]
    if not parts or any(re.fullmatch(r"\d{6}", part) is None for part in parts):
        return None
    records = _stream_records(stream)
    record_ids = tuple(dict.fromkeys(int(part) for part in parts))
    if not records or any(record_id not in records for record_id in record_ids):
        return None
    normalized_ids = tuple(sorted(record_ids))
    return _references_for_record_ids(normalized_ids, records=records), normalized_ids


def _references_for_record_ids(
    record_ids: Iterable[int], *, records: dict[int, str]
) -> tuple[str, ...]:
    normalized: list[str] = []
    start = previous = None
    thread = ""
    for record_id in sorted(dict.fromkeys(int(value) for value in record_ids)):
        current_thread = records.get(record_id, "")
        if not current_thread:
            continue
        if start is None:
            start = previous = record_id
            thread = current_thread
            continue
        if current_thread == thread and record_id == int(previous) + 1:
            previous = record_id
            continue
        normalized.append(
            f"{thread}:{int(start):06d}-{int(previous):06d}"
        )
        start = previous = record_id
        thread = current_thread
    if start is not None:
        normalized.append(
            f"{thread}:{int(start):06d}-{int(previous):06d}"
        )
    return tuple(normalized)


def _record_ids_from_references(
    references: Iterable[str],
) -> tuple[int, ...]:
    values: list[int] = []
    for reference in references:
        match = _EVIDENCE_REFERENCE_RE.fullmatch(reference)
        if match is None:
            continue
        start = int(match.group("start"))
        end = int(match.group("end") or match.group("start"))
        values.extend(range(start, end + 1))
    return tuple(dict.fromkeys(values))


def _record_ids_for_references(
    references: Iterable[str], *, stream: str
) -> tuple[int, ...]:
    records = _stream_records(stream)
    selected: list[int] = []
    for reference in references:
        match = _EVIDENCE_REFERENCE_RE.fullmatch(reference)
        if match is None:
            return ()
        thread = match.group("thread")
        start = int(match.group("start"))
        end = int(match.group("end") or match.group("start"))
        selected.extend(
            record_id
            for record_id, record_thread in records.items()
            if record_thread == thread and start <= record_id <= end
        )
    return tuple(sorted(dict.fromkeys(selected)))


def _parse_discovery_evidence(
    value: str, *, stream: str
) -> tuple[str, ...] | None:
    parts = [
        part.strip()
        for part in re.split(r"\s*[,，;；]\s*", value)
        if part.strip()
    ]
    if not parts:
        return None
    records = _stream_records(stream)
    if not records:
        return None
    valid: list[str] = []
    current_thread = ""
    for part in parts:
        explicit = re.match(
            r"^(thread-\d{4}|unattributed-\d{6}):", part
        )
        if explicit is not None:
            current_thread = explicit.group(1)
            expanded = part
        elif current_thread and re.fullmatch(r"\d{6}(?:-\d{6})?", part):
            expanded = f"{current_thread}:{part}"
        else:
            expanded = part
        interval = _EVIDENCE_REFERENCE_RE.fullmatch(expanded)
        if interval is None:
            return None
        thread = interval.group("thread")
        start = int(interval.group("start"))
        end = int(interval.group("end") or interval.group("start"))
        if (
            start > end
            or start not in records
            or end not in records
            or records[start] != thread
            or records[end] != thread
        ):
            return None
        matching = sorted(
            record_id
            for record_id, record_thread in records.items()
            if record_thread == thread and start <= record_id <= end
        )
        if not matching:
            return None
        range_start = matching[0]
        previous = matching[0]
        for record_id in matching[1:]:
            if record_id != previous + 1:
                valid.append(
                    f"{thread}:{range_start:06d}-{previous:06d}"
                )
                range_start = record_id
            previous = record_id
        valid.append(f"{thread}:{range_start:06d}-{previous:06d}")
    normalized = tuple(dict.fromkeys(valid))
    return normalized or None


def _parse_discovery_block(
    block: str, *, stream: str
) -> DiscoveryCandidate | None:
    raw = str(block or "").strip()
    if not raw.startswith(DISCOVERY_MARKER) or _contains_private_runtime_value(raw):
        return None
    record_rows = list(_RECORDS_LINE_RE.finditer(raw))
    evidence_rows = list(_EVIDENCE_LINE_RE.finditer(raw))
    if (len(record_rows), len(evidence_rows)) not in {(1, 0), (0, 1)}:
        return None
    if record_rows:
        parsed_records = _parse_global_record_ids(
            record_rows[0].group("value"), stream=stream
        )
        if parsed_records is None:
            return None
        evidence, record_ids = parsed_records
    else:
        evidence = _parse_discovery_evidence(
            evidence_rows[0].group("value"), stream=stream
        )
        if evidence is None:
            return None
        record_ids = _record_ids_for_references(evidence, stream=stream)
    parsed = _parse_sections(
        raw,
        (
            "Goal",
            "Non-trivial Obstacle",
            "Effective Turn",
            "Observed Result",
            "Reuse Hypothesis",
        ),
    )
    if parsed is not None:
        title, sections = parsed
        return DiscoveryCandidate(
            title=title,
            goal=sections["Goal"],
            obstacle=sections["Non-trivial Obstacle"],
            effective_turn=sections["Effective Turn"],
            observed_result=sections["Observed Result"],
            reuse_hypothesis=sections["Reuse Hypothesis"],
            evidence=evidence,
            record_ids=record_ids,
        )
    title_match = re.search(r"(?m)^# (?P<title>[^\n]+)[ \t]*$", raw)
    why_matches = list(
        re.finditer(r"(?ms)^Why[ \t]*:[ \t]*(?P<why>.+?)\s*$", raw)
    )
    if title_match is None or len(why_matches) != 1:
        return None
    title = title_match.group("title").strip()
    summary = why_matches[0].group("why").strip()
    if not title or not summary or "\n#" in summary:
        return None
    return DiscoveryCandidate(
        title=title,
        goal="",
        obstacle="",
        effective_turn="",
        observed_result="",
        reuse_hypothesis="",
        evidence=evidence,
        record_ids=record_ids,
        summary=summary,
    )


def parse_discovery_output(
    raw_output: str, *, stream: str
) -> tuple[list[DiscoveryCandidate], int]:
    prepared = _unwrap_outer_fence(
        _THINKING_RE.sub("", str(raw_output or ""))
    ).strip()
    if not prepared or prepared.casefold() in {
        "nothing to save.",
        "nothing to preserve.",
    }:
        return [], 0
    prepared = _normalize_discovery_envelope(prepared)
    markers = list(_DISCOVERY_MARKER_RE.finditer(prepared))
    if not markers:
        return [], 1
    candidates: list[DiscoveryCandidate] = []
    rejected = 0
    for index, marker in enumerate(markers):
        end = (
            markers[index + 1].start()
            if index + 1 < len(markers)
            else len(prepared)
        )
        item = _parse_discovery_block(
            prepared[marker.start() : end], stream=stream
        )
        if item is None:
            rejected += 1
        else:
            candidates.append(item)
    return candidates, rejected


def _normalize_discovery_envelope(prepared: str) -> str:
    text = re.sub(
        r"(?im)^</?actanara-discovery>[ \t]*$",
        "",
        str(prepared or ""),
    ).strip()
    text = text.replace("## Non-trivial Obst障", "## Non-trivial Obstacle")
    if DISCOVERY_MARKER in text or _GENERIC_DISCOVERY_HEADING not in text:
        return text
    lines = text.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if line.strip() == _GENERIC_DISCOVERY_HEADING
    ]
    if not starts:
        return text
    blocks: list[str] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        title_index = start + 1
        while title_index < end and not lines[title_index].strip():
            title_index += 1
        if title_index >= end:
            blocks.append("\n".join(lines[start:end]).strip())
            continue
        title = lines[title_index].strip()
        body = "\n".join(lines[title_index + 1 : end]).strip()
        blocks.append(f"{DISCOVERY_MARKER}\n# {title}\n{body}".strip())
    return "\n\n".join(blocks)


def redact_discovery_output(raw_output: str) -> str:
    """Remove private literals without deciding what a candidate means."""
    text = _PRIVATE_KEY_BLOCK_RE.sub(
        "<redacted-private-key>", str(raw_output or "")
    )
    text = _PRIVATE_HOME_RE.sub("<private-home>/", text)
    text = _SECRET_RE.sub("<redacted-secret>", text)

    def replace_address(match: re.Match[str]) -> str:
        address = match.group(0)
        return (
            address
            if address in {"0.0.0.0", "127.0.0.1"}
            else "<network-address>"
        )

    return _IPV4_RE.sub(replace_address, text)


def select_cited_records(
    stream: str, candidates: Iterable[DiscoveryCandidate]
) -> str:
    selected_ids: set[int] = set()
    for candidate in candidates:
        if candidate.record_ids:
            selected_ids.update(candidate.record_ids)
            continue
        for reference in candidate.evidence:
            match = _EVIDENCE_REFERENCE_RE.fullmatch(reference)
            if match is None:
                continue
            thread = match.group("thread")
            start = int(match.group("start"))
            end = int(match.group("end") or match.group("start"))
            selected_ids.update(
                record_id
                for record_id, record_thread in _stream_records(stream).items()
                if record_thread == thread and start <= record_id <= end
            )

    matches = list(_RECORD_HEADER_RE.finditer(stream))
    blocks: list[str] = []
    for index, match in enumerate(matches):
        record_id = int(match.group("record"))
        if record_id not in selected_ids:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(stream)
        blocks.append(stream[match.start() : end].strip())
    return "\n\n".join(blocks)


def _persist_raw(path: Path, raw: str) -> None:
    if _contains_private_runtime_value(raw):
        content = (
            "# Raw Skill Pass output withheld\n\n"
            "The model response matched the private-output safety gate and "
            "was not persisted.\n"
        )
    else:
        content = str(raw or "").rstrip() + "\n"
    _write_text_atomic(path, content)


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

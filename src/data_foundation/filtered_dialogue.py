"""Lossless structural deduplication for filtered dialogue artifacts.

The collector can observe the same conversation prefix in several session
snapshots.  This module stores each exact prefix message once while retaining
the occurrence data needed to reconstruct the original filtered stream.  It
does not classify, summarize, truncate, or semantically compare messages.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


FILTERED_DIALOGUE_SCHEMA = "actanara.filtered-dialogue.v2"


def opaque_conversation_id(source: str, identity: str) -> str:
    """Return a stable identifier without exposing the source path/session key."""

    material = f"{str(source)}\0{str(identity)}".encode("utf-8", errors="surrogatepass")
    return "CNV-" + hashlib.sha256(material).hexdigest()[:24]


def compact_filtered_entries(
    entries: Iterable[Mapping[str, Any]],
    *,
    source: str,
) -> list[dict[str, Any]]:
    """Collapse exact shared conversation prefixes and retain every occurrence.

    A message is shared only when its role and exact filtered content match at
    the same parent prefix.  Similar wording is never merged.  Records without
    a conversation id remain isolated so legacy input cannot be over-deduped.
    """

    materialized = [dict(entry) for entry in entries if isinstance(entry, Mapping)]
    content_rows = [entry for entry in materialized if str(entry.get("content") or "").strip()]
    if content_rows and all(entry.get("schemaVersion") == FILTERED_DIALOGUE_SCHEMA for entry in content_rows):
        return sorted(content_rows, key=lambda entry: _positive_int(entry.get("firstOrdinal"), 0))

    conversations: OrderedDict[str, list[tuple[int, dict[str, Any]]]] = OrderedDict()
    for ordinal, entry in enumerate(content_rows, start=1):
        conversation_id = str(entry.get("conversationId") or "").strip()
        if not conversation_id:
            conversation_id = f"unattributed-{ordinal:08d}"
        conversations.setdefault(conversation_id, []).append((ordinal, entry))

    nodes: dict[tuple[str, str, str], dict[str, Any]] = {}
    for conversation_id, sequence in conversations.items():
        parent_message_id = ""
        parent_identity = (
            f"isolated-root:{conversation_id}"
            if conversation_id.startswith("unattributed-")
            else ""
        )
        for ordinal, entry in sequence:
            role = str(entry.get("role") or "unknown").strip()
            content = str(entry.get("content") or "")
            key = (parent_identity, role, content)
            node = nodes.get(key)
            if node is None:
                message_id = _message_id(source, parent_identity, role, content)
                node = {
                    "schemaVersion": FILTERED_DIALOGUE_SCHEMA,
                    "messageId": message_id,
                    "parentMessageId": parent_message_id,
                    "role": role,
                    "content": content,
                    "time": str(entry.get("time") or ""),
                    "conversationId": conversation_id,
                    "occurrenceCount": 0,
                    "firstOrdinal": ordinal,
                    "lastTime": str(entry.get("time") or ""),
                    "occurrences": [],
                }
                nodes[key] = node
            node["occurrenceCount"] += 1
            node["lastTime"] = str(entry.get("time") or node.get("lastTime") or "")
            node["occurrences"].append(
                {
                    "conversationId": conversation_id,
                    "time": str(entry.get("time") or ""),
                    "ordinal": ordinal,
                }
            )
            parent_message_id = str(node["messageId"])
            parent_identity = parent_message_id

    return sorted(nodes.values(), key=lambda entry: _positive_int(entry.get("firstOrdinal"), 0))


def replay_filtered_entries(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Reconstruct the expanded v1 message stream from compact v2 records."""

    replay: list[tuple[int, dict[str, str]]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        content = str(entry.get("content") or "")
        if not content:
            continue
        occurrences = entry.get("occurrences")
        if entry.get("schemaVersion") != FILTERED_DIALOGUE_SCHEMA or not isinstance(occurrences, list):
            replay.append(
                (
                    len(replay) + 1,
                    {
                        "role": str(entry.get("role") or ""),
                        "content": content,
                        "time": str(entry.get("time") or ""),
                        "conversationId": str(entry.get("conversationId") or ""),
                    },
                )
            )
            continue
        for occurrence in occurrences:
            if not isinstance(occurrence, Mapping):
                continue
            replay.append(
                (
                    _positive_int(occurrence.get("ordinal"), len(replay) + 1),
                    {
                        "role": str(entry.get("role") or ""),
                        "content": content,
                        "time": str(occurrence.get("time") or ""),
                        "conversationId": str(occurrence.get("conversationId") or ""),
                    },
                )
            )
    replay.sort(key=lambda item: item[0])
    return [entry for _ordinal, entry in replay]


def load_filtered_source_entries(source_dir: Path) -> list[dict[str, Any]]:
    """Read a source directory and compact legacy v1 data in memory."""

    rows: list[dict[str, Any]] = []
    for path in sorted(source_dir.glob("*.jsonl")):
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    return compact_filtered_entries(rows, source=source_dir.name)


def load_filtered_day(root: Path, *, include_cron: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Load compact records for every source in one filtered business day."""

    if not root.exists():
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for source_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if source_dir.name == "cron" and not include_cron:
            continue
        entries = load_filtered_source_entries(source_dir)
        if entries:
            result[source_dir.name] = entries
    return result


def write_compact_filtered_jsonl(
    path: Path,
    entries: Iterable[Mapping[str, Any]],
    *,
    source: str,
) -> list[dict[str, Any]]:
    """Atomically write compact filtered records and return the written rows."""

    compact = compact_filtered_entries(entries, source=source)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            for entry in compact:
                handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
    return compact


def _message_id(source: str, parent_message_id: str, role: str, content: str) -> str:
    material = "\0".join((str(source), parent_message_id, role, content)).encode(
        "utf-8",
        errors="surrogatepass",
    )
    return "MSG-" + hashlib.sha256(material).hexdigest()[:32]


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default

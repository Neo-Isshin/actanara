"""Read-only usage adapters for normalized ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator, Mapping

from ..paths import RuntimePaths
from ..session_files import is_openclaw_session_file
from ..settings import default_external_tool_path, external_tool_path, resolve_external_tool_paths
from ..runtime_sources.antigravity import AntigravityRuntime
from ..runtime_sources.base import SessionRecord, UsageRecord
from ..runtime_sources.cursor import CursorRuntime
from ..runtime_sources.opencode import OpenCodeRuntime
from ..time import parse_timestamp
from ..token_semantics import normalize_cached_input_detail
from .base import Cursor, NormalizedEvent, SourceArtifact


class UsageAdapter:
    adapter_version = "shadow-usage-v2"
    capabilities = {"usage_events", "session_inventory"}

    _JSONL_CURSOR_KIND = "jsonl-byte-offset"
    _JSONL_CURSOR_VERSION = 1
    _JSONL_HASH_BYTES = 4096

    def fingerprint(self, artifact: SourceArtifact) -> str:
        stat = artifact.path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    def _event_key(self, artifact: SourceArtifact, locator: str) -> str:
        raw = f"{artifact.path.absolute()}:{locator}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _usage_event(
        self,
        artifact: SourceArtifact,
        *,
        locator: str,
        session_key: str,
        occurred_at: object,
        model: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        message_count: int = 1,
        metadata: dict | None = None,
    ) -> NormalizedEvent | None:
        timestamp = parse_timestamp(occurred_at)
        if timestamp is None:
            return None
        payload = {
            "model_key": model or "unknown",
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "cache_read_tokens": int(cache_read_tokens or 0),
            "cache_write_tokens": int(cache_write_tokens or 0),
            "reasoning_tokens": int(reasoning_tokens or 0),
            "message_count": int(message_count or 1),
            "raw_locator": {"path": str(artifact.path), "locator": locator},
            "metadata": metadata or {},
        }
        return NormalizedEvent(
            tool_key=self.tool_key,
            external_event_key=self._event_key(artifact, locator),
            external_session_key=session_key,
            occurred_at=timestamp,
            event_type="usage",
            payload=payload,
        )

    @classmethod
    def _jsonl(
        cls,
        path: Path,
        cursor: Cursor | None = None,
    ) -> Iterable[tuple[int, dict]]:
        """Read JSONL records, using a durable byte cursor during ingestion.

        Calls without a cursor retain the legacy full-file parser used by
        adapter-level callers.  Ingestion always supplies a cursor, which
        means only newline-committed records advance progress; an incomplete
        tail is retried when the writer appends the rest of the line.
        """

        if cursor is None:
            return cls._legacy_jsonl(path)

        handle = path.open("rb")
        try:
            stat = os.fstat(handle.fileno())
            cursor.source_stat_at_open = _stat_signature(stat)
            plan = cls._plan_jsonl_read(handle, stat, cursor)
        except BaseException:
            handle.close()
            raise
        if plan["mode"] == "hit":
            handle.close()
            cls._publish_unchanged_jsonl_cursor(cursor, plan)
            return ()
        return cls._incremental_jsonl(handle, stat, cursor, plan)

    @staticmethod
    def _legacy_jsonl(path: Path) -> Iterator[tuple[int, dict]]:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for number, line in enumerate(handle, 1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield number, value

    @classmethod
    def _plan_jsonl_read(cls, handle: BinaryIO, stat: os.stat_result, cursor: Cursor) -> dict[str, Any]:
        cursor.managed = True
        requested = set(cursor.requested_business_dates)
        prior = cls._validated_jsonl_cursor(cursor.value)
        prior_coverage = _decode_date_ranges(prior.get("coverage", ())) if prior else set()
        prior_min = _cursor_date(prior.get("observed_min_date")) if prior else None
        prior_max = _cursor_date(prior.get("observed_max_date")) if prior else None
        prior_event_counts = _cursor_event_counts(prior.get("event_counts")) if prior else {}
        cursor.reset_business_dates = frozenset()

        if prior is None:
            mode = "full"
            reason = "bootstrap"
            start_offset = 0
            start_line = 0
            write_dates = requested
            coverage = requested
            observed_min = observed_max = None
            event_counts: dict[str, int] = {}
            # A legacy database may already contain rows without any cursor
            # coverage proof. Rebuild the requested window instead of merely
            # upserting and leaving stale line-derived event keys behind.
            cursor.reset_required = True
            cursor.reset_business_dates = frozenset(requested)
        elif cursor.force_full_reset:
            mode = "full"
            reason = "adapter-state-reset"
            start_offset = 0
            start_line = 0
            write_dates = prior_coverage | requested
            coverage = prior_coverage | requested
            observed_min = observed_max = None
            event_counts = {}
            cursor.reset_required = True
            cursor.reset_business_dates = frozenset(write_dates)
        elif prior["policy_hash"] != cursor.policy_hash:
            mode = "full"
            reason = "policy-reset"
            start_offset = 0
            start_line = 0
            write_dates = prior_coverage | requested
            coverage = prior_coverage | requested
            observed_min = observed_max = None
            event_counts = {}
            cursor.reset_required = True
            cursor.reset_business_dates = frozenset(write_dates)
        else:
            same_identity = (
                int(prior["device"]) == int(stat.st_dev)
                and int(prior["inode"]) == int(stat.st_ino)
            )
            exact_snapshot = (
                same_identity
                and int(prior["observed_size"]) == int(stat.st_size)
                and int(prior["mtime_ns"]) == int(stat.st_mtime_ns)
                and int(prior["ctime_ns"]) == int(stat.st_ctime_ns)
            )
            missing = requested - prior_coverage
            missing_may_exist = {
                day for day in missing if _date_inside_envelope(day, prior_min, prior_max)
            }
            if exact_snapshot and not missing_may_exist:
                mode = "hit"
                reason = "covered" if not missing else "known-empty-window"
                start_offset = int(prior["committed_offset"])
                start_line = int(prior["committed_lines"])
                write_dates = set()
                coverage = prior_coverage | requested
                observed_min, observed_max = prior_min, prior_max
                event_counts = prior_event_counts
                if missing:
                    # Coverage is a proof that the selected date has been
                    # reconciled, not merely that the source contains no
                    # matching event. Remove any pre-cursor legacy rows before
                    # claiming a known-empty date.
                    cursor.reset_required = True
                    cursor.reset_business_dates = frozenset(missing)
            elif exact_snapshot:
                mode = "full"
                reason = "coverage-backfill"
                start_offset = 0
                start_line = 0
                write_dates = requested
                coverage = prior_coverage | requested
                observed_min = observed_max = None
                event_counts = {}
                cursor.reset_required = True
                cursor.reset_business_dates = frozenset(requested)
            elif (
                same_identity
                and int(stat.st_size) > int(prior["observed_size"])
                and cls._jsonl_prefix_is_unchanged(handle, prior, cursor)
            ):
                if missing_may_exist:
                    mode = "full"
                    reason = "coverage-backfill-after-append"
                    start_offset = 0
                    start_line = 0
                    # The full scan is required for the missing window anyway;
                    # also consume appended late events for prior coverage.
                    write_dates = prior_coverage | requested
                    observed_min = observed_max = None
                    event_counts = {}
                    cursor.reset_required = True
                    cursor.reset_business_dates = frozenset(requested)
                else:
                    mode = "append"
                    reason = "append"
                    start_offset = int(prior["committed_offset"])
                    start_line = int(prior["committed_lines"])
                    # Late appended events must be written for every date that
                    # this artifact already promises to cover.
                    write_dates = prior_coverage | requested
                    observed_min, observed_max = prior_min, prior_max
                    event_counts = prior_event_counts
                    if missing:
                        cursor.reset_required = True
                        cursor.reset_business_dates = frozenset(missing)
                coverage = prior_coverage | requested
            else:
                mode = "full"
                reason = "source-reset"
                start_offset = 0
                start_line = 0
                write_dates = prior_coverage | requested
                coverage = prior_coverage | requested
                observed_min = observed_max = None
                event_counts = {}
                cursor.reset_required = True
                cursor.reset_business_dates = frozenset(write_dates)

        cursor.read_mode = mode
        cursor.write_business_dates = frozenset(write_dates)
        cursor._observed_min_date = observed_min
        cursor._observed_max_date = observed_max
        cursor._event_counts = dict(event_counts)
        return {
            "mode": mode,
            "reason": reason,
            "start_offset": start_offset,
            "start_line": start_line,
            "coverage": coverage,
            "prior": prior,
        }

    @classmethod
    def _validated_jsonl_cursor(cls, value: object) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if value.get("kind") != cls._JSONL_CURSOR_KIND or value.get("version") != cls._JSONL_CURSOR_VERSION:
            return None
        if value.get("scan_complete") is not True:
            return None
        policy_hash = value.get("policy_hash")
        if not isinstance(policy_hash, str) or len(policy_hash) != 64:
            return None
        integer_keys = (
            "device",
            "inode",
            "observed_size",
            "mtime_ns",
            "ctime_ns",
            "committed_offset",
            "committed_lines",
        )
        if any(
            isinstance(value.get(key), bool)
            or not isinstance(value.get(key), int)
            or int(value[key]) < 0
            for key in integer_keys
        ):
            return None
        if int(value["committed_offset"]) > int(value["observed_size"]):
            return None
        for key in ("head", "boundary", "partial_tail"):
            block = value.get(key)
            if not isinstance(block, dict):
                return None
            size_key = "size" if key == "partial_tail" else "length"
            size = block.get(size_key)
            digest = block.get("sha256")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                return None
            if not isinstance(digest, str) or len(digest) != 64:
                return None
        boundary = value["boundary"]
        if (
            isinstance(boundary.get("offset"), bool)
            or not isinstance(boundary.get("offset"), int)
            or boundary["offset"] < 0
            or boundary["offset"] + boundary["length"] > value["observed_size"]
        ):
            return None
        if value["head"]["length"] > value["observed_size"]:
            return None
        partial_size = value["observed_size"] - value["committed_offset"]
        if value["partial_tail"]["size"] != partial_size:
            return None
        try:
            coverage = _decode_date_ranges(value.get("coverage", ()))
            event_counts = _cursor_event_counts(value.get("event_counts"))
        except ValueError:
            return None
        minimum = _cursor_date(value.get("observed_min_date"))
        maximum = _cursor_date(value.get("observed_max_date"))
        if minimum is None and value.get("observed_min_date") is not None:
            return None
        if maximum is None and value.get("observed_max_date") is not None:
            return None
        if (minimum is None) != (maximum is None) or (minimum is not None and maximum < minimum):
            return None
        if not set(event_counts).issubset(coverage):
            return None
        if event_counts and (
            minimum is None
            or maximum is None
            or any(not _date_inside_envelope(day, minimum, maximum) for day in event_counts)
        ):
            return None
        return value

    @classmethod
    def _jsonl_prefix_is_unchanged(
        cls,
        handle: BinaryIO,
        prior: dict[str, Any],
        cursor: Cursor,
    ) -> bool:
        for key in ("head", "boundary"):
            block = prior[key]
            offset = int(block.get("offset", 0)) if key == "boundary" else 0
            data = _read_binary_region(handle, offset, int(block["length"]))
            cursor.validation_bytes_read += len(data)
            if len(data) != int(block["length"]) or hashlib.sha256(data).hexdigest() != block["sha256"]:
                return False
        partial = prior["partial_tail"]
        if int(partial["size"]):
            data = _read_binary_region(
                handle,
                int(prior["committed_offset"]),
                int(partial["size"]),
            )
            cursor.validation_bytes_read += len(data)
            if len(data) != int(partial["size"]) or hashlib.sha256(data).hexdigest() != partial["sha256"]:
                return False
        return True

    @classmethod
    def _incremental_jsonl(
        cls,
        handle: BinaryIO,
        initial_stat: os.stat_result,
        cursor: Cursor,
        plan: dict[str, Any],
    ) -> Iterator[tuple[int, dict]]:
        committed_offset = int(plan["start_offset"])
        committed_lines = int(plan["start_line"])
        read_end = committed_offset
        try:
            handle.seek(committed_offset)
            while True:
                raw = handle.readline()
                if not raw:
                    break
                cursor.data_bytes_read += len(raw)
                read_end = handle.tell()
                if not raw.endswith(b"\n"):
                    break
                committed_offset = read_end
                committed_lines += 1
                try:
                    value = json.loads(raw.decode("utf-8", errors="ignore"))
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield committed_lines, value
            final_stat = os.fstat(handle.fileno())
            if _stat_signature(final_stat) != _stat_signature(initial_stat):
                raise RuntimeError("source artifact changed during incremental ingestion")
            cursor.next_value = cls._build_jsonl_cursor(
                handle,
                initial_stat,
                observed_size=read_end,
                committed_offset=committed_offset,
                committed_lines=committed_lines,
                coverage=set(plan["coverage"]),
                observed_min=cursor._observed_min_date,
                observed_max=cursor._observed_max_date,
                event_counts=cursor._event_counts,
                policy_hash=cursor.policy_hash,
            )
        finally:
            handle.close()

    @classmethod
    def _build_jsonl_cursor(
        cls,
        handle: BinaryIO,
        stat: os.stat_result,
        *,
        observed_size: int,
        committed_offset: int,
        committed_lines: int,
        coverage: set[str],
        observed_min: str | None,
        observed_max: str | None,
        event_counts: dict[str, int],
        policy_hash: str,
    ) -> dict[str, Any]:
        head_length = min(cls._JSONL_HASH_BYTES, observed_size)
        boundary_length = min(cls._JSONL_HASH_BYTES, observed_size)
        boundary_offset = observed_size - boundary_length
        partial_size = observed_size - committed_offset
        head = _read_binary_region(handle, 0, head_length)
        boundary = _read_binary_region(handle, boundary_offset, boundary_length)
        partial = _read_binary_region(handle, committed_offset, partial_size)
        return {
            "kind": cls._JSONL_CURSOR_KIND,
            "version": cls._JSONL_CURSOR_VERSION,
            "scan_complete": True,
            "policy_hash": policy_hash,
            "device": int(stat.st_dev),
            "inode": int(stat.st_ino),
            "observed_size": int(observed_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "ctime_ns": int(stat.st_ctime_ns),
            "committed_offset": int(committed_offset),
            "committed_lines": int(committed_lines),
            "head": {"length": len(head), "sha256": hashlib.sha256(head).hexdigest()},
            "boundary": {
                "offset": int(boundary_offset),
                "length": len(boundary),
                "sha256": hashlib.sha256(boundary).hexdigest(),
            },
            "partial_tail": {"size": len(partial), "sha256": hashlib.sha256(partial).hexdigest()},
            "coverage": _encode_date_ranges(coverage),
            "observed_min_date": observed_min,
            "observed_max_date": observed_max,
            "event_counts": {
                day: int(count)
                for day, count in sorted(event_counts.items())
                if day in coverage and count > 0
            },
        }

    @classmethod
    def _publish_unchanged_jsonl_cursor(cls, cursor: Cursor, plan: dict[str, Any]) -> None:
        prior = dict(plan["prior"] or {})
        prior["coverage"] = _encode_date_ranges(set(plan["coverage"]))
        cursor.next_value = prior


class OpenClawAdapter(UsageAdapter):
    tool_key = "openclaw"
    capabilities = UsageAdapter.capabilities | {"message_metadata"}

    def __init__(self, root: Path | None = None):
        self.root = root or _external_tool_path("openclaw", "agentsRoot", default_external_tool_path("openclaw", "agentsRoot"))

    def discover_sources(self) -> Iterable[SourceArtifact]:
        if not self.root.exists():
            return ()
        return (
            SourceArtifact(self.tool_key, path, "session_jsonl")
            for path in self.root.glob("*/sessions/*.jsonl*")
            if is_openclaw_session_file(path.name)
        )

    def read_incremental(self, artifact: SourceArtifact, cursor: Cursor | None = None) -> Iterable[NormalizedEvent]:
        agent = artifact.path.parent.parent.name
        for line, value in self._jsonl(artifact.path, cursor):
            message = value.get("message", {})
            usage = message.get("usage", {})
            if value.get("type") != "message" or message.get("role") != "assistant" or not usage:
                continue
            event = self._usage_event(
                artifact,
                locator=f"line:{line}",
                session_key=str(value.get("sessionId") or f"{agent}:{artifact.path.stem}"),
                occurred_at=value.get("timestamp") or message.get("timestamp"),
                model=value.get("model") or message.get("model"),
                input_tokens=usage.get("input") or usage.get("input_tokens"),
                output_tokens=usage.get("output") or usage.get("output_tokens"),
                cache_read_tokens=usage.get("cacheRead") or usage.get("cache_read") or usage.get("cache_read_input_tokens"),
                cache_write_tokens=usage.get("cacheWrite") or usage.get("cache_write"),
                metadata={"agent_key": agent},
            )
            if event:
                yield event


class ClaudeCodeAdapter(UsageAdapter):
    tool_key = "claude-code"
    capabilities = UsageAdapter.capabilities | {"workspace_metadata"}

    def __init__(self, root: Path | None = None):
        self.root = root or _external_tool_path("claudeCode", "projectsRoot", default_external_tool_path("claudeCode", "projectsRoot"))

    def discover_sources(self) -> Iterable[SourceArtifact]:
        return () if not self.root.exists() else (
            SourceArtifact(self.tool_key, path, "session_jsonl") for path in self.root.rglob("*.jsonl")
        )

    def read_incremental(self, artifact: SourceArtifact, cursor: Cursor | None = None) -> Iterable[NormalizedEvent]:
        for line, value in self._jsonl(artifact.path, cursor):
            message = value.get("message", {})
            usage = message.get("usage", {})
            if value.get("type") != "assistant" or not usage:
                continue
            event = self._usage_event(
                artifact,
                locator=f"line:{line}",
                session_key=str(value.get("sessionId") or artifact.path.stem),
                occurred_at=value.get("timestamp"),
                model=message.get("model"),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_tokens=usage.get("cache_read_input_tokens"),
                cache_write_tokens=usage.get("cache_creation_input_tokens"),
                metadata={"cwd": value.get("cwd")},
            )
            if event:
                yield event


class CodexAdapter(UsageAdapter):
    tool_key = "codex"
    capabilities = UsageAdapter.capabilities | {"workspace_metadata"}

    def __init__(self, root: Path | None = None):
        self.root = root or _external_tool_path("codex", "sessionsRoot", default_external_tool_path("codex", "sessionsRoot"))

    def discover_sources(self) -> Iterable[SourceArtifact]:
        return () if not self.root.exists() else (
            SourceArtifact(self.tool_key, path, "rollout_jsonl") for path in self.root.rglob("rollout-*.jsonl")
        )

    def read_incremental(self, artifact: SourceArtifact, cursor: Cursor | None = None) -> Iterable[NormalizedEvent]:
        session_key = artifact.path.stem
        cwd = None
        current_model = None
        if cursor is not None:
            persisted_state = cursor.value.get("adapter_state")
            if self._validated_jsonl_cursor(cursor.value) is not None and not _valid_codex_adapter_state(
                persisted_state
            ):
                cursor.force_full_reset = True
        records = self._jsonl(artifact.path, cursor)
        if cursor is not None and cursor.read_mode in {"append", "hit"}:
            state = cursor.value.get("adapter_state")
            if isinstance(state, dict):
                session_key = str(state.get("session_key") or session_key)
                cwd = str(state.get("cwd") or "").strip() or None
                current_model = str(state.get("current_model") or "").strip() or None
        for line, value in records:
            payload = value.get("payload", {})
            if value.get("type") == "session_meta":
                session_key = str(payload.get("id") or session_key)
                cwd = payload.get("cwd")
                continue
            if value.get("type") == "turn_context":
                current_model = _codex_model_from_payload(payload) or current_model
                continue
            if value.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            usage = info.get("last_token_usage")
            if not usage:
                continue
            raw_input = usage.get("input_tokens") or 0
            output = usage.get("output_tokens") or 0
            cache_read = usage.get("cached_input_tokens") or 0
            input_tokens, cache_read_tokens, cache_semantics = normalize_cached_input_detail(
                input_tokens=raw_input,
                output_tokens=output,
                cache_read_tokens=cache_read,
                reported_total_tokens=usage.get("total_tokens"),
            )
            event = self._usage_event(
                artifact,
                locator=f"line:{line}",
                session_key=session_key,
                occurred_at=value.get("timestamp"),
                model=_codex_model_from_payload(payload) or current_model or _codex_model_from_context_window(info.get("model_context_window")),
                input_tokens=input_tokens,
                output_tokens=output,
                cache_read_tokens=cache_read_tokens,
                reasoning_tokens=usage.get("reasoning_output_tokens"),
                metadata={
                    "cwd": cwd,
                    "token_semantics": "last_token_usage",
                    "cache_input_semantics": cache_semantics,
                    "raw_input_tokens": int(raw_input or 0),
                    "reported_total_tokens": usage.get("total_tokens"),
                },
            )
            if event:
                yield event
        if cursor is not None and cursor.next_value is not None:
            cursor.next_value["adapter_state"] = {
                "session_key": session_key,
                "cwd": cwd,
                "current_model": current_model,
            }


def _codex_model_from_payload(payload: dict) -> str | None:
    for key in ("model", "model_key", "model_slug"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    settings_candidates: list[object] = [payload.get("settings")]
    collaboration = payload.get("collaboration_mode")
    if isinstance(collaboration, dict):
        settings_candidates.insert(0, collaboration.get("settings"))
    for settings in settings_candidates:
        if not isinstance(settings, dict):
            continue
        value = settings.get("model")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _valid_codex_adapter_state(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    session_key = value.get("session_key")
    if not isinstance(session_key, str) or not session_key.strip():
        return False
    return all(
        item is None or isinstance(item, str)
        for item in (value.get("cwd"), value.get("current_model"))
    )


def _codex_model_from_context_window(window: object) -> str | None:
    try:
        parsed = int(window)
    except (TypeError, ValueError):
        return None
    if parsed == 258400:
        return "gpt-5.5"
    return None


class GeminiCliAdapter(UsageAdapter):
    tool_key = "gemini-cli"

    def __init__(self, root: Path | None = None):
        self.root = root or _external_tool_path("geminiCli", "chatsRoot", default_external_tool_path("geminiCli", "chatsRoot"))

    def discover_sources(self) -> Iterable[SourceArtifact]:
        if not self.root.exists():
            return ()
        return (
            SourceArtifact(self.tool_key, path, "session_json")
            for path in self.root.glob("session-*")
            if path.suffix in {".json", ".jsonl"}
        )

    def read_incremental(self, artifact: SourceArtifact, cursor: Cursor | None = None) -> Iterable[NormalizedEvent]:
        if artifact.path.suffix == ".jsonl":
            records = self._jsonl(artifact.path, cursor)
            default_timestamp = None
        else:
            value = json.loads(artifact.path.read_text(encoding="utf-8"))
            default_timestamp = value.get("startTime")
            records = enumerate(value.get("messages", []), 1)
        for line, value in records:
            if value.get("type") != "gemini":
                continue
            tokens = value.get("tokens") or {}
            event = self._usage_event(
                artifact,
                locator=f"record:{line}",
                session_key=artifact.path.stem,
                occurred_at=value.get("timestamp") or default_timestamp,
                model=value.get("model"),
                input_tokens=tokens.get("input"),
                output_tokens=tokens.get("output"),
                cache_read_tokens=tokens.get("cached"),
            )
            if event:
                yield event


class HermesAdapter(UsageAdapter):
    tool_key = "hermes"

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or _external_tool_path("hermes", "stateDbPath", default_external_tool_path("hermes", "stateDbPath"))

    def discover_sources(self) -> Iterable[SourceArtifact]:
        return () if not self.db_path.exists() else (SourceArtifact(self.tool_key, self.db_path, "sqlite_sessions"),)

    def read_incremental(self, artifact: SourceArtifact, cursor: Cursor | None = None) -> Iterable[NormalizedEvent]:
        del cursor
        connection = sqlite3.connect(artifact.path)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
            cwd_select = "cwd" if "cwd" in columns else "'' AS cwd"
            rows = connection.execute(
                f"""
                SELECT id, started_at, model, input_tokens, output_tokens, cache_read_tokens,
                       cache_write_tokens, reasoning_tokens, api_call_count, {cwd_select}
                FROM sessions
                """
            )
            for row in rows:
                event = self._usage_event(
                    artifact,
                    locator=f"session:{row[0]}",
                    session_key=str(row[0]),
                    occurred_at=row[1],
                    model=row[2],
                    input_tokens=row[3],
                    output_tokens=row[4],
                    cache_read_tokens=row[5],
                    cache_write_tokens=row[6],
                    reasoning_tokens=row[7],
                    message_count=row[8] or 1,
                    metadata={
                        "aggregation": "session",
                        "message_semantics": "api_call_count",
                        "cwd": str(row[9] or ""),
                    },
                )
                if event:
                    yield event
        finally:
            connection.close()


class CronAdapter(UsageAdapter):
    tool_key = "cron"

    def __init__(self, root: Path | None = None):
        self.root = root or _external_tool_path("openclaw", "cronRunsRoot", default_external_tool_path("openclaw", "cronRunsRoot"))

    def discover_sources(self) -> Iterable[SourceArtifact]:
        return () if not self.root.exists() else (
            SourceArtifact(self.tool_key, path, "cron_jsonl") for path in _cron_run_files(self.root)
        )

    def read_incremental(self, artifact: SourceArtifact, cursor: Cursor | None = None) -> Iterable[NormalizedEvent]:
        for line, value in self._jsonl(artifact.path, cursor):
            usage = value.get("usage", {})
            if value.get("action") != "finished" or not usage:
                continue
            event = self._usage_event(
                artifact,
                locator=f"line:{line}",
                session_key=str(value.get("jobId") or value.get("sessionId") or artifact.path.stem),
                occurred_at=value.get("ts"),
                model=value.get("model"),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_tokens=usage.get("cacheRead"),
            )
            if event:
                yield event


class LocalRuntimeAdapter(UsageAdapter):
    """Bridge one normalized local-runtime parser into Foundation ingestion."""

    adapter_version = "local-runtime-v1"
    capabilities = UsageAdapter.capabilities | {"workspace_metadata", "dialogue"}

    def __init__(self, runtime: Any, source_root: Path):
        self.runtime = runtime
        self.source_root = Path(source_root).expanduser()
        runtime_capabilities = getattr(runtime, "capabilities", ())
        self.capabilities = set(self.capabilities) | set(runtime_capabilities)
        if getattr(runtime, "usage_status", "") == "unavailable":
            self.capabilities.discard("usage_events")
            self.capabilities.add("usage_unavailable")

    def discover_sources(self) -> Iterable[SourceArtifact]:
        artifacts = tuple(self.runtime.artifacts())
        if not artifacts:
            return ()
        coordinator = self.source_root if self.source_root.exists() else artifacts[0]
        return (SourceArtifact(self.tool_key, coordinator, "local_runtime_inventory"),)

    def read_incremental(
        self,
        artifact: SourceArtifact,
        cursor: Cursor | None = None,
    ) -> Iterable[NormalizedEvent]:
        del cursor
        sessions = tuple(self.runtime.sessions())
        sessions_by_key = {record.external_session_key: record for record in sessions}
        for record in sessions:
            event = self._session_event(artifact, record)
            if event is not None:
                yield event
        for record in self.runtime.usage():
            event = self._normalized_usage_event(
                artifact,
                record,
                sessions_by_key.get(record.external_session_key),
            )
            if event is not None:
                yield event

    def _session_event(
        self,
        artifact: SourceArtifact,
        record: SessionRecord,
    ) -> NormalizedEvent | None:
        occurred_at = record.last_active_at or record.started_at or _artifact_timestamp(artifact.path)
        if occurred_at is None:
            return None
        metadata = {
            **record.metadata,
            "source_variant": record.source_variant,
            "usage_status": getattr(self.runtime, "usage_status", "available"),
        }
        return NormalizedEvent(
            tool_key=self.tool_key,
            external_event_key=self._event_key(
                artifact,
                f"session:{record.external_session_key}",
            ),
            external_session_key=record.external_session_key,
            occurred_at=occurred_at,
            event_type="session",
            payload={
                "started_at": record.started_at,
                "last_active_at": record.last_active_at,
                "initial_cwd": record.initial_cwd,
                "title": record.title,
                "agent_key": record.agent_key,
                "model_key": record.model_key,
                "source_variant": record.source_variant,
                "usage_status": getattr(self.runtime, "usage_status", "available"),
                "raw_locator": record.raw_locator,
                "metadata": metadata,
            },
        )

    def _normalized_usage_event(
        self,
        artifact: SourceArtifact,
        record: UsageRecord,
        session: SessionRecord | None,
    ) -> NormalizedEvent | None:
        occurred_at = parse_timestamp(record.occurred_at)
        if occurred_at is None:
            return None
        metadata = {
            **record.metadata,
            "source_variant": record.source_variant,
            "tool_tokens": int(record.tool_tokens or 0),
        }
        if session is not None:
            if session.initial_cwd:
                metadata.setdefault("cwd", session.initial_cwd)
            if session.title:
                metadata.setdefault("session_title", session.title)
        return NormalizedEvent(
            tool_key=self.tool_key,
            external_event_key=record.external_event_key,
            external_session_key=record.external_session_key,
            occurred_at=occurred_at,
            event_type="usage",
            payload={
                "model_key": record.model_key or (session.model_key if session else None) or "unknown",
                "input_tokens": int(record.input_tokens or 0),
                "output_tokens": int(record.output_tokens or 0),
                "cache_read_tokens": int(record.cache_read_tokens or 0),
                "cache_write_tokens": int(record.cache_write_tokens or 0),
                "reasoning_tokens": int(record.reasoning_tokens or 0),
                "protocol_total_tokens": record.protocol_total_tokens,
                "message_count": int(record.message_count or 1),
                "raw_locator": record.raw_locator,
                "metadata": metadata,
            },
        )


class OpenCodeAdapter(LocalRuntimeAdapter):
    tool_key = "opencode"

    def __init__(self, home: Path | None = None):
        selected = home or default_external_tool_path("opencode", "home")
        super().__init__(OpenCodeRuntime(selected), selected)


class AntigravityAdapter(LocalRuntimeAdapter):
    tool_key = "antigravity"

    def __init__(self, variant_homes: Mapping[str, Path] | None = None):
        selected = dict(variant_homes or {
            "cli": default_external_tool_path("antigravity", "cliHome"),
            "ide": default_external_tool_path("antigravity", "ideHome"),
            "app": default_external_tool_path("antigravity", "appHome"),
        })
        common_home = _common_runtime_home(selected.values())
        super().__init__(AntigravityRuntime(selected), common_home)


class CursorRuntimeAdapter(LocalRuntimeAdapter):
    tool_key = "cursor"

    def __init__(
        self,
        home: Path | None = None,
        *,
        ide_state_dbs: Iterable[Path] = (),
        workspace_storage_roots: Iterable[Path] = (),
    ):
        selected = home or default_external_tool_path("cursor", "home")
        super().__init__(
            CursorRuntime(
                selected,
                ide_state_dbs=ide_state_dbs,
                workspace_storage_roots=workspace_storage_roots,
            ),
            selected,
        )


def default_usage_adapters(paths: RuntimePaths | None = None) -> tuple[UsageAdapter, ...]:
    external_paths = _external_tool_paths(paths)
    return (
        OpenClawAdapter(_tool_path(external_paths, "openclaw", "agentsRoot")),
        ClaudeCodeAdapter(_tool_path(external_paths, "claudeCode", "projectsRoot")),
        CodexAdapter(_tool_path(external_paths, "codex", "sessionsRoot")),
        GeminiCliAdapter(_tool_path(external_paths, "geminiCli", "chatsRoot")),
        HermesAdapter(_tool_path(external_paths, "hermes", "stateDbPath")),
        OpenCodeAdapter(_tool_path(external_paths, "opencode", "home")),
        AntigravityAdapter({
            variant: path
            for variant, key in (
                ("cli", "cliHome"),
                ("ide", "ideHome"),
                ("app", "appHome"),
            )
            if (path := _tool_path(external_paths, "antigravity", key)) is not None
        }),
        CursorRuntimeAdapter(
            _tool_path(external_paths, "cursor", "home"),
            ide_state_dbs=_tool_path_list(external_paths, "cursor", "ideStateDbCandidates"),
            workspace_storage_roots=_tool_path_list(external_paths, "cursor", "workspaceStorageRoots"),
        ),
        CronAdapter(_tool_path(external_paths, "openclaw", "cronRunsRoot")),
    )


def _external_tool_paths(paths: RuntimePaths | None = None) -> dict[str, dict]:
    try:
        return resolve_external_tool_paths(paths)
    except Exception:
        return {}


def _tool_path(paths: dict[str, dict], tool: str, key: str) -> Path | None:
    value = paths.get(tool, {}).get(key)
    return value if isinstance(value, Path) else None


def _tool_path_list(paths: dict[str, dict], tool: str, key: str) -> tuple[Path, ...]:
    value = paths.get(tool, {}).get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Path))


def _common_runtime_home(paths: Iterable[Path]) -> Path:
    selected = tuple(Path(path).expanduser() for path in paths)
    if not selected:
        return default_external_tool_path("antigravity", "home")
    parents = {path.parent for path in selected}
    return next(iter(parents)) if len(parents) == 1 else selected[0]


def _artifact_timestamp(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return None


def _external_tool_path(tool: str, key: str, fallback: Path) -> Path:
    try:
        return external_tool_path(tool, key)
    except Exception:
        return fallback


def _cron_run_files(root: Path) -> Iterable[Path]:
    seen: set[Path] = set()
    for pattern in ("*.jsonl", "*.jsonl.migrated"):
        for path in sorted(root.glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            yield path


def _read_binary_region(handle: BinaryIO, offset: int, length: int) -> bytes:
    if length <= 0:
        return b""
    handle.seek(offset)
    return handle.read(length)


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
    )


def _cursor_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat()


def _date_inside_envelope(value: str, minimum: str | None, maximum: str | None) -> bool:
    if minimum is None or maximum is None:
        return False
    return minimum <= value <= maximum


def _decode_date_ranges(value: object) -> set[str]:
    if value in (None, (), []):
        return set()
    if not isinstance(value, list) or len(value) > 4096:
        raise ValueError("cursor coverage ranges are invalid")
    result: set[str] = set()
    for entry in value:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError("cursor coverage range is invalid")
        start_value = _cursor_date(entry[0])
        end_value = _cursor_date(entry[1])
        if start_value is None or end_value is None:
            raise ValueError("cursor coverage date is invalid")
        start = date.fromisoformat(start_value)
        end = date.fromisoformat(end_value)
        if end < start or (end - start).days > 36525:
            raise ValueError("cursor coverage span is invalid")
        current = start
        while current <= end:
            result.add(current.isoformat())
            current += timedelta(days=1)
            if len(result) > 100000:
                raise ValueError("cursor coverage is too large")
    return result


def _encode_date_ranges(values: Iterable[str]) -> list[list[str]]:
    parsed = sorted({date.fromisoformat(value) for value in values})
    if not parsed:
        return []
    ranges: list[list[str]] = []
    start = previous = parsed[0]
    for current in parsed[1:]:
        if current == previous + timedelta(days=1):
            previous = current
            continue
        ranges.append([start.isoformat(), previous.isoformat()])
        start = previous = current
    ranges.append([start.isoformat(), previous.isoformat()])
    return ranges


def _cursor_event_counts(value: object) -> dict[str, int]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict) or len(value) > 100000:
        raise ValueError("cursor event counts are invalid")
    result: dict[str, int] = {}
    for key, count in value.items():
        day = _cursor_date(key)
        if (
            day is None
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise ValueError("cursor event count is invalid")
        if count:
            result[day] = count
    return result

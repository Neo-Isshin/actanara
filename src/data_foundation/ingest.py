"""Shadow ingestion service for normalized facts; it is not a production reader."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

from .adapters.registry import ToolRegistry
from .adapters.base import Cursor
from .adapters.usage import UsageAdapter, default_usage_adapters
from .aggregate import refresh_daily_usage
from .db import connect, migrate, seed_projects_from_registry
from .jobs import begin_ingestion_run, finish_ingestion_run
from .observations import observe_non_rag_assets
from .paths import RuntimePaths
from .time import DEFAULT_BUSINESS_DAY_START_HOUR, business_date_for, resolve_timezone

DISPLAY_NAMES = {
    "openclaw": "OpenClaw",
    "claude-code": "Claude Code",
    "codex": "Codex",
    "gemini-cli": "Gemini CLI",
    "hermes": "Hermes",
    "opencode": "OpenCode",
    "antigravity": "Antigravity",
    "cursor": "Cursor",
    "cron": "Cron",
}

INGESTION_LOCK_NAME = "foundation-ingestion.lock"
_INGESTION_THREAD_LOCK = threading.RLock()
_INGESTION_LOCKS = threading.local()


@dataclass(frozen=True)
class ShadowIngestionResult:
    run_id: int
    business_date: date
    artifacts_seen: int
    events_seen: int
    events_in_window: int
    errors: int


@dataclass(frozen=True)
class _ArtifactState:
    artifact_id: int
    cursor: dict


def _business_dates(start_date: date, end_date: date) -> tuple[date, ...]:
    if end_date < start_date:
        raise ValueError("end_date must not precede start_date")
    return tuple(start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1))


@contextmanager
def _exclusive_ingestion_guard(paths: RuntimePaths) -> Iterator[None]:
    """Serialize complete ingestion runs across threads and processes."""

    lock_dir = paths.state_dir / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = lock_dir / INGESTION_LOCK_NAME
    key = str(lock_path.absolute())

    with _INGESTION_THREAD_LOCK:
        held = getattr(_INGESTION_LOCKS, "held", None)
        if held is None:
            held = {}
            _INGESTION_LOCKS.held = held
        current = held.get(key)
        if current is not None:
            current["count"] += 1
            try:
                yield
            finally:
                current["count"] -= 1
            return

        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as error:
            raise RuntimeError("Foundation ingestion lock is unavailable") from error
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
            ):
                raise RuntimeError("Foundation ingestion lock is unsafe")
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as error:
                raise RuntimeError("Foundation ingestion lock could not be acquired") from error
            held[key] = {"count": 1, "descriptor": descriptor}
            try:
                yield
            finally:
                held.pop(key, None)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _path_is_absent(path: object) -> bool:
    if not isinstance(path, str) or not path:
        return False
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
    )


def _has_artifact_identity_continuity(
    adapter: UsageAdapter,
    artifact,
    candidate: sqlite3.Row,
    source_stat: os.stat_result,
    fingerprint: str,
) -> bool:
    """Prove a path move before preserving an existing artifact identity."""

    if not _path_is_absent(candidate["canonical_path"]):
        return False

    cursor_json = candidate["cursor_json"]
    if cursor_json is None:
        try:
            same_size = int(candidate["byte_size"]) == int(source_stat.st_size)
        except (TypeError, ValueError):
            return False
        return candidate["fingerprint"] == fingerprint and same_size

    prior = adapter._validated_jsonl_cursor(_decoded_cursor(cursor_json))
    if prior is None:
        return False
    if (
        int(prior["device"]) != int(source_stat.st_dev)
        or int(prior["inode"]) != int(source_stat.st_ino)
    ):
        return False
    exact_snapshot = (
        int(prior["observed_size"]) == int(source_stat.st_size)
        and int(prior["mtime_ns"]) == int(source_stat.st_mtime_ns)
        and int(prior["ctime_ns"]) == int(source_stat.st_ctime_ns)
    )
    if exact_snapshot:
        return True
    if int(source_stat.st_size) < int(prior["observed_size"]):
        return False

    try:
        with artifact.path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if _stat_identity(opened) != _stat_identity(source_stat):
                return False
            unchanged = adapter._jsonl_prefix_is_unchanged(handle, prior, Cursor())
            return unchanged and _stat_identity(os.fstat(handle.fileno())) == _stat_identity(opened)
    except OSError:
        return False


def _upsert_artifact(paths: RuntimePaths, adapter: UsageAdapter, artifact, run_id: int) -> _ArtifactState:
    stat = artifact.path.stat()
    fingerprint = adapter.fingerprint(artifact)
    canonical_path = str(artifact.path.absolute())
    with connect(paths) as connection:
        row = connection.execute(
            "SELECT id, cursor_json FROM source_artifacts WHERE tool_key = ? AND canonical_path = ?",
            (adapter.tool_key, canonical_path),
        ).fetchone()
        if row is None:
            cursor_matches = connection.execute(
                """
                SELECT id, canonical_path, cursor_json, fingerprint, byte_size
                FROM source_artifacts
                WHERE tool_key = ?
                  AND CAST(json_extract(
                        CASE WHEN json_valid(cursor_json) THEN cursor_json ELSE '{}' END,
                        '$.device'
                      ) AS INTEGER) = ?
                  AND CAST(json_extract(
                        CASE WHEN json_valid(cursor_json) THEN cursor_json ELSE '{}' END,
                        '$.inode'
                      ) AS INTEGER) = ?
                LIMIT 2
                """,
                (
                    adapter.tool_key,
                    int(stat.st_dev),
                    int(stat.st_ino),
                ),
            ).fetchall()
            fingerprint_matches = []
            if not cursor_matches:
                fingerprint_matches = connection.execute(
                    """
                    SELECT id, canonical_path, cursor_json, fingerprint, byte_size
                    FROM source_artifacts
                    WHERE tool_key = ? AND fingerprint = ? AND byte_size = ?
                      AND cursor_json IS NULL
                    LIMIT 2
                    """,
                    (adapter.tool_key, fingerprint, int(stat.st_size)),
                ).fetchall()
            identity_matches = cursor_matches or fingerprint_matches
            if len(identity_matches) == 1 and _has_artifact_identity_continuity(
                adapter,
                artifact,
                identity_matches[0],
                stat,
                fingerprint,
            ):
                row = identity_matches[0]
                connection.execute(
                    "UPDATE source_artifacts SET canonical_path = ? WHERE id = ?",
                    (canonical_path, row["id"]),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO source_artifacts(
                        tool_key, canonical_path, artifact_type, fingerprint, byte_size,
                        modified_at, last_ingestion_run_id, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'seen')
                    """,
                    (
                        adapter.tool_key,
                        canonical_path,
                        artifact.artifact_type,
                        fingerprint,
                        stat.st_size,
                        datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
                        run_id,
                    ),
                )
                row = connection.execute(
                    "SELECT id, cursor_json FROM source_artifacts WHERE tool_key = ? AND canonical_path = ?",
                    (adapter.tool_key, canonical_path),
                ).fetchone()
        connection.execute(
            """
            UPDATE source_artifacts
            SET artifact_type = ?, fingerprint = ?, byte_size = ?, modified_at = ?,
                last_ingestion_run_id = ?, status = 'seen'
            WHERE id = ?
            """,
            (
                artifact.artifact_type,
                fingerprint,
                stat.st_size,
                datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
                run_id,
                row["id"],
            ),
        )
        cursor = _decoded_cursor(row["cursor_json"])
        return _ArtifactState(
            artifact_id=int(row["id"]),
            cursor=cursor,
        )


def _decoded_cursor(value: object) -> dict:
    try:
        cursor = json.loads(value) if value else {}
    except (TypeError, json.JSONDecodeError):
        cursor = {}
    return cursor if isinstance(cursor, dict) else {}


def _write_error(paths: RuntimePaths, run_id: int, tool_key: str, artifact_id: int | None, error: Exception) -> None:
    with connect(paths) as connection:
        if artifact_id is not None:
            connection.execute(
                "UPDATE source_artifacts SET status = 'error' WHERE id = ?",
                (artifact_id,),
            )
        connection.execute(
            """
            INSERT INTO ingestion_errors(
                ingestion_run_id, tool_key, artifact_id, error_code, message, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                tool_key,
                artifact_id,
                error.__class__.__name__,
                str(error)[:1000],
                datetime.now().astimezone().isoformat(),
            ),
        )


def _upsert_session(
    connection: sqlite3.Connection,
    event,
    session_cache: dict[tuple[str, str], tuple[int, dict]],
) -> tuple[int, str, str | None]:
    payload = event.payload
    metadata = dict(payload.get("metadata") or {})
    for key in ("title", "model_key", "source_variant", "usage_status", "dialogue_status"):
        if payload.get(key) not in (None, ""):
            metadata[key] = payload[key]
    if payload.get("raw_locator"):
        metadata["raw_locator"] = payload["raw_locator"]
    occurred = event.occurred_at.isoformat()
    started_at = _event_timestamp(payload.get("started_at")) or occurred
    last_active_at = _event_timestamp(payload.get("last_active_at")) or occurred
    cwd = str(payload.get("initial_cwd") or metadata.get("cwd") or "").strip() or None
    agent_key = str(payload.get("agent_key") or metadata.get("agent_key") or "").strip() or None
    cache_key = (event.tool_key, event.external_session_key)
    cached = session_cache.get(cache_key)
    if cached is None:
        existing = connection.execute(
            "SELECT id, metadata_json FROM sessions WHERE tool_key = ? AND external_session_key = ?",
            (event.tool_key, event.external_session_key),
        ).fetchone()
        if existing is not None:
            try:
                prior_metadata = json.loads(existing["metadata_json"])
            except (TypeError, json.JSONDecodeError):
                prior_metadata = {}
            if isinstance(prior_metadata, dict):
                metadata = {**prior_metadata, **metadata}
    else:
        session_id, prior_metadata = cached
        metadata = {**prior_metadata, **metadata}
        existing = {"id": session_id}
    connection.execute(
        """
        INSERT INTO sessions(
            tool_key, external_session_key, started_at, last_active_at,
            initial_cwd, agent_key, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tool_key, external_session_key) DO UPDATE SET
            started_at=CASE
                WHEN sessions.started_at IS NULL OR excluded.started_at < sessions.started_at
                THEN excluded.started_at ELSE sessions.started_at END,
            last_active_at=CASE
                WHEN sessions.last_active_at IS NULL OR excluded.last_active_at > sessions.last_active_at
                THEN excluded.last_active_at ELSE sessions.last_active_at END,
            initial_cwd=COALESCE(sessions.initial_cwd, excluded.initial_cwd),
            agent_key=COALESCE(sessions.agent_key, excluded.agent_key),
            metadata_json=excluded.metadata_json
        """,
        (
            event.tool_key,
            event.external_session_key,
            started_at,
            last_active_at,
            cwd,
            agent_key,
            json.dumps(metadata, sort_keys=True),
        ),
    )
    if existing is None:
        session_id = connection.execute(
            "SELECT id FROM sessions WHERE tool_key = ? AND external_session_key = ?",
            (event.tool_key, event.external_session_key),
        ).fetchone()["id"]
    else:
        session_id = existing["id"]
    session_cache[cache_key] = (int(session_id), metadata)
    return int(session_id), last_active_at, cwd


def _write_event(
    connection: sqlite3.Connection,
    event,
    artifact_id: int,
    session_cache: dict[tuple[str, str], tuple[int, dict]],
    event_date: date,
) -> None:
    payload = event.payload
    session_id, session_last_active, cwd = _upsert_session(connection, event, session_cache)
    occurred = event.occurred_at.isoformat()
    day = event_date.isoformat()
    metadata = payload.get("metadata") or {}
    if event.event_type == "session":
        _write_activity_evidence(
            connection,
            session_id=session_id,
            occurred_at=session_last_active,
            business_date=day,
            cwd=cwd,
            artifact_id=artifact_id,
            raw_locator=payload.get("raw_locator") or {},
            confidence=str(payload.get("workspace_confidence") or "high"),
        )
        return
    if event.event_type != "usage":
        raise ValueError(f"unsupported normalized event type: {event.event_type}")
    explicit_protocol_total = payload.get("protocol_total_tokens")
    protocol_total = (
        int(explicit_protocol_total)
        if explicit_protocol_total is not None
        else payload["input_tokens"] + payload["output_tokens"] + payload["cache_read_tokens"]
    )
    connection.execute(
        """
        INSERT INTO usage_events(
            tool_key, session_id, external_event_key, occurred_at, business_date, model_key,
            input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
            reasoning_tokens, protocol_total_tokens, message_count, source_artifact_id,
            raw_locator_json, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tool_key, external_event_key) DO UPDATE SET
            session_id=excluded.session_id,
            occurred_at=excluded.occurred_at,
            business_date=excluded.business_date,
            model_key=excluded.model_key,
            input_tokens=excluded.input_tokens,
            output_tokens=excluded.output_tokens,
            cache_read_tokens=excluded.cache_read_tokens,
            cache_write_tokens=excluded.cache_write_tokens,
            reasoning_tokens=excluded.reasoning_tokens,
            protocol_total_tokens=excluded.protocol_total_tokens,
            message_count=excluded.message_count,
            source_artifact_id=excluded.source_artifact_id,
            raw_locator_json=excluded.raw_locator_json,
            metadata_json=excluded.metadata_json
        """,
        (
            event.tool_key,
            session_id,
            event.external_event_key,
            occurred,
            day,
            payload["model_key"],
            payload["input_tokens"],
            payload["output_tokens"],
            payload["cache_read_tokens"],
            payload["cache_write_tokens"],
            payload["reasoning_tokens"],
            protocol_total,
            payload["message_count"],
            artifact_id,
            json.dumps(payload["raw_locator"], sort_keys=True),
            json.dumps(metadata, sort_keys=True),
        ),
    )
    _write_activity_evidence(
        connection,
        session_id=session_id,
        occurred_at=occurred,
        business_date=day,
        cwd=cwd,
        artifact_id=artifact_id,
        raw_locator=payload.get("raw_locator") or {},
        confidence="high",
    )


def _write_activity_evidence(
    connection: sqlite3.Connection,
    *,
    session_id: int,
    occurred_at: str,
    business_date: str,
    cwd: str | None,
    artifact_id: int,
    raw_locator: dict,
    confidence: str,
) -> None:
    if not cwd or not Path(cwd).is_absolute():
        return
    normalized = str(Path(cwd).expanduser().absolute())
    connection.execute(
        """
        INSERT OR IGNORE INTO activity_evidence(
            session_id, occurred_at, business_date, evidence_type,
            observed_path, normalized_path, source_artifact_id,
            raw_locator_json, confidence
        ) VALUES (?, ?, ?, 'cwd', ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            occurred_at,
            business_date,
            cwd,
            normalized,
            artifact_id,
            json.dumps(raw_locator, sort_keys=True),
            confidence if confidence in {"high", "medium", "low", "fallback"} else "low",
        ),
    )


def _event_timestamp(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _delete_artifact_dates(
    connection: sqlite3.Connection,
    artifact_id: int,
    business_dates: Iterable[str],
) -> None:
    dates = sorted(set(business_dates))
    for offset in range(0, len(dates), 500):
        chunk = dates[offset : offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        parameters = (artifact_id, *chunk)
        connection.execute(
            f"DELETE FROM usage_events WHERE source_artifact_id = ? AND business_date IN ({placeholders})",
            parameters,
        )
        connection.execute(
            f"DELETE FROM activity_evidence WHERE source_artifact_id = ? AND business_date IN ({placeholders})",
            parameters,
        )


def _complete_artifact(
    connection: sqlite3.Connection,
    adapter: UsageAdapter,
    artifact,
    artifact_id: int,
    run_id: int,
    cursor: Cursor,
) -> None:
    stat = artifact.path.stat()
    if cursor.managed:
        expected = cursor.source_stat_at_open
        actual = (
            int(stat.st_dev),
            int(stat.st_ino),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
        )
        if expected is None:
            raise RuntimeError("incremental adapter did not publish its source snapshot")
        if actual[:2] != expected[:2]:
            raise RuntimeError("source artifact was replaced during incremental ingestion")
        if actual[2] < expected[2]:
            raise RuntimeError("source artifact was truncated during incremental ingestion")
        if actual != expected:
            raise RuntimeError("source artifact changed during incremental ingestion")
    cursor_json = (
        json.dumps(cursor.next_value, sort_keys=True, separators=(",", ":"))
        if cursor.managed and cursor.next_value is not None
        else None
    )
    connection.execute(
        """
        UPDATE source_artifacts
        SET artifact_type = ?, fingerprint = ?, byte_size = ?, modified_at = ?,
            cursor_json = CASE WHEN ? IS NULL THEN cursor_json ELSE ? END,
            last_ingestion_run_id = ?, status = 'ingested'
        WHERE id = ?
        """,
        (
            artifact.artifact_type,
            adapter.fingerprint(artifact),
            stat.st_size,
            datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            cursor_json,
            cursor_json,
            run_id,
            artifact_id,
        ),
    )


def _ingest_artifact(
    paths: RuntimePaths,
    adapter: UsageAdapter,
    artifact,
    state: _ArtifactState,
    run_id: int,
    selected_dates: set[date],
    timezone,
) -> tuple[int, int, set[date]]:
    requested = frozenset(value.isoformat() for value in selected_dates)
    policy = {
        "adapterVersion": adapter.adapter_version,
        "artifactType": artifact.artifact_type,
        "businessDayStartHour": DEFAULT_BUSINESS_DAY_START_HOUR,
        "parser": f"{adapter.__class__.__module__}.{adapter.__class__.__qualname__}",
        "timezone": getattr(timezone, "key", str(timezone)),
    }
    policy_hash = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    cursor = Cursor(
        value=state.cursor,
        requested_business_dates=requested,
        policy_hash=policy_hash,
    )
    events_seen = events_in_window = 0
    refresh_dates: set[date] = set()
    session_cache: dict[tuple[str, str], tuple[int, dict]] = {}
    sentinel = object()

    with connect(paths) as connection:
        iterator = iter(adapter.read_incremental(artifact, cursor))
        try:
            first_event = next(iterator)
        except StopIteration:
            first_event = sentinel

        if not cursor.managed:
            # Whole-JSON, SQLite, and local-runtime adapters intentionally do
            # not claim an append cursor. Rebuild the requested window every
            # time so source deletion/shrinkage cannot leave stale facts.
            _delete_artifact_dates(connection, state.artifact_id, requested)
            refresh_dates.update(selected_dates)
        elif cursor.reset_required:
            _delete_artifact_dates(connection, state.artifact_id, cursor.reset_business_dates)
            refresh_dates.update(date.fromisoformat(value) for value in cursor.reset_business_dates)

        def write(event) -> None:
            nonlocal events_seen, events_in_window
            events_seen += 1
            event_date = business_date_for(event.occurred_at, tz=timezone)
            event_day = event_date.isoformat()
            cursor.observe_business_date(event_day)
            if cursor.managed:
                if event_day not in cursor.write_business_dates:
                    return
            elif event_date not in selected_dates:
                return
            _write_event(
                connection,
                event,
                state.artifact_id,
                session_cache,
                event_date,
            )
            refresh_dates.add(event_date)
            if event_date in selected_dates:
                events_in_window += 1

        if first_event is not sentinel:
            write(first_event)
        for event in iterator:
            write(event)

        if cursor.managed and cursor.next_value is None:
            raise RuntimeError("incremental adapter did not publish a completed cursor")
        _complete_artifact(connection, adapter, artifact, state.artifact_id, run_id, cursor)

    logical_window_count = cursor.covered_event_count(requested) if cursor.managed else events_in_window
    return events_seen, logical_window_count, refresh_dates


def run_shadow_ingestion(
    paths: RuntimePaths,
    business_date: date,
    *,
    adapters: Iterable[UsageAdapter] | None = None,
    trigger: str = "shadow-manual",
    observe_assets: bool = True,
) -> ShadowIngestionResult:
    return run_shadow_period_ingestion(
        paths,
        business_date,
        business_date,
        adapters=adapters,
        trigger=trigger,
        observe_assets=observe_assets,
    )


def run_shadow_period_ingestion(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    adapters: Iterable[UsageAdapter] | None = None,
    trigger: str = "shadow-period",
    observe_assets: bool = True,
) -> ShadowIngestionResult:
    """Ingest one inclusive business-date interval with a single source scan."""
    with _exclusive_ingestion_guard(paths):
        return _run_shadow_period_ingestion_locked(
            paths,
            start_date,
            end_date,
            adapters=adapters,
            trigger=trigger,
            observe_assets=observe_assets,
        )


def _run_shadow_period_ingestion_locked(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    adapters: Iterable[UsageAdapter] | None,
    trigger: str,
    observe_assets: bool,
) -> ShadowIngestionResult:
    dates = _business_dates(start_date, end_date)
    selected_dates = set(dates)
    timezone = resolve_timezone(paths)
    migrate(paths)
    seed_projects_from_registry(paths)
    selected = tuple(adapters or default_usage_adapters(paths))
    registry = ToolRegistry(paths)
    for adapter in selected:
        registry.register(
            tool_key=adapter.tool_key,
            display_name=DISPLAY_NAMES.get(adapter.tool_key, adapter.tool_key),
            adapter_version=adapter.adapter_version,
            capabilities=adapter.capabilities,
            enabled=True,
        )
    run_id = begin_ingestion_run(
        paths,
        trigger_type=trigger,
        business_date=end_date,
        adapter_versions={adapter.tool_key: adapter.adapter_version for adapter in selected},
    )
    artifacts_seen = events_seen = events_in_window = errors = 0
    refresh_dates = set(selected_dates)
    try:
        for adapter in selected:
            for artifact in adapter.discover_sources():
                artifacts_seen += 1
                artifact_id = None
                try:
                    state = _upsert_artifact(paths, adapter, artifact, run_id)
                    artifact_id = state.artifact_id
                    artifact_events, artifact_window, artifact_refresh_dates = _ingest_artifact(
                        paths,
                        adapter,
                        artifact,
                        state,
                        run_id,
                        selected_dates,
                        timezone,
                    )
                    events_seen += artifact_events
                    events_in_window += artifact_window
                    refresh_dates.update(artifact_refresh_dates)
                except Exception as error:
                    errors += 1
                    _write_error(paths, run_id, adapter.tool_key, artifact_id, error)
        for business_date in sorted(refresh_dates):
            refresh_daily_usage(paths, business_date, run_id)
        if observe_assets:
            observe_non_rag_assets(paths, end_date, run_id)
        finish_ingestion_run(paths, run_id, status="completed" if not errors else "completed_with_errors")
    except Exception as error:
        finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        raise
    return ShadowIngestionResult(run_id, end_date, artifacts_seen, events_seen, events_in_window, errors)

"""Audit records for future foundation ingestion and projection jobs."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Iterable

from .db import connect
from .paths import RuntimePaths


def begin_ingestion_run(
    paths: RuntimePaths,
    *,
    trigger_type: str,
    business_date: date | None,
    adapter_versions: dict[str, Any] | None = None,
    status: str = "running",
) -> int:
    with connect(paths) as connection:
        cursor = connection.execute(
            """
            INSERT INTO ingestion_runs(
                trigger_type, business_date, started_at, status, adapter_versions_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                trigger_type,
                business_date.isoformat() if business_date else None,
                datetime.now().astimezone().isoformat(),
                status,
                json.dumps(adapter_versions or {}, sort_keys=True),
            ),
        )
        return int(cursor.lastrowid)


def finish_ingestion_run(paths: RuntimePaths, run_id: int, *, status: str, error_summary: str | None = None) -> None:
    with connect(paths) as connection:
        connection.execute(
            "UPDATE ingestion_runs SET completed_at = ?, status = ?, error_summary = ? WHERE id = ?",
            (datetime.now().astimezone().isoformat(), status, error_summary, run_id),
        )


def set_ingestion_run_status(paths: RuntimePaths, run_id: int, *, status: str) -> None:
    with connect(paths) as connection:
        connection.execute("UPDATE ingestion_runs SET status = ? WHERE id = ?", (status, run_id))


def update_ingestion_run_metadata(paths: RuntimePaths, run_id: int, metadata: dict[str, Any]) -> None:
    with connect(paths) as connection:
        row = connection.execute(
            "SELECT adapter_versions_json FROM ingestion_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return
        try:
            current = json.loads(row["adapter_versions_json"] or "{}")
        except json.JSONDecodeError:
            current = {}
        if not isinstance(current, dict):
            current = {}
        current.update(metadata)
        connection.execute(
            "UPDATE ingestion_runs SET adapter_versions_json = ? WHERE id = ?",
            (json.dumps(current, sort_keys=True), run_id),
        )


def ingestion_run_status(paths: RuntimePaths, run_id: int) -> dict | None:
    with connect(paths, read_only=True) as connection:
        row = connection.execute(
            """
            SELECT id, trigger_type, business_date, started_at, completed_at, status, adapter_versions_json, error_summary
            FROM ingestion_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    return _run_dict(row) if row is not None else None


def list_ingestion_runs(
    paths: RuntimePaths,
    *,
    trigger_types: Iterable[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    selected = list(trigger_types or [])
    limit = max(1, min(int(limit), 100))
    where = ""
    params: list[object] = []
    if selected:
        where = f"WHERE trigger_type IN ({','.join('?' for _ in selected)})"
        params.extend(selected)
    params.append(limit)
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            f"""
            SELECT id, trigger_type, business_date, started_at, completed_at, status, adapter_versions_json, error_summary
            FROM ingestion_runs
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_run_dict(row) for row in rows]


def _run_dict(row) -> dict:
    result = dict(row)
    raw_metadata = result.pop("adapter_versions_json", "{}")
    try:
        metadata = json.loads(raw_metadata or "{}")
    except json.JSONDecodeError:
        metadata = {}
    result["metadata"] = metadata if isinstance(metadata, dict) else {}
    return result

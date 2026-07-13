"""Audit records for controlled Foundation repair actions."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from .db import connect, migrate
from .paths import RuntimePaths

ACTIVE_STATUSES = ("queued", "running")


def digest_text(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_repair_run(
    paths: RuntimePaths,
    *,
    action_id: str,
    action_class: str,
    business_date: date,
    lock_key: str,
    command_digest: str,
    confirmation_digest: str | None = None,
    qa_before: dict | None = None,
) -> int:
    migrate(paths)
    with connect(paths) as connection:
        cursor = connection.execute(
            """
            INSERT INTO foundation_repair_runs(
                action_id, action_class, business_date, requested_at, status,
                lock_key, command_digest, confirmation_digest, qa_before_json
            ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                action_id,
                action_class,
                business_date.isoformat(),
                datetime.now().astimezone().isoformat(),
                lock_key,
                command_digest,
                confirmation_digest,
                json.dumps(qa_before or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        return int(cursor.lastrowid)


def mark_repair_run_running(paths: RuntimePaths, run_id: int) -> None:
    with connect(paths) as connection:
        connection.execute(
            "UPDATE foundation_repair_runs SET started_at = ?, status = 'running' WHERE id = ?",
            (datetime.now().astimezone().isoformat(), run_id),
        )


def finish_repair_run(
    paths: RuntimePaths,
    run_id: int,
    *,
    status: str,
    exit_code: int | None = None,
    stdout_tail: str | None = None,
    stderr_tail: str | None = None,
    error_summary: str | None = None,
    qa_after: dict | None = None,
) -> None:
    with connect(paths) as connection:
        connection.execute(
            """
            UPDATE foundation_repair_runs
            SET completed_at = ?, status = ?, exit_code = ?, stdout_tail = ?,
                stderr_tail = ?, error_summary = ?, qa_after_json = ?
            WHERE id = ?
            """,
            (
                datetime.now().astimezone().isoformat(),
                status,
                exit_code,
                stdout_tail,
                stderr_tail,
                error_summary,
                json.dumps(qa_after or {}, ensure_ascii=False, sort_keys=True),
                run_id,
            ),
        )


def get_repair_run(paths: RuntimePaths, run_id: int) -> dict | None:
    migrate(paths)
    with connect(paths, read_only=True) as connection:
        row = connection.execute(
            """
            SELECT id, action_id, action_class, business_date, requested_at,
                   started_at, completed_at, status, exit_code, lock_key,
                   command_digest, confirmation_digest, stdout_tail, stderr_tail,
                   error_summary, qa_before_json, qa_after_json
            FROM foundation_repair_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    return _run_dict(row) if row is not None else None


def list_repair_runs(paths: RuntimePaths, *, limit: int = 20) -> list[dict]:
    migrate(paths)
    limit = max(1, min(int(limit), 100))
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT id, action_id, action_class, business_date, requested_at,
                   started_at, completed_at, status, exit_code, lock_key,
                   command_digest, confirmation_digest, stdout_tail, stderr_tail,
                   error_summary, qa_before_json, qa_after_json
            FROM foundation_repair_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_run_dict(row) for row in rows]


def find_active_repair_run(paths: RuntimePaths, *, action_id: str, business_date: date) -> dict | None:
    migrate(paths)
    with connect(paths, read_only=True) as connection:
        row = connection.execute(
            """
            SELECT id, action_id, action_class, business_date, requested_at,
                   started_at, completed_at, status, exit_code, lock_key,
                   command_digest, confirmation_digest, stdout_tail, stderr_tail,
                   error_summary, qa_before_json, qa_after_json
            FROM foundation_repair_runs
            WHERE action_id = ?
              AND business_date = ?
              AND status IN ('queued', 'running')
            ORDER BY id DESC
            LIMIT 1
            """,
            (action_id, business_date.isoformat()),
        ).fetchone()
    return _run_dict(row) if row is not None else None


def _decode_json(value: str | None) -> dict:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_dict(row) -> dict:
    result = dict(row)
    result["qaBefore"] = _decode_json(result.pop("qa_before_json", None))
    result["qaAfter"] = _decode_json(result.pop("qa_after_json", None))
    return result

"""Materialized period projections for Dashboard compatibility reads."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Callable

from .db import connect
from .paths import RuntimePaths
from .snapshots import TOOL_DISPLAY
from .time import parse_timestamp, resolve_timezone
from .usage_attribution import WORKSPACE_USAGE_MIN_TOKENS, usage_group_display_allowed

LEGACY_ASSET_PROJECTION = "legacy-dashboard-assets-v1"


def build_foundation_period_asset_projection(
    paths: RuntimePaths,
    start_date: date,
    days: int,
) -> dict:
    """Build one period projection from already-materialized Foundation facts.

    The daily pipeline has populated ``daily_*_usage`` and ``usage_events``
    before this function runs.  Reopening every raw usage source for the
    current week and then again for the current month therefore adds no
    authority; it only repeats source discovery, cache loading, and Python
    aggregation.  Keep the diary/topic portion of the established Dashboard
    contract, but derive usage breakdowns from the date-indexed Foundation
    read models.
    """

    if days <= 0:
        raise ValueError("period days must be positive")
    end_date = start_date + timedelta(days=days - 1)
    usage_breakdown = _foundation_period_usage_breakdown(paths, start_date, end_date)
    diary_rollup = _foundation_period_diary_rollup(paths, start_date, end_date)
    diary_rollup["hourlyHeatmap"] = usage_breakdown["assetHourlyHeatmap"]
    period_memory = (diary_rollup.get("knowledgePeriod") or {}).get("memory") or {}
    memory_stats = {
        "sessionFiles": int(period_memory.get("currentCount") or 0),
        "totalSizeMB": round(float(period_memory.get("currentSizeMB") or 0), 1),
    }
    return {
        **diary_rollup,
        **usage_breakdown,
        "memoryStats": memory_stats,
        "knowledgePeriodMemoryCurrent": dict(memory_stats),
    }


def _foundation_period_diary_rollup(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
) -> dict:
    """Build period diary metrics from indexed facts and structured Markdown."""

    from .diary_markdown import build_diary_period_page_payload

    start = start_date.isoformat()
    end = end_date.isoformat()
    with connect(paths, read_only=True) as connection:
        tool_rows = connection.execute(
            """
            SELECT business_date, tool_key, tokens, messages, sessions, api_calls
            FROM daily_tool_usage
            WHERE business_date BETWEEN ? AND ?
            ORDER BY business_date, tool_key
            """,
            (start, end),
        ).fetchall()
        event_rows = connection.execute(
            """
            SELECT business_date,
                   SUM(input_tokens) AS input_tokens,
                   SUM(cache_read_tokens) AS cache_read_tokens,
                   SUM(cache_write_tokens) AS cache_write_tokens
            FROM usage_events
            WHERE business_date BETWEEN ? AND ?
            GROUP BY business_date
            """,
            (start, end),
        ).fetchall()
        model_rows = connection.execute(
            """
            SELECT model_key, SUM(tokens) AS tokens
            FROM daily_model_usage
            WHERE business_date BETWEEN ? AND ?
            GROUP BY model_key
            ORDER BY tokens DESC, model_key
            LIMIT 8
            """,
            (start, end),
        ).fetchall()
        embedded_rows = connection.execute(
            """
            SELECT business_date, embedded_json
            FROM diary_markdown_documents
            WHERE business_date BETWEEN ? AND ?
              AND report_type = 'narrative' AND status = 'ready'
            ORDER BY business_date
            """,
            (start, end),
        ).fetchall()

    total_days = (end_date - start_date).days + 1
    per_day: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tokens": 0, "messages": 0, "sessions": 0, "api_calls": 0}
    )
    agent_activity: dict[str, dict[str, object]] = defaultdict(
        lambda: {"tokens": 0, "messages": 0, "days": set()}
    )
    for row in tool_rows:
        business_date = str(row["business_date"])
        tool_key = str(row["tool_key"] or "unknown")
        per_day[business_date]["tokens"] += int(row["tokens"] or 0)
        per_day[business_date]["messages"] += int(row["messages"] or 0)
        per_day[business_date]["sessions"] += int(row["sessions"] or 0)
        per_day[business_date]["api_calls"] += int(row["api_calls"] or 0)
        agent_activity[tool_key]["tokens"] = int(agent_activity[tool_key]["tokens"]) + int(row["tokens"] or 0)
        agent_activity[tool_key]["messages"] = int(agent_activity[tool_key]["messages"]) + int(row["messages"] or 0)
        if int(row["tokens"] or 0) or int(row["messages"] or 0) or int(row["sessions"] or 0):
            cast_days = agent_activity[tool_key]["days"]
            if isinstance(cast_days, set):
                cast_days.add(business_date)

    event_by_day = {
        str(row["business_date"]): {
            "input": int(row["input_tokens"] or 0),
            "cache_read": int(row["cache_read_tokens"] or 0),
            "cache_write": int(row["cache_write_tokens"] or 0),
        }
        for row in event_rows
    }
    daily_series = []
    current = start_date
    while current <= end_date:
        day = current.isoformat()
        values = per_day[day]
        event = event_by_day.get(day, {})
        cache = int(event.get("cache_read") or 0) + int(event.get("cache_write") or 0)
        denominator = int(event.get("input") or 0) + cache
        daily_series.append(
            {
                "date": day,
                "displayDate": current.strftime("%m-%d"),
                "tokens": values["tokens"],
                "messages": values["messages"],
                "cacheHitRate": round(cache / denominator * 100, 1) if denominator else 0,
            }
        )
        current += timedelta(days=1)

    page = build_diary_period_page_payload(paths, start_date, end_date)
    topic_counter = Counter(
        str(item.get("title") or "").replace("**", "").strip()
        for item in page.get("summaryTopics") or []
        if str(item.get("title") or "").strip()
    )
    cron_success = 0
    cron_failed = 0
    latest_rag_snapshot: dict = {}
    latest_memory_snapshot: dict = {}
    for row in embedded_rows:
        try:
            embedded = json.loads(row["embedded_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        tasks = embedded.get("cronTasks") if isinstance(embedded, dict) else None
        if isinstance(embedded, dict) and isinstance(embedded.get("ragStats"), dict):
            latest_rag_snapshot = embedded["ragStats"]
        if isinstance(embedded, dict) and isinstance(embedded.get("memoryStats"), dict):
            latest_memory_snapshot = embedded["memoryStats"]
        for task in tasks if isinstance(tasks, list) else ():
            status = str(task.get("status") or "") if isinstance(task, dict) else ""
            if "成功" in status or "✅" in status or status.casefold() in {"success", "passed", "ok"}:
                cron_success += 1
            elif status:
                cron_failed += 1

    total_input = sum(int(row.get("input") or 0) for row in event_by_day.values())
    total_cache_read = sum(int(row.get("cache_read") or 0) for row in event_by_day.values())
    total_cache_write = sum(int(row.get("cache_write") or 0) for row in event_by_day.values())
    total_cache = total_cache_read + total_cache_write
    cron_total = cron_success + cron_failed
    agent_activity_out = {
        agent: {
            "messages": int(values["messages"]),
            "tokens": int(values["tokens"]),
            "days_active": len(values["days"]) if isinstance(values["days"], set) else 0,
            "total_days": total_days,
            "active_rate": round(
                (len(values["days"]) if isinstance(values["days"], set) else 0)
                / total_days
                * 100,
                1,
            ),
        }
        for agent, values in agent_activity.items()
    }
    return {
        "parsedDays": len(
            {str(row["business_date"]) for row in embedded_rows}
        ),
        "kpi": {
            "totalTokens": sum(item["tokens"] for item in per_day.values()),
            "totalMessages": sum(item["messages"] for item in per_day.values()),
            "totalApiCalls": sum(item["api_calls"] for item in per_day.values()),
            "activeSessions": sum(item["sessions"] for item in per_day.values()),
            "totalSessions": sum(item["sessions"] for item in per_day.values()),
            "cacheHitRate": round(total_cache / (total_input + total_cache) * 100, 1)
            if total_input + total_cache
            else 0,
            "cronSuccessRate": round(cron_success / cron_total * 100, 1) if cron_total else 0,
            "agentCount": len(agent_activity_out),
        },
        "dailyTokenSeries": daily_series,
        "modelUsage": [
            {"model": str(row["model_key"] or "unknown"), "tokens": int(row["tokens"] or 0)}
            for row in model_rows
        ],
        "agentActivity": agent_activity_out,
        "cronStats": {
            "success": cron_success,
            "failed": cron_failed,
            "rate": round(cron_success / cron_total * 100, 1) if cron_total else 0,
        },
        "topTopics": [
            {"topic": topic, "count": count}
            for topic, count in topic_counter.most_common(20)
        ],
        "knowledgePeriod": {
            "rag": {
                "currentCount": int(latest_rag_snapshot.get("entries") or 0),
                "currentSizeMB": round(float(latest_rag_snapshot.get("sizeMB") or 0), 1),
                "deltaAvailable": False,
            },
            "memory": {
                "currentCount": int(latest_memory_snapshot.get("sessionFiles") or 0),
                "currentSizeMB": round(float(latest_memory_snapshot.get("totalSizeMB") or 0), 1),
                "deltaAvailable": False,
            },
        },
    }


def _foundation_period_usage_breakdown(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
) -> dict:
    start = start_date.isoformat()
    end = end_date.isoformat()
    with connect(paths, read_only=True) as connection:
        workspace_rows = connection.execute(
            """
            SELECT d.project_id_or_bucket, d.tool_key,
                   COALESCE(p.canonical_name, '') AS canonical_name,
                   SUM(d.tokens) AS tokens,
                   SUM(d.messages) AS messages,
                   SUM(d.active_sessions) AS sessions,
                   COUNT(DISTINCT d.business_date) AS active_days
            FROM daily_project_usage d
            LEFT JOIN projects p
              ON d.project_id_or_bucket = ('project:' || p.id)
            WHERE d.business_date BETWEEN ? AND ?
            GROUP BY d.project_id_or_bucket, d.tool_key, p.canonical_name
            ORDER BY tokens DESC, d.project_id_or_bucket, d.tool_key
            """,
            (start, end),
        ).fetchall()
        model_rows = connection.execute(
            """
            SELECT model_key, SUM(tokens) AS tokens, SUM(messages) AS messages,
                   SUM(sessions) AS sessions
            FROM daily_model_usage
            WHERE business_date BETWEEN ? AND ?
            GROUP BY model_key
            ORDER BY tokens DESC, model_key
            LIMIT 10
            """,
            (start, end),
        ).fetchall()
        event_rows = connection.execute(
            """
            SELECT business_date, occurred_at, protocol_total_tokens
            FROM usage_events
            WHERE business_date BETWEEN ? AND ?
            ORDER BY business_date, occurred_at
            """,
            (start, end),
        ).fetchall()

    total_days = (end_date - start_date).days + 1
    workspace_usage = []
    for row in workspace_rows:
        tool_name, emoji = TOOL_DISPLAY.get(
            str(row["tool_key"] or ""),
            (str(row["tool_key"] or ""), ""),
        )
        bucket = str(row["project_id_or_bucket"] or "")
        name = str(row["canonical_name"] or "")
        if not name:
            name = (
                f"{tool_name} unattributed"
                if bucket == "unattributed"
                else bucket.removeprefix("project:")
            )
        tokens = int(row["tokens"] or 0)
        if tokens < WORKSPACE_USAGE_MIN_TOKENS:
            continue
        if not usage_group_display_allowed(name, tool_name):
            continue
        workspace_usage.append(
            {
                "name": name,
                "tool": tool_name,
                "emoji": emoji,
                "tokens": tokens,
                "messages": int(row["messages"] or 0),
                "sessions": int(row["sessions"] or 0),
                "days_active": int(row["active_days"] or 0),
                "total_days": total_days,
                "active_rate": round(
                    int(row["active_days"] or 0) / total_days * 100,
                    1,
                ),
                "attribution": bucket,
            }
        )

    models = [
        {
            "name": str(row["model_key"] or "unknown"),
            "tokens": int(row["tokens"] or 0),
            "messages": int(row["messages"] or 0),
            "sessions": int(row["sessions"] or 0),
        }
        for row in model_rows
    ]
    dates = [
        (start_date + timedelta(days=index)).isoformat()
        for index in range(total_days)
    ]
    indices = {value: index for index, value in enumerate(dates)}
    slots = {label: [0] * total_days for label in ("上午", "下午", "晚上", "凌晨")}
    timezone = resolve_timezone(paths)
    for row in event_rows:
        index = indices.get(str(row["business_date"] or ""))
        parsed = parse_timestamp(str(row["occurred_at"] or ""))
        if index is None or parsed is None:
            continue
        hour = parsed.astimezone(timezone).hour
        label = (
            "上午"
            if 4 <= hour < 12
            else "下午"
            if 12 <= hour < 18
            else "晚上"
            if 18 <= hour < 24
            else "凌晨"
        )
        slots[label][index] += int(row["protocol_total_tokens"] or 0)

    return {
        "workspaceUsage": workspace_usage,
        "workspaceAttributionQa": {
            "source": "foundation-daily-project-usage",
            "rowCount": len(workspace_usage),
        },
        "models": models,
        "assetHourlyHeatmap": {
            "dates": dates,
            "periods": [
                {"label": label, "values": slots[label]}
                for label in ("上午", "下午", "晚上", "凌晨")
            ],
        },
    }


def _report_key(projection_type: str, start_date: date, end_date: date) -> str:
    return f"{projection_type}:{start_date.isoformat()}:{end_date.isoformat()}"


def _period_type(start_date: date, end_date: date) -> str:
    days = (end_date - start_date).days + 1
    if start_date.day == 1:
        return "month"
    if start_date.weekday() == 0 and 1 <= days <= 7:
        return "week"
    if days == 7:
        return "week"
    return "custom"


def write_period_projection(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    metrics: dict,
    *,
    source_run_id: int | None,
    projection_type: str = LEGACY_ASSET_PROJECTION,
    status: str = "ready",
) -> str:
    period_type = _period_type(start_date, end_date)
    report_key = _report_key(projection_type, start_date, end_date)
    with connect(paths) as connection:
        connection.execute(
            """
            INSERT INTO period_reports(
                report_key, period_type, start_date, end_date, projection_type,
                metrics_json, generated_at, source_run_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_key) DO UPDATE SET
                period_type=excluded.period_type,
                metrics_json=excluded.metrics_json,
                generated_at=excluded.generated_at,
                source_run_id=excluded.source_run_id,
                status=excluded.status
            """,
            (
                report_key,
                period_type,
                start_date.isoformat(),
                end_date.isoformat(),
                projection_type,
                json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                datetime.now().astimezone().isoformat(),
                source_run_id,
                status,
            ),
        )
    return report_key


def read_period_projection(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    projection_type: str = LEGACY_ASSET_PROJECTION,
) -> dict | None:
    with connect(paths, read_only=True) as connection:
        row = connection.execute(
            """
            SELECT metrics_json, generated_at, status, source_run_id
            FROM period_reports
            WHERE report_key = ? AND status = 'ready'
            """,
            (_report_key(projection_type, start_date, end_date),),
        ).fetchone()
    if row is None:
        return None
    return {
        "metrics": json.loads(row["metrics_json"]),
        "generatedAt": row["generated_at"],
        "status": row["status"],
        "sourceRunId": row["source_run_id"],
        "projectionType": projection_type,
    }


def materialize_legacy_asset_projection(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    source_run_id: int,
    *,
    builder: Callable[[date, int], dict] | None = None,
) -> str:
    """Snapshot an indexed Foundation period projection outside request handling."""
    if builder is None:
        builder = lambda selected_start, days: build_foundation_period_asset_projection(
            paths,
            selected_start,
            days,
        )
    days = (end_date - start_date).days + 1
    metrics = builder(start_date, days)
    return write_period_projection(paths, start_date, end_date, metrics, source_run_id=source_run_id)

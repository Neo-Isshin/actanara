"""Durable, secret-safe LLM attribution for pipeline runs.

The ledger records one row per logical LLM call. Provider retry/fallback attempts
are bounded structured metadata; prompts, request/response bodies, headers, and
credentials are deliberately excluded from the persistence contract.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Mapping

from .db import connect, migrate
from .paths import RuntimePaths


USAGE_SOURCES = {"response", "estimated", "unavailable"}
PIPELINE_RUN_ID_ENV = "ACTANARA_PIPELINE_RUN_ID"
PIPELINE_STAGE_ID_ENV = "ACTANARA_PIPELINE_STAGE_ID"
TOKEN_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "reasoningTokens",
    "totalTokens",
)
_TOKEN_COLUMNS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadTokens": "cache_read_tokens",
    "cacheWriteTokens": "cache_write_tokens",
    "reasoningTokens": "reasoning_tokens",
    "totalTokens": "total_tokens",
}
_USAGE_ALIASES = {
    "inputTokens": ("inputTokens", "input_tokens", "input"),
    "outputTokens": ("outputTokens", "output_tokens", "output"),
    "cacheReadTokens": ("cacheReadTokens", "cache_read_tokens", "cacheRead"),
    "cacheWriteTokens": ("cacheWriteTokens", "cache_write_tokens", "cacheWrite"),
    "reasoningTokens": ("reasoningTokens", "reasoning_tokens", "reasoning"),
    "totalTokens": ("totalTokens", "total_tokens", "total"),
}
_ATTEMPT_FIELDS = {
    "provider",
    "providerId",
    "model",
    "api",
    "apiType",
    "attemptIndex",
    "retryIndex",
    "fallbackIndex",
    "status",
    "failureClass",
    "errorSummary",
    "httpStatus",
    "startedAt",
    "completedAt",
    "durationMs",
}
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(?:^|[_-])(?:prompt|system|messages?|headers?|authorization|api[_-]?key|password|"
    r"secret|cookie|bearer|token|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"request[_-]?body|response[_-]?body)(?:$|[_-])"
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|password|secret|cookie|token|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token)\b[\"']?\s*[:=]\s*[\"']?"
    r"(?:bearer\s+)?[^\s,;|\"']+"
)
_BEARER_VALUE_RE = re.compile(r"(?i)\bbearer\s+[^\s,;|]+")


def pipeline_llm_attribution_context(
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Resolve optional parent-provided run/stage attribution from the environment."""

    selected = os.environ if environment is None else environment
    raw_run_id = str(selected.get(PIPELINE_RUN_ID_ENV) or "").strip()
    raw_stage_id = str(selected.get(PIPELINE_STAGE_ID_ENV) or "").strip()
    if not raw_run_id and not raw_stage_id:
        return None
    if not raw_run_id or not raw_stage_id:
        return None
    try:
        run_id = _non_negative_int(raw_run_id, field=PIPELINE_RUN_ID_ENV, allow_zero=False)
        stage_id = _required_text(raw_stage_id, field=PIPELINE_STAGE_ID_ENV, max_length=128)
    except ValueError:
        return None
    return {"pipelineRunId": run_id, "stageId": stage_id}


def record_pipeline_llm_call_from_environment(
    paths: RuntimePaths,
    *,
    environment: Mapping[str, str] | None = None,
    **call: Any,
) -> int | None:
    """Record a call when a pipeline parent supplied both attribution values."""

    context = pipeline_llm_attribution_context(environment)
    if context is None:
        return None
    return record_pipeline_llm_call(
        paths,
        pipeline_run_id=context["pipelineRunId"],
        stage_id=context["stageId"],
        **call,
    )


def record_pipeline_llm_call(
    paths: RuntimePaths,
    *,
    pipeline_run_id: int,
    stage_id: str,
    status: str,
    provider_id: str | None = None,
    model: str | None = None,
    api_type: str | None = None,
    call_id: str | None = None,
    pass_id: str | None = None,
    chunk_id: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
    usage: dict[str, Any] | None = None,
    usage_source: str | None = None,
    estimation_method: str | None = None,
    retry_count: int = 0,
    fallback_count: int = 0,
    failure_class: str | None = None,
    error_summary: str | None = None,
    attempts: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Append a logical LLM call without persisting request content or secrets."""

    migrate(paths)
    run_id = _non_negative_int(pipeline_run_id, field="pipeline_run_id", allow_zero=False)
    normalized_stage = _required_text(stage_id, field="stage_id", max_length=128)
    normalized_status = _required_text(status, field="status", max_length=64)
    normalized_call_id = _bounded_text(call_id or uuid.uuid4().hex, max_length=128)
    normalized_usage = _normalize_usage(usage)
    resolved_usage_source = str(usage_source or ("response" if _usage_available(normalized_usage) else "unavailable"))
    if resolved_usage_source not in USAGE_SOURCES:
        raise ValueError("usage_source must be response, estimated, or unavailable")
    if resolved_usage_source == "unavailable" and _usage_available(normalized_usage):
        raise ValueError("unavailable usage_source cannot include token values")
    if resolved_usage_source in {"response", "estimated"} and not _usage_available(normalized_usage):
        raise ValueError(f"{resolved_usage_source} usage_source requires at least one token value")
    normalized_method = _optional_text(estimation_method, max_length=240)
    if resolved_usage_source == "estimated" and not normalized_method:
        raise ValueError("estimated usage requires estimation_method")
    normalized_duration = _optional_non_negative_int(duration_ms, field="duration_ms")
    normalized_retry_count = _non_negative_int(retry_count, field="retry_count")
    normalized_fallback_count = _non_negative_int(fallback_count, field="fallback_count")
    now = datetime.now().astimezone().isoformat()
    safe_attempts = _sanitize_attempts(attempts or [])
    safe_metadata = _sanitize_mapping(metadata or {})

    try:
        with connect(paths) as connection:
            cursor = connection.execute(
                """
                INSERT INTO pipeline_llm_calls(
                    pipeline_run_id, stage_id, pass_id, call_id, chunk_id, status,
                    started_at, completed_at, duration_ms, provider_id, model, api_type,
                    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                    reasoning_tokens, total_tokens, usage_source, estimation_method,
                    retry_count, fallback_count, failure_class, error_summary,
                    attempts_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    normalized_stage,
                    _optional_text(pass_id, max_length=128),
                    normalized_call_id,
                    _optional_text(chunk_id, max_length=128),
                    normalized_status,
                    _optional_text(started_at, max_length=64),
                    _optional_text(completed_at, max_length=64),
                    normalized_duration,
                    _optional_text(provider_id, max_length=128),
                    _optional_text(model, max_length=256),
                    _optional_text(api_type, max_length=64),
                    *[normalized_usage[field] for field in TOKEN_FIELDS],
                    resolved_usage_source,
                    normalized_method,
                    normalized_retry_count,
                    normalized_fallback_count,
                    _optional_text(failure_class, max_length=128),
                    _sanitize_error(error_summary),
                    json.dumps(safe_attempts, ensure_ascii=False, sort_keys=True),
                    json.dumps(safe_metadata, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)
    except Exception as exc:
        if "FOREIGN KEY constraint failed" in str(exc):
            raise ValueError(f"pipeline run {run_id} does not exist") from None
        raise


def list_pipeline_llm_calls(
    paths: RuntimePaths,
    pipeline_run_id: int,
    *,
    stage_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return ordered, public-safe call records for one run or stage."""

    migrate(paths)
    run_id = _non_negative_int(pipeline_run_id, field="pipeline_run_id", allow_zero=False)
    params: list[Any] = [run_id]
    where = "pipeline_run_id = ?"
    if stage_id is not None:
        where += " AND stage_id = ?"
        params.append(str(stage_id))
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            f"SELECT * FROM pipeline_llm_calls WHERE {where} ORDER BY id",
            params,
        ).fetchall()
    return [_row_dict(row) for row in rows]


def aggregate_pipeline_llm_calls(
    paths: RuntimePaths,
    pipeline_run_id: int,
    *,
    stage_id: str | None = None,
) -> dict[str, Any]:
    """Aggregate call/token attribution while preserving unavailable semantics."""

    calls = list_pipeline_llm_calls(paths, pipeline_run_id, stage_id=stage_id)
    return _aggregate_call_records(calls, pipeline_run_id, stage_id=stage_id)


def _aggregate_call_records(
    calls: list[dict[str, Any]],
    pipeline_run_id: int,
    *,
    stage_id: str | None,
) -> dict[str, Any]:
    if not calls:
        return _empty_aggregate(pipeline_run_id, stage_id=stage_id)
    usage_sources = {str(call.get("usageSource") or "unavailable") for call in calls}
    available_fields = {
        field: any(call.get("usage", {}).get(field) is not None for call in calls)
        for field in TOKEN_FIELDS
    }
    tokens = {
        field: (
            sum(int(call.get("usage", {}).get(field) or 0) for call in calls)
            if available_fields[field]
            else None
        )
        for field in TOKEN_FIELDS
    }
    usage_available = any(available_fields.values())
    unavailable_calls = sum(1 for call in calls if call.get("usageSource") == "unavailable")
    if not usage_available:
        usage_status = "unavailable"
    elif unavailable_calls:
        usage_status = "partial"
    else:
        usage_status = "available"
    return {
        "pipelineRunId": int(pipeline_run_id),
        "stageId": stage_id,
        "callDataAvailable": True,
        "usageAvailable": usage_available,
        "usageStatus": usage_status,
        "estimated": "estimated" in usage_sources,
        "llmCallCount": len(calls),
        "retryCount": sum(int(call.get("retryCount") or 0) for call in calls),
        "fallbackCount": sum(int(call.get("fallbackCount") or 0) for call in calls),
        "failedCallCount": sum(1 for call in calls if call.get("status") == "failed"),
        "unavailableCallCount": unavailable_calls,
        "tokens": tokens,
        "providers": _provider_breakdown(calls),
    }


def pipeline_llm_attribution_by_stage(paths: RuntimePaths, pipeline_run_id: int) -> dict[str, Any]:
    """Return run totals plus ordered stage aggregates for Dashboard expansion."""

    calls = list_pipeline_llm_calls(paths, pipeline_run_id)
    stages: list[str] = []
    for call in calls:
        stage_id = str(call.get("stageId") or "")
        if stage_id and stage_id not in stages:
            stages.append(stage_id)
    return {
        "pipelineRunId": int(pipeline_run_id),
        "summary": _aggregate_call_records(calls, pipeline_run_id, stage_id=None),
        "stages": [
            {
                **_aggregate_call_records(
                    [call for call in calls if call.get("stageId") == stage_id],
                    pipeline_run_id,
                    stage_id=stage_id,
                ),
                "calls": [call for call in calls if call.get("stageId") == stage_id],
            }
            for stage_id in stages
        ],
    }


def _empty_aggregate(pipeline_run_id: int, *, stage_id: str | None) -> dict[str, Any]:
    return {
        "pipelineRunId": int(pipeline_run_id),
        "stageId": stage_id,
        "callDataAvailable": False,
        "usageAvailable": False,
        "usageStatus": "unavailable",
        "estimated": False,
        "llmCallCount": None,
        "retryCount": None,
        "fallbackCount": None,
        "failedCallCount": None,
        "unavailableCallCount": None,
        "tokens": {field: None for field in TOKEN_FIELDS},
        "providers": [],
    }


def _normalize_usage(usage: dict[str, Any] | None) -> dict[str, int | None]:
    source = usage if isinstance(usage, dict) else {}
    result: dict[str, int | None] = {}
    for field, aliases in _USAGE_ALIASES.items():
        raw = next((source[key] for key in aliases if key in source), None)
        result[field] = _optional_non_negative_int(raw, field=field)
    return result


def _usage_available(usage: dict[str, int | None]) -> bool:
    return any(usage.get(field) is not None for field in TOKEN_FIELDS)


def _provider_breakdown(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for call in calls:
        key = (str(call.get("providerId") or ""), str(call.get("model") or ""))
        item = grouped.setdefault(
            key,
            {"providerId": key[0], "model": key[1], "callCount": 0, "tokens": {field: None for field in TOKEN_FIELDS}},
        )
        item["callCount"] += 1
        for field in TOKEN_FIELDS:
            value = call.get("usage", {}).get(field)
            if value is not None:
                item["tokens"][field] = int(item["tokens"].get(field) or 0) + int(value)
    return list(grouped.values())


def _row_dict(row: Any) -> dict[str, Any]:
    raw = dict(row)
    usage = {field: raw.get(column) for field, column in _TOKEN_COLUMNS.items()}
    return {
        "id": int(raw["id"]),
        "pipelineRunId": int(raw["pipeline_run_id"]),
        "stageId": raw.get("stage_id"),
        "passId": raw.get("pass_id"),
        "callId": raw.get("call_id"),
        "chunkId": raw.get("chunk_id"),
        "status": raw.get("status"),
        "startedAt": raw.get("started_at"),
        "completedAt": raw.get("completed_at"),
        "durationMs": raw.get("duration_ms"),
        "providerId": raw.get("provider_id"),
        "model": raw.get("model"),
        "apiType": raw.get("api_type"),
        "usage": usage,
        "usageSource": raw.get("usage_source"),
        "estimationMethod": raw.get("estimation_method"),
        "retryCount": int(raw.get("retry_count") or 0),
        "fallbackCount": int(raw.get("fallback_count") or 0),
        "failureClass": raw.get("failure_class"),
        "errorSummary": raw.get("error_summary"),
        "attempts": _json_value(raw.get("attempts_json"), []),
        "metadata": _json_value(raw.get("metadata_json"), {}),
        "createdAt": raw.get("created_at"),
        "updatedAt": raw.get("updated_at"),
    }


def _sanitize_attempts(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for attempt in attempts[:100]:
        if not isinstance(attempt, dict):
            continue
        safe = {
            key: _sanitize_value(value)
            for key, value in attempt.items()
            if key in _ATTEMPT_FIELDS and not _is_sensitive_key(key)
        }
        if "errorSummary" in safe:
            safe["errorSummary"] = _sanitize_error(str(safe["errorSummary"] or ""))
        result.append(safe)
    return result


def _sanitize_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _sanitize_value(item)
        for key, item in value.items()
        if not _is_sensitive_key(str(key))
    }


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _sanitize_mapping(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:100]]
    if isinstance(value, str):
        return _sanitize_string(value, max_length=1000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_value(str(value))


def _sanitize_error(value: str | None) -> str | None:
    if value is None:
        return None
    sanitized = _sanitize_string(str(value), max_length=500)
    return sanitized.replace("\r", " ").replace("\n", " ")[:500]


def _sanitize_string(value: str, *, max_length: int) -> str:
    sanitized = _SENSITIVE_VALUE_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", str(value))
    sanitized = _BEARER_VALUE_RE.sub("Bearer [REDACTED]", sanitized)
    return sanitized[:max_length]


def _is_sensitive_key(value: str) -> bool:
    raw = str(value or "")
    if _SENSITIVE_KEY_RE.search(raw):
        return True
    compact = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    return any(
        marker in compact
        for marker in (
            "prompt",
            "header",
            "authorization",
            "apikey",
            "password",
            "secret",
            "cookie",
            "bearer",
            "accesstoken",
            "refreshtoken",
            "idtoken",
            "requestbody",
            "responsebody",
        )
    )


def _json_value(raw: str | None, default: Any) -> Any:
    try:
        value = json.loads(raw or "")
    except (json.JSONDecodeError, TypeError):
        return default
    return value if isinstance(value, type(default)) else default


def _required_text(value: Any, *, field: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be non-empty")
    return _bounded_text(normalized, max_length=max_length)


def _optional_text(value: Any, *, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return _bounded_text(normalized, max_length=max_length) if normalized else None


def _bounded_text(value: Any, *, max_length: int) -> str:
    return str(value)[:max_length]


def _optional_non_negative_int(value: Any, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    return _non_negative_int(value, field=field)


def _non_negative_int(value: Any, *, field: str, allow_zero: bool = True) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < 0 or (not allow_zero and parsed == 0):
        qualifier = "positive" if not allow_zero else "non-negative"
        raise ValueError(f"{field} must be {qualifier}")
    return parsed

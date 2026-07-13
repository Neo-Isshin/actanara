"""Additive Dashboard data-state envelopes shared by API surfaces."""

from __future__ import annotations

from typing import Any, Iterable


DASHBOARD_STATE_SCHEMA_VERSION = 1
DASHBOARD_STATE_VALUES = {"ready", "empty", "degraded", "unavailable", "error"}


def source_error(source: str, *, code: str = "source-read-failed", retryable: bool = True) -> dict[str, Any]:
    return {
        "source": str(source or "unknown"),
        "code": str(code or "source-read-failed"),
        "retryable": bool(retryable),
    }


def normalize_source_errors(items: Iterable[Any] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        source = item.get("source") or item.get("id") or "unknown"
        normalized.append(
            source_error(
                str(source),
                code=str(item.get("code") or "source-read-failed"),
                retryable=bool(item.get("retryable", True)),
            )
        )
    return normalized


def state_envelope(
    status: str,
    *,
    source_errors: Iterable[Any] | None = None,
) -> dict[str, Any]:
    selected = str(status or "error")
    if selected not in DASHBOARD_STATE_VALUES:
        raise ValueError("unsupported Dashboard state")
    return {
        "schemaVersion": DASHBOARD_STATE_SCHEMA_VERSION,
        "status": selected,
        "sourceErrors": normalize_source_errors(source_errors),
    }


def attach_dashboard_state(
    payload: dict[str, Any],
    *,
    empty: bool = False,
    source_errors: Iterable[Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    result = dict(payload)
    errors = normalize_source_errors(source_errors)
    selected = status or ("degraded" if errors else "empty" if empty else "ready")
    result["dashboardState"] = state_envelope(selected, source_errors=errors)
    return result


def dashboard_failure(
    source: str,
    *,
    status: str = "error",
    code: str = "source-read-failed",
    retryable: bool = True,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error = source_error(source, code=code, retryable=retryable)
    result = dict(fallback or {})
    result["error"] = f"{source} {status}"
    result["dashboardState"] = state_envelope(status, source_errors=[error])
    return result

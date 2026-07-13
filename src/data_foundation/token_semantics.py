"""Authoritative token accounting helpers."""

from __future__ import annotations

from typing import Any


PROTOCOL_TOTAL_FORMULA = "input + output + cacheRead"
PROMPT_TOTAL_FORMULA = "input + cacheRead + cacheWrite"
LEGACY_OPERATIONAL_TOTAL_FORMULA = "input + output + cacheRead + cacheWrite"


def protocol_total(values: dict[str, Any]) -> int:
    return int(values.get("input") or 0) + int(values.get("output") or 0) + int(values.get("cacheRead") or 0)


def normalize_cached_input_detail(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    reported_total_tokens: int | None = None,
) -> tuple[int, int, str]:
    """Return input/cache fields normalized for the protocol total formula."""
    input_value = int(input_tokens or 0)
    output_value = int(output_tokens or 0)
    cache_value = int(cache_read_tokens or 0)
    if reported_total_tokens is None:
        return input_value, cache_value, "input_excludes_cached_input"
    reported_total = int(reported_total_tokens or 0)
    if cache_value > 0 and reported_total == input_value + output_value:
        return max(input_value - cache_value, 0), cache_value, "input_includes_cached_input"
    return input_value, cache_value, "input_excludes_cached_input"


def prompt_total(values: dict[str, Any]) -> int:
    return int(values.get("input") or 0) + int(values.get("cacheRead") or 0) + int(values.get("cacheWrite") or 0)


def legacy_operational_total(values: dict[str, Any]) -> int:
    return protocol_total(values) + int(values.get("cacheWrite") or 0)


def cache_hit_rate(values: dict[str, Any]) -> float:
    input_tokens = int(values.get("input") or 0)
    cache_read = int(values.get("cacheRead") or 0)
    denom = input_tokens + cache_read
    return round(cache_read / denom * 100, 1) if denom > 0 else 0.0


def authoritative_semantics(*, scope: str, day_boundary: str, live: bool = False) -> dict[str, Any]:
    return {
        "source": "foundation-protocol",
        "legacyLive": False,
        "live": bool(live),
        "tokenTotalFormula": PROTOCOL_TOTAL_FORMULA,
        "foundationProtocolTotalFormula": PROTOCOL_TOTAL_FORMULA,
        "promptTokenFormula": PROMPT_TOTAL_FORMULA,
        "legacyOperationalTotalFormula": LEGACY_OPERATIONAL_TOTAL_FORMULA,
        "cacheRateFormula": "cacheRead / (input + cacheRead)",
        "dayBoundary": day_boundary,
        "scope": scope,
    }

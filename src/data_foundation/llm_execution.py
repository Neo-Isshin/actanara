"""Ordered, attributed execution for one logical LLM message.

This module deliberately owns provider selection and fallback only. Transport
payload repair/retry remains in :mod:`data_foundation.llm_transport`, while
provider persistence and secret resolution remain in settings.
"""

from __future__ import annotations

import re
import time
import urllib.error
import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable, Mapping

from .llm_transport import (
    LlmTransportError,
    LlmTransportResult,
    send_anthropic_message_detailed,
    send_openai_compatible_message_detailed,
)
from .paths import RuntimePaths, load_paths
from .pipeline_llm_attribution import record_pipeline_llm_call_from_environment
from .settings import resolve_llm_provider_chain


FALLBACK_FAILURE_CLASSES = {
    "auth",
    "rate_limit",
    "network",
    "timeout",
    "5xx",
    "content_parse",
}
SUPPORTED_API_TYPES = {"anthropic-messages", "openai-compatible"}

DetailedSender = Callable[..., LlmTransportResult]

_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|password|secret|cookie|token)\b"
    r"[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?[^\s,;|\"']+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;|]+")


class ProviderChainError(RuntimeError):
    """Public-safe terminal failure for one logical provider-chain call."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: str,
        retryable: bool,
        attempts: tuple[dict[str, Any], ...] = (),
        call_id: str,
        provider_id: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.retryable = bool(retryable)
        self.attempts = attempts
        self.call_id = call_id
        self.provider_id = provider_id
        self.model = model

    def to_dict(self) -> dict[str, Any]:
        return {
            "failureClass": self.failure_class,
            "retryable": self.retryable,
            "callId": self.call_id,
            "providerId": self.provider_id,
            "model": self.model,
            "message": str(self),
            "attempts": [dict(attempt) for attempt in self.attempts],
        }


def execute_llm_message(
    *,
    system: str,
    prompt: str,
    temperature: float,
    max_tokens: int | None = None,
    timeout: int | None = None,
    thinking_mode: str | None = None,
    paths: RuntimePaths | None = None,
    require_cross_process_secret: bool = True,
    environment: Mapping[str, str] | None = None,
    pass_id: str | None = None,
    chunk_id: str | None = None,
    label: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    sender: DetailedSender | None = None,
) -> LlmTransportResult:
    """Execute one logical call against an ordered, ready provider chain.

    ``sender`` is an optional test seam with the same keyword signature as the
    detailed transport senders. The returned text is available as
    ``result.text``; prompts, credentials, headers, and response bodies are
    never passed to the attribution ledger.
    """

    runtime_paths = paths or load_paths()
    call_id = uuid.uuid4().hex
    call_started = _now()
    started_clock = time.perf_counter()
    provider_attempts: list[dict[str, Any]] = []
    total_retry_count = 0
    selected_provider: dict[str, Any] | None = None

    try:
        chain = resolve_llm_provider_chain(
            runtime_paths,
            redact_secrets=False,
            require_cross_process_secret=bool(require_cross_process_secret),
        )
    except Exception as error:
        summary = _safe_error_summary(error, system=system, prompt=prompt)
        chain_error = ProviderChainError(
            "LLM provider chain configuration could not be resolved: " + summary,
            failure_class="config",
            retryable=False,
            call_id=call_id,
        )
        _record_failure(
            runtime_paths,
            environment=environment,
            call_id=call_id,
            pass_id=pass_id,
            chunk_id=chunk_id,
            label=label,
            metadata=metadata,
            started_at=call_started,
            started_clock=started_clock,
            error=chain_error,
            retry_count=0,
            fallback_count=0,
        )
        raise chain_error from None

    readiness_error = _provider_chain_readiness_error(
        chain,
        require_cross_process_secret=bool(require_cross_process_secret),
    )
    if readiness_error is not None:
        selected_provider, detail = readiness_error
        provider_attempts.append(
            _provider_attempt(
                selected_provider,
                attempt_index=0,
                status="not-ready",
                failure_class="config",
                error_summary=detail,
            )
        )
        chain_error = ProviderChainError(
            "LLM provider chain is not ready: " + detail,
            failure_class="config",
            retryable=False,
            attempts=tuple(provider_attempts),
            call_id=call_id,
            provider_id=_provider_id(selected_provider),
            model=_model(selected_provider),
        )
        _record_failure(
            runtime_paths,
            environment=environment,
            call_id=call_id,
            pass_id=pass_id,
            chunk_id=chunk_id,
            label=label,
            metadata=metadata,
            started_at=call_started,
            started_clock=started_clock,
            error=chain_error,
            retry_count=0,
            fallback_count=0,
            provider=selected_provider,
        )
        raise chain_error from None

    for attempt_index, provider in enumerate(chain):
        selected_provider = provider
        attempt_started = _now()
        attempt_clock = time.perf_counter()
        api_type = _api_type(provider)
        model = _model(provider)
        secrets = (str(provider.get("apiKey") or ""), system, prompt)
        try:
            selected_sender = sender or _sender_for_api(api_type)
            result = selected_sender(
                endpoint=str(provider.get("endpoint") or ""),
                api_key=str(provider.get("apiKey") or ""),
                model=model,
                system=system,
                prompt=prompt,
                temperature=temperature,
                max_tokens=_positive_int(max_tokens, provider.get("maxTokens"), 8192),
                timeout=_positive_int(timeout, provider.get("timeoutSeconds"), 300),
                thinking_mode=thinking_mode,
            )
            if not isinstance(result, LlmTransportResult):
                raise LlmTransportError(
                    "Detailed sender returned an invalid result type.",
                    failure_class="request",
                    retryable=False,
                    api_type=api_type,
                    model=model,
                )
            if not str(result.text or "").strip():
                raise LlmTransportError(
                    "Detailed sender returned empty content after repair attempts.",
                    failure_class="content_parse",
                    retryable=False,
                    api_type=api_type,
                    model=model,
                )
        except Exception as error:
            failure = _normalized_transport_failure(
                error,
                api_type=api_type,
                model=model,
                secrets=secrets,
            )
            retry_count = _transport_retry_count(failure)
            total_retry_count += retry_count
            provider_attempts.append(
                _provider_attempt(
                    provider,
                    attempt_index=attempt_index,
                    status="failed",
                    failure_class=failure.failure_class,
                    error_summary=str(failure),
                    http_status=failure.status_code,
                    retry_count=retry_count,
                    started_at=attempt_started,
                    duration_ms=_duration_ms(attempt_clock),
                )
            )
            may_fallback = failure.failure_class in FALLBACK_FAILURE_CLASSES
            if may_fallback and attempt_index + 1 < len(chain):
                continue
            chain_error = ProviderChainError(
                "LLM provider chain failed after "
                f"{len(provider_attempts)} provider attempt(s); final failure: {failure.failure_class}.",
                failure_class=failure.failure_class,
                retryable=may_fallback,
                attempts=tuple(provider_attempts),
                call_id=call_id,
                provider_id=_provider_id(provider),
                model=model,
            )
            _record_failure(
                runtime_paths,
                environment=environment,
                call_id=call_id,
                pass_id=pass_id,
                chunk_id=chunk_id,
                label=label,
                metadata=metadata,
                started_at=call_started,
                started_clock=started_clock,
                error=chain_error,
                retry_count=total_retry_count,
                fallback_count=attempt_index,
                provider=provider,
            )
            raise chain_error from None

        total_retry_count += result.retry_count
        provider_attempts.append(
            _provider_attempt(
                provider,
                attempt_index=attempt_index,
                status="completed",
                retry_count=result.retry_count,
                started_at=attempt_started,
                duration_ms=_duration_ms(attempt_clock),
            )
        )
        normalized_result = replace(result, api_type=api_type, model=model)
        completed_at = _now()
        record_pipeline_llm_call_from_environment(
            runtime_paths,
            environment=environment,
            status="completed",
            provider_id=_provider_id(provider),
            model=model,
            api_type=api_type,
            call_id=call_id,
            pass_id=pass_id,
            chunk_id=chunk_id,
            started_at=call_started,
            completed_at=completed_at,
            duration_ms=_duration_ms(started_clock),
            usage=_attribution_usage(normalized_result),
            usage_source="estimated" if normalized_result.usage.estimated else "response",
            estimation_method=(
                normalized_result.usage.method if normalized_result.usage.estimated else None
            ),
            retry_count=total_retry_count,
            fallback_count=attempt_index,
            attempts=provider_attempts,
            metadata=_attribution_metadata(
                label=label,
                provider=provider,
                metadata=metadata,
            ),
        )
        return normalized_result

    # The readiness gate rejects empty chains, so this is defensive only.
    raise AssertionError("provider chain execution ended without a result")


def _provider_chain_readiness_error(
    chain: Any,
    *,
    require_cross_process_secret: bool,
) -> tuple[dict[str, Any], str] | None:
    if not isinstance(chain, list) or not chain:
        return {}, "provider chain is empty"
    for index, raw_provider in enumerate(chain):
        if not isinstance(raw_provider, dict):
            return {}, f"provider entry {index + 1} is invalid"
        provider = raw_provider
        readiness = provider.get("readiness") if isinstance(provider.get("readiness"), dict) else {}
        missing = [
            field
            for field in ("endpoint", "model", "apiKey")
            if not str(provider.get(field) or "").strip()
        ]
        api_type = _api_type(provider)
        secret_ref = provider.get("secretRef") if isinstance(provider.get("secretRef"), dict) else {}
        detail = ""
        if readiness.get("ready") is False:
            detail = str(readiness.get("error") or readiness.get("status") or "not ready")
        elif missing:
            detail = "missing " + ", ".join(missing)
        elif api_type not in SUPPORTED_API_TYPES:
            detail = f"unsupported API transport: {api_type or 'unset'}"
        elif require_cross_process_secret and str(secret_ref.get("backend") or "") == "memory":
            detail = "process-local memory secrets cannot be used by pipeline subprocesses"
        if detail:
            entry_id = str(provider.get("entryId") or f"provider-{index + 1}")
            return provider, f"provider entry {entry_id} is not ready: {detail}"
    return None


def _sender_for_api(api_type: str) -> DetailedSender:
    if api_type == "anthropic-messages":
        return send_anthropic_message_detailed
    if api_type == "openai-compatible":
        return send_openai_compatible_message_detailed
    raise ValueError(f"unsupported API transport: {api_type or 'unset'}")


def _normalized_transport_failure(
    error: Exception,
    *,
    api_type: str,
    model: str,
    secrets: tuple[str, ...],
) -> LlmTransportError:
    if isinstance(error, LlmTransportError):
        return LlmTransportError(
            _safe_error_summary(error, secrets=secrets),
            failure_class=error.failure_class,
            retryable=error.retryable,
            status_code=error.status_code,
            attempts=error.attempts,
            api_type=api_type,
            model=model,
        )
    if isinstance(error, (TimeoutError,)):
        failure_class = "timeout"
        retryable = True
    elif isinstance(error, (urllib.error.URLError, ConnectionError, OSError)):
        failure_class = "network"
        retryable = True
    else:
        failure_class = "request"
        retryable = False
    return LlmTransportError(
        _safe_error_summary(error, secrets=secrets),
        failure_class=failure_class,
        retryable=retryable,
        api_type=api_type,
        model=model,
    )


def _transport_retry_count(error: LlmTransportError) -> int:
    return sum(1 for attempt in error.attempts if attempt.retry_index > 0)


def _provider_attempt(
    provider: Mapping[str, Any],
    *,
    attempt_index: int,
    status: str,
    failure_class: str | None = None,
    error_summary: str | None = None,
    http_status: int | None = None,
    retry_count: int = 0,
    started_at: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    completed_at = _now() if started_at else None
    return {
        "provider": str(provider.get("provider") or ""),
        "providerId": str(provider.get("entryId") or provider.get("provider") or ""),
        "model": _model(provider),
        "apiType": _api_type(provider),
        "attemptIndex": attempt_index,
        "retryIndex": retry_count,
        "fallbackIndex": attempt_index,
        "status": status,
        "failureClass": failure_class,
        "errorSummary": error_summary,
        "httpStatus": http_status,
        "startedAt": started_at,
        "completedAt": completed_at,
        "durationMs": duration_ms,
    }


def _record_failure(
    paths: RuntimePaths,
    *,
    environment: Mapping[str, str] | None,
    call_id: str,
    pass_id: str | None,
    chunk_id: str | None,
    label: str | None,
    metadata: Mapping[str, Any] | None,
    started_at: str,
    started_clock: float,
    error: ProviderChainError,
    retry_count: int,
    fallback_count: int,
    provider: Mapping[str, Any] | None = None,
) -> None:
    record_pipeline_llm_call_from_environment(
        paths,
        environment=environment,
        status="failed",
        provider_id=_provider_id(provider or {}),
        model=_model(provider or {}) or None,
        api_type=_api_type(provider or {}) or None,
        call_id=call_id,
        pass_id=pass_id,
        chunk_id=chunk_id,
        started_at=started_at,
        completed_at=_now(),
        duration_ms=_duration_ms(started_clock),
        usage_source="unavailable",
        retry_count=retry_count,
        fallback_count=fallback_count,
        failure_class=error.failure_class,
        error_summary=str(error),
        attempts=list(error.attempts),
        metadata=_attribution_metadata(label=label, provider=provider or {}, metadata=metadata),
    )


def _attribution_usage(result: LlmTransportResult) -> dict[str, int | None]:
    usage = result.usage
    return {
        "inputTokens": usage.input_tokens,
        "outputTokens": usage.output_tokens,
        "cacheReadTokens": usage.cache_read_tokens,
        "cacheWriteTokens": usage.cache_write_tokens,
        "reasoningTokens": usage.reasoning_tokens,
        "totalTokens": usage.total_tokens,
    }


def _attribution_metadata(
    *,
    label: str | None,
    provider: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        **dict(metadata or {}),
        **({"label": label} if label else {}),
        "providerEntryId": str(provider.get("entryId") or ""),
    }


def _safe_error_summary(
    error: BaseException,
    *,
    secrets: tuple[str, ...] = (),
    system: str = "",
    prompt: str = "",
) -> str:
    value = str(error).replace("\r", " ").replace("\n", " ")
    for secret in (*secrets, system, prompt):
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = _SENSITIVE_VALUE_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    return _BEARER_RE.sub("Bearer [REDACTED]", value)[:500]


def _provider_id(provider: Mapping[str, Any]) -> str | None:
    value = str(provider.get("provider") or provider.get("entryId") or "").strip()
    return value or None


def _model(provider: Mapping[str, Any]) -> str:
    return str(provider.get("model") or "")


def _api_type(provider: Mapping[str, Any]) -> str:
    return str(provider.get("api") or "")


def _positive_int(*values: Any) -> int:
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 1


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _duration_ms(started_clock: float) -> int:
    return max(0, int((time.perf_counter() - started_clock) * 1000))

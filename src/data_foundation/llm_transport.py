import json
from dataclasses import dataclass
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Callable


ANTHROPIC_VERSION = "2023-06-01"


class _ResponseParseError(ValueError):
    pass


@dataclass(frozen=True)
class LlmUsage:
    """Normalized token usage without retaining request or response content."""

    input_tokens: int | None
    output_tokens: int | None
    cache_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    reported_total_tokens: int | None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    estimated: bool = False
    source: str = "provider_response"
    method: str = "provider-reported"
    estimated_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cacheTokens": self.cache_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "totalTokens": self.total_tokens,
            "reportedTotalTokens": self.reported_total_tokens,
            "cacheReadTokens": self.cache_read_tokens,
            "cacheWriteTokens": self.cache_write_tokens,
            "estimated": self.estimated,
            "source": self.source,
            "method": self.method,
            "estimatedFields": list(self.estimated_fields),
        }


@dataclass(frozen=True)
class LlmTransportAttempt:
    variant: str
    retry_index: int
    status: str
    failure_class: str | None = None
    status_code: int | None = None
    retryable: bool = False
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "retryIndex": self.retry_index,
            "status": self.status,
            "failureClass": self.failure_class,
            "statusCode": self.status_code,
            "retryable": self.retryable,
            "message": self.message,
        }


@dataclass(frozen=True)
class LlmTransportResult:
    text: str
    usage: LlmUsage
    api_type: str
    model: str
    payload_variant: str
    attempts: tuple[LlmTransportAttempt, ...]
    response_id: str | None = None

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def retry_count(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.retry_index > 0)

    @property
    def payload_variant_count(self) -> int:
        return len({attempt.variant for attempt in self.attempts})

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "usage": self.usage.to_dict(),
            "apiType": self.api_type,
            "model": self.model,
            "payloadVariant": self.payload_variant,
            "attemptCount": self.attempt_count,
            "retryCount": self.retry_count,
            "payloadVariantCount": self.payload_variant_count,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "responseId": self.response_id,
        }


class LlmTransportError(RuntimeError):
    """Public-safe typed transport failure for retry/fallback decisions."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: str,
        retryable: bool,
        status_code: int | None = None,
        attempts: tuple[LlmTransportAttempt, ...] = (),
        api_type: str = "unknown",
        model: str = "",
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.retryable = bool(retryable)
        self.status_code = status_code
        self.attempts = attempts
        self.api_type = api_type
        self.model = model

    def to_dict(self) -> dict[str, Any]:
        return {
            "failureClass": self.failure_class,
            "retryable": self.retryable,
            "statusCode": self.status_code,
            "apiType": self.api_type,
            "model": self.model,
            "message": str(self),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


def anthropic_messages_url(endpoint: str) -> str:
    base = str(endpoint or "").strip().rstrip("/")
    if not base:
        raise ValueError("Anthropic Messages endpoint is required")
    if base.endswith("/v1/messages") or base.endswith("/messages"):
        return base
    if base.endswith("/anthropic/v1"):
        return base + "/messages"
    if base.endswith("/anthropic"):
        return base + "/v1/messages"
    if base.endswith("/coding"):
        return base + "/v1/messages"
    if base.endswith("/v1"):
        base = base[:-3]
    return base + "/anthropic/v1/messages"


def anthropic_messages_payload(
    model: str,
    system: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    thinking_mode: str | None = None,
    include_temperature: bool = True,
) -> dict:
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    if include_temperature:
        payload["temperature"] = temperature
    if str(thinking_mode or "").lower() in {"off", "disabled", "disable"}:
        payload["thinking"] = {"type": "disabled"}
    return payload


def parse_anthropic_text(result: dict) -> str:
    blocks = result.get("content") or []
    text_parts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
    if text_parts:
        return "".join(text_parts)
    raise KeyError("Anthropic response missing text content block")


def send_anthropic_message(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    system: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    thinking_mode: str | None = None,
) -> str:
    return send_anthropic_message_detailed(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        system=system,
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        thinking_mode=thinking_mode,
    ).text


def send_anthropic_message_detailed(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    system: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    thinking_mode: str | None = None,
) -> LlmTransportResult:
    variants = [
        ("full", thinking_mode, True, max_tokens),
        ("no-thinking", None, True, max_tokens),
        ("no-thinking-no-temperature", None, False, max_tokens),
        ("reduced-output-budget", None, False, _reduced_max_tokens(max_tokens)),
    ]
    url = _configured_url(anthropic_messages_url, endpoint, api_type="anthropic", model=model)
    return _send_with_fallback_detailed(
        url=url,
        headers={
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        },
        timeout=timeout,
        variants=[
            (
                name,
                anthropic_messages_payload(model, system, prompt, temperature, budget, mode, include_temperature=temp),
            )
            for name, mode, temp, budget in variants
        ],
        parse=parse_anthropic_text,
        parse_usage=lambda result: _anthropic_usage(result, system=system, prompt=prompt),
        api_type="anthropic",
        model=model,
    )


def openai_chat_completions_url(endpoint: str) -> str:
    base = str(endpoint or "").strip().rstrip("/")
    if not base:
        raise ValueError("OpenAI-compatible endpoint is required")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith(("/v1", "/v2", "/v3", "/v4")):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def openai_chat_completions_payload(
    model: str,
    system: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    thinking_mode: str | None = None,
    include_temperature: bool = True,
) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
    }
    if include_temperature:
        payload["temperature"] = temperature
    mode = str(thinking_mode or "").lower()
    if mode in {"low", "medium", "high"}:
        payload["reasoning_effort"] = mode
    elif mode in {"off", "disabled", "disable"}:
        payload["reasoning_effort"] = "low"
    return payload


def parse_openai_chat_text(result: dict) -> str:
    choices = result.get("choices") or []
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        if isinstance(message, dict):
            content = str(message.get("content") or "").strip()
            if content:
                return content
    raise KeyError("OpenAI-compatible response missing message content")


def send_openai_compatible_message(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    system: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    thinking_mode: str | None = None,
) -> str:
    return send_openai_compatible_message_detailed(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        system=system,
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        thinking_mode=thinking_mode,
    ).text


def send_openai_compatible_message_detailed(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    system: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    thinking_mode: str | None = None,
) -> LlmTransportResult:
    variants = [
        ("full", thinking_mode, True, max_tokens),
        ("no-reasoning-effort", None, True, max_tokens),
        ("no-reasoning-no-temperature", None, False, max_tokens),
        ("reduced-output-budget", None, False, _reduced_max_tokens(max_tokens)),
    ]
    url = _configured_url(openai_chat_completions_url, endpoint, api_type="openai-compatible", model=model)
    return _send_with_fallback_detailed(
        url=url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
        variants=[
            (
                name,
                openai_chat_completions_payload(model, system, prompt, temperature, budget, mode, include_temperature=temp),
            )
            for name, mode, temp, budget in variants
        ],
        parse=parse_openai_chat_text,
        parse_usage=lambda result: _openai_usage(result, system=system, prompt=prompt),
        api_type="openai-compatible",
        model=model,
    )


def _reduced_max_tokens(max_tokens: int) -> int:
    try:
        parsed = int(max_tokens)
    except (TypeError, ValueError):
        parsed = 4096
    if parsed <= 1024:
        return max(1, parsed)
    return max(1024, parsed // 2)


def _configured_url(builder: Callable[[str], str], endpoint: str, *, api_type: str, model: str) -> str:
    try:
        return builder(endpoint)
    except Exception as error:
        detail = _sanitize_transport_error(str(error))
        raise LlmTransportError(
            "LLM request configuration failed: " + detail,
            failure_class="config",
            retryable=False,
            api_type=api_type,
            model=model,
        ) from None


def _send_with_fallback(*, url: str, headers: dict, timeout: int, variants: list[tuple[str, dict]], parse) -> str:
    """Compatibility wrapper for the former private string-returning helper."""
    return _send_with_fallback_detailed(
        url=url,
        headers=headers,
        timeout=timeout,
        variants=variants,
        parse=parse,
        parse_usage=lambda result: _estimated_usage("", "", parse(result)),
        api_type="unknown",
        model="",
    ).text


def _send_with_fallback_detailed(
    *,
    url: str,
    headers: dict,
    timeout: int,
    variants: list[tuple[str, dict]],
    parse: Callable[[dict], str],
    parse_usage: Callable[[dict], LlmUsage],
    api_type: str,
    model: str,
) -> LlmTransportResult:
    attempts: list[LlmTransportAttempt] = []
    secret_values = _transport_secret_values(headers)
    for name, payload in _dedupe_variants(variants):
        for retry_index in range(_retry_count_for_variant(name)):
            try:
                result = _post_json(url=url, headers=headers, payload=payload, timeout=timeout)
            except urllib.error.HTTPError as error:
                detail = _sanitize_transport_error(_http_error_detail(error), secret_values=secret_values)
                failure_class = _http_failure_class(error.code)
                retryable = _http_status_retryable(error.code)
                attempts.append(
                    LlmTransportAttempt(
                        variant=name,
                        retry_index=retry_index,
                        status="failed",
                        failure_class=failure_class,
                        status_code=error.code,
                        retryable=retryable,
                        message=(f"HTTP {error.code} {detail}").strip(),
                    )
                )
                if error.code in {400, 422}:
                    break
                if _should_retry_same_variant(error.code) and retry_index + 1 < _retry_count_for_variant(name):
                    _sleep_before_retry(retry_index)
                    continue
                break
            except (TimeoutError, socket.timeout, urllib.error.URLError) as error:
                detail = _sanitize_transport_error(str(error), secret_values=secret_values)
                failure_class = "timeout" if _is_timeout_error(error) else "network"
                attempts.append(
                    LlmTransportAttempt(
                        variant=name,
                        retry_index=retry_index,
                        status="failed",
                        failure_class=failure_class,
                        retryable=True,
                        message=(f"{error.__class__.__name__} {detail}").strip(),
                    )
                )
                if retry_index + 1 < _retry_count_for_variant(name):
                    _sleep_before_retry(retry_index)
                    continue
                break
            except Exception as error:
                detail = _sanitize_transport_error(str(error), secret_values=secret_values)
                failure_class = _exception_failure_class(error)
                attempts.append(
                    LlmTransportAttempt(
                        variant=name,
                        retry_index=retry_index,
                        status="failed",
                        failure_class=failure_class,
                        retryable=failure_class in {"timeout", "network", "5xx"},
                        message=(f"{error.__class__.__name__} {detail}").strip(),
                    )
                )
                break
            else:
                try:
                    text = parse(result)
                except Exception as error:
                    detail = _sanitize_transport_error(str(error), secret_values=secret_values)
                    attempts.append(
                        LlmTransportAttempt(
                            variant=name,
                            retry_index=retry_index,
                            status="failed",
                            failure_class="content_parse",
                            retryable=False,
                            message=(f"{error.__class__.__name__} {detail}").strip(),
                        )
                    )
                    break
                attempts.append(
                    LlmTransportAttempt(
                        variant=name,
                        retry_index=retry_index,
                        status="success",
                    )
                )
                return LlmTransportResult(
                    text=text,
                    usage=parse_usage(result),
                    api_type=api_type,
                    model=model,
                    payload_variant=name,
                    attempts=tuple(attempts),
                    response_id=str(result.get("id") or "") or None,
                )
    recent = attempts[-8:]
    detail = " | ".join(
        f"{attempt.variant}: {attempt.message or attempt.failure_class or 'failed'}"
        for attempt in recent
    )
    final = attempts[-1] if attempts else LlmTransportAttempt("unknown", 0, "failed", "request")
    raise LlmTransportError(
        "LLM request failed after fallback attempts: " + detail,
        failure_class=final.failure_class or "request",
        retryable=final.retryable,
        status_code=final.status_code,
        attempts=tuple(attempts),
        api_type=api_type,
        model=model,
    )


def _dedupe_variants(variants: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    seen = set()
    result = []
    for name, payload in variants:
        marker = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if marker in seen:
            continue
        seen.add(marker)
        result.append((name, payload))
    return result


def _retry_count_for_variant(name: str) -> int:
    return 2 if name == "full" else 1


def _sleep_before_retry(retry_index: int) -> None:
    time.sleep([2, 8][min(retry_index, 1)])


def _post_json(*, url: str, headers: dict, payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        result = json.loads(resp.read())
        if not isinstance(result, dict):
            raise _ResponseParseError("LLM response JSON must be an object")
        return result


def _http_status_retryable(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or 500 <= status_code <= 599


def _should_retry_same_variant(status_code: int) -> bool:
    """Keep the existing narrow in-provider retry policy."""
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


def _http_failure_class(status_code: int) -> str:
    if status_code in {401, 403, 407}:
        return "auth"
    if status_code == 429:
        return "rate_limit"
    if status_code == 408:
        return "timeout"
    if 500 <= status_code <= 599:
        return "5xx"
    return "request"


def _is_timeout_error(error: BaseException) -> bool:
    current: object = error
    seen: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, socket.timeout)):
            return True
        if "timed out" in str(current).casefold() or "timeout" in str(current).casefold():
            return True
        current = getattr(current, "reason", None) or getattr(current, "__cause__", None)
    return False


def _exception_failure_class(error: BaseException) -> str:
    if _is_timeout_error(error):
        return "timeout"
    if isinstance(error, (_ResponseParseError, json.JSONDecodeError, UnicodeDecodeError, KeyError)):
        return "content_parse"
    if isinstance(error, (ssl.SSLError, ConnectionError, OSError)):
        return "network"
    return "request"


def _openai_usage(result: dict, *, system: str, prompt: str) -> LlmUsage:
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    input_details = _first_dict(usage, "prompt_tokens_details", "input_tokens_details")
    output_details = _first_dict(usage, "completion_tokens_details", "output_tokens_details")
    input_tokens = _first_token_count(usage, "prompt_tokens", "input_tokens")
    output_tokens = _first_token_count(usage, "completion_tokens", "output_tokens")
    cache_read = _first_token_count(
        input_details,
        "cached_tokens",
    )
    if cache_read is None:
        cache_read = _first_token_count(usage, "cached_tokens", "cache_read_input_tokens")
    cache_write = _first_token_count(usage, "cache_creation_input_tokens", "cache_write_input_tokens")
    cache_tokens = _first_token_count(usage, "cache_tokens")
    if cache_tokens is None:
        cache_tokens = _sum_optional(cache_read, cache_write)
    reasoning_tokens = _first_token_count(output_details, "reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = _first_token_count(usage, "reasoning_tokens")
    reported_total = _first_token_count(usage, "total_tokens")
    if not _has_any_usage(input_tokens, output_tokens, cache_tokens, reasoning_tokens, reported_total):
        return _estimated_usage(system, prompt, parse_openai_chat_text(result))
    total_tokens = reported_total
    method = "provider-reported-total"
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
        method = "provider-input-plus-output"
    elif total_tokens is None:
        method = "provider-partial-usage"
    return LlmUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_tokens=cache_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        reported_total_tokens=reported_total,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        source="provider_response",
        method=method,
    )


def _anthropic_usage(result: dict, *, system: str, prompt: str) -> LlmUsage:
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    input_tokens = _first_token_count(usage, "input_tokens", "prompt_tokens")
    output_tokens = _first_token_count(usage, "output_tokens", "completion_tokens")
    cache_read = _first_token_count(usage, "cache_read_input_tokens", "cached_tokens")
    cache_write = _first_token_count(usage, "cache_creation_input_tokens", "cache_write_input_tokens")
    cache_tokens = _first_token_count(usage, "cache_tokens")
    if cache_tokens is None:
        cache_tokens = _sum_optional(cache_read, cache_write)
    output_details = _first_dict(usage, "output_tokens_details", "completion_tokens_details")
    reasoning_tokens = _first_token_count(output_details, "reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = _first_token_count(usage, "reasoning_tokens", "thinking_tokens")
    reported_total = _first_token_count(usage, "total_tokens")
    if not _has_any_usage(input_tokens, output_tokens, cache_tokens, reasoning_tokens, reported_total):
        return _estimated_usage(system, prompt, parse_anthropic_text(result))
    total_tokens = reported_total
    method = "provider-reported-total"
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens + (cache_tokens or 0)
        method = "provider-input-plus-output-plus-cache"
    elif total_tokens is None:
        method = "provider-partial-usage"
    return LlmUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_tokens=cache_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        reported_total_tokens=reported_total,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        source="provider_response",
        method=method,
    )


def _estimated_usage(system: str, prompt: str, output: str) -> LlmUsage:
    input_tokens = _estimate_text_tokens(system) + _estimate_text_tokens(prompt)
    if system or prompt:
        input_tokens += 8
    output_tokens = _estimate_text_tokens(output)
    return LlmUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_tokens=None,
        reasoning_tokens=None,
        total_tokens=input_tokens + output_tokens,
        reported_total_tokens=None,
        estimated=True,
        source="local_estimate",
        method="utf8-bytes-divided-by-4-plus-message-overhead-v1",
        estimated_fields=("input_tokens", "output_tokens", "total_tokens"),
    )


def _estimate_text_tokens(value: str) -> int:
    encoded_size = len(str(value or "").encode("utf-8"))
    return (encoded_size + 3) // 4 if encoded_size else 0


def _first_dict(source: dict, *keys: str) -> dict:
    for key in keys:
        value = source.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _first_token_count(source: dict, *keys: str) -> int | None:
    for key in keys:
        value = _token_count(source.get(key))
        if value is not None:
            return value
    return None


def _token_count(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 and str(value).strip() not in {"", "nan", "inf", "-inf"} else None


def _sum_optional(*values: int | None) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _has_any_usage(*values: int | None) -> bool:
    return any(value is not None for value in values)


def _http_error_detail(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    return body[:240].replace("\n", " ")


def _transport_secret_values(headers: dict) -> tuple[str, ...]:
    values = []
    for key, raw_value in (headers or {}).items():
        lowered = str(key).casefold().replace("_", "-")
        if lowered not in {"authorization", "x-api-key", "api-key"}:
            continue
        value = str(raw_value or "")
        if value:
            values.append(value)
            if value.casefold().startswith("bearer "):
                values.append(value[7:])
    return tuple(sorted(set(values), key=len, reverse=True))


def _sanitize_transport_error(value: str, *, secret_values: tuple[str, ...] = ()) -> str:
    sanitized = str(value or "")
    for secret in secret_values:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = re.sub(
        r"(?i)\b(api[_-]?key|authorization|bearer|password|secret|token)\b[\"']?\s*[:=]\s*[\"']?[^\s,;|\"']+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        sanitized,
    )
    return sanitized[:240].replace("\n", " ")

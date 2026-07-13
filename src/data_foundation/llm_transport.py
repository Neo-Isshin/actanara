import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.request


ANTHROPIC_VERSION = "2023-06-01"


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
    variants = [
        ("full", thinking_mode, True, max_tokens),
        ("no-thinking", None, True, max_tokens),
        ("no-thinking-no-temperature", None, False, max_tokens),
        ("reduced-output-budget", None, False, _reduced_max_tokens(max_tokens)),
    ]
    return _send_with_fallback(
        url=anthropic_messages_url(endpoint),
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
    variants = [
        ("full", thinking_mode, True, max_tokens),
        ("no-reasoning-effort", None, True, max_tokens),
        ("no-reasoning-no-temperature", None, False, max_tokens),
        ("reduced-output-budget", None, False, _reduced_max_tokens(max_tokens)),
    ]
    return _send_with_fallback(
        url=openai_chat_completions_url(endpoint),
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
    )


def _reduced_max_tokens(max_tokens: int) -> int:
    try:
        parsed = int(max_tokens)
    except (TypeError, ValueError):
        parsed = 4096
    if parsed <= 1024:
        return max(1, parsed)
    return max(1024, parsed // 2)


def _send_with_fallback(*, url: str, headers: dict, timeout: int, variants: list[tuple[str, dict]], parse) -> str:
    attempts = []
    secret_values = _transport_secret_values(headers)
    for name, payload in _dedupe_variants(variants):
        for retry_index in range(_retry_count_for_variant(name)):
            try:
                result = _post_json(url=url, headers=headers, payload=payload, timeout=timeout)
                return parse(result)
            except urllib.error.HTTPError as error:
                detail = _sanitize_transport_error(_http_error_detail(error), secret_values=secret_values)
                attempts.append(f"{name}: HTTP {error.code} {detail}".strip())
                if error.code in {400, 422}:
                    break
                if error.code in {408, 409, 425, 429, 500, 502, 503, 504} and retry_index + 1 < _retry_count_for_variant(name):
                    _sleep_before_retry(retry_index)
                    continue
                break
            except (TimeoutError, socket.timeout, urllib.error.URLError) as error:
                detail = _sanitize_transport_error(str(error), secret_values=secret_values)
                attempts.append(f"{name}: {error.__class__.__name__} {detail}".strip())
                if retry_index + 1 < _retry_count_for_variant(name):
                    _sleep_before_retry(retry_index)
                    continue
                break
            except Exception as error:
                detail = _sanitize_transport_error(str(error), secret_values=secret_values)
                attempts.append(f"{name}: {error.__class__.__name__} {detail}".strip())
                break
    raise RuntimeError("LLM request failed after fallback attempts: " + " | ".join(attempts[-8:]))


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
        return json.loads(resp.read())


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
        r"(?i)\b(api[_-]?key|authorization|bearer|password|secret|token)\b\s*[:=]\s*[^\s,;|]+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        sanitized,
    )
    return sanitized[:240].replace("\n", " ")

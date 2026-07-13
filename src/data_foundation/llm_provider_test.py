"""Non-persistent LLM provider connectivity checks."""

from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Callable

from .llm_transport import send_anthropic_message, send_openai_compatible_message
from .llm_provider_catalog import normalize_llm_provider_update
from .paths import RuntimePaths
from .secret_store import read_secret
from .settings import MASKED_SECRET, _secret_ref_requires_reentry, read_settings, resolve_llm_provider

LlmSender = Callable[..., str]


def check_llm_provider_availability(
    paths: RuntimePaths | None = None,
    *,
    candidate: dict | None = None,
    anthropic_sender: LlmSender = send_anthropic_message,
    openai_sender: LlmSender = send_openai_compatible_message,
) -> dict:
    """Probe the configured provider without persisting candidate fields or secrets."""
    provider = _resolved_candidate(paths, candidate)
    missing = [field for field in ("endpoint", "model", "apiKey") if not str(provider.get(field) or "").strip()]
    base = _public_probe_payload(provider)
    if missing:
        return {**base, "ok": False, "status": "missing_config", "missing": missing, "error": "Missing " + ", ".join(missing)}

    sender = anthropic_sender if provider.get("api") == "anthropic-messages" else openai_sender
    started = perf_counter()
    try:
        text = sender(
            endpoint=provider["endpoint"],
            api_key=provider["apiKey"],
            model=provider["model"],
            system="You are an availability probe. Reply with OK only.",
            prompt="Reply exactly OK.",
            temperature=0,
            max_tokens=16,
            timeout=30,
            thinking_mode="off",
        )
        latency_ms = int((perf_counter() - started) * 1000)
        return {
            **base,
            "ok": True,
            "status": "ok",
            "latencyMs": latency_ms,
            "responsePreview": str(text or "").strip()[:80],
        }
    except Exception as exc:
        latency_ms = int((perf_counter() - started) * 1000)
        return {
            **base,
            "ok": False,
            "status": "error",
            "latencyMs": latency_ms,
            "error": str(exc)[:500],
        }


def _resolved_candidate(paths: RuntimePaths | None, candidate: dict | None) -> dict:
    provider = resolve_llm_provider(paths, redact_secrets=False)
    if not isinstance(candidate, dict):
        return provider
    normalized_candidate = dict(candidate)
    if str(normalized_candidate.get("apiKey") or "") == MASKED_SECRET:
        normalized_candidate.pop("apiKey", None)
    merged = normalize_llm_provider_update(normalized_candidate, provider)
    api_key = str(candidate.get("apiKey") or "")
    if api_key and api_key != MASKED_SECRET:
        merged["apiKey"] = api_key
    else:
        candidate_provider = str(merged.get("provider") or "")
        current_provider = str(provider.get("provider") or "")
        saved_ref = _saved_secret_ref_for_provider(paths, candidate_provider)
        saved_key = _read_secret_for_paths(saved_ref, paths) if saved_ref else ""
        if saved_key:
            merged["apiKey"] = saved_key
            merged["secretRef"] = saved_ref
        elif candidate_provider == current_provider and provider.get("apiKey"):
            merged["apiKey"] = provider["apiKey"]
            if provider.get("secretRef"):
                merged["secretRef"] = provider["secretRef"]
        else:
            merged["apiKey"] = ""
            merged.pop("secretRef", None)
    return merged


def _saved_secret_ref_for_provider(paths: RuntimePaths | None, provider_id: str) -> dict | None:
    try:
        settings = read_settings(paths, redact_secrets=False)
    except Exception:
        return None
    refs = settings.get("llmProviderSecrets") if isinstance(settings.get("llmProviderSecrets"), dict) else {}
    ref = refs.get(provider_id)
    if isinstance(ref, dict) and not _secret_ref_requires_reentry(settings, ref):
        return ref
    provider = settings.get("llmProvider") if isinstance(settings.get("llmProvider"), dict) else {}
    if (
        str(provider.get("provider") or "") == provider_id
        and isinstance(provider.get("secretRef"), dict)
        and not _secret_ref_requires_reentry(settings, provider["secretRef"])
    ):
        return provider["secretRef"]
    return None


def _read_secret_for_paths(ref: dict, paths: RuntimePaths | None) -> str:
    if str(ref.get("backend") or "") == "runtime-file" and paths is not None:
        return read_secret(ref, runtime_home=paths.home)
    return read_secret(ref)


def _public_probe_payload(provider: dict) -> dict:
    return {
        "provider": provider.get("provider") or "",
        "endpoint": provider.get("endpoint") or "",
        "model": provider.get("model") or "",
        "api": provider.get("api") or "openai-compatible",
        "hasApiKey": bool(provider.get("apiKey")),
        "checkedAt": datetime.now().astimezone().isoformat(),
    }

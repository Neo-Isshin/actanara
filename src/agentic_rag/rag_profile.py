"""RAG embedding profile helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .rag_settings import RagSettings

PROFILE_KEYS = ("mode", "providerId", "model", "dimension")


def settings_embedding_profile(settings: RagSettings) -> dict[str, Any]:
    return {
        "mode": settings.embedding_provider,
        "providerId": settings.embedding_provider_id,
        "model": settings.embedding_model,
        "dimension": settings.embedding_dimension,
    }


def manifest_embedding_profile(manifest: dict[str, Any]) -> dict[str, Any]:
    mode = str(manifest.get("embeddingProvider") or manifest.get("embeddingMode") or "").strip()
    provider_id = str(manifest.get("embeddingProviderId") or manifest.get("providerId") or mode or "").strip()
    return {
        "mode": mode,
        "providerId": provider_id,
        "model": str(manifest.get("model") or manifest.get("embeddingModel") or "").strip(),
        "dimension": _optional_int(manifest.get("dimension") or manifest.get("embeddingDimension")),
    }


def profile_hash(profile: dict[str, Any]) -> str:
    normalized = {key: profile.get(key) for key in PROFILE_KEYS}
    body = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def source_profile_hash(profile: dict[str, Any]) -> str:
    body = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def profile_with_hash(profile: dict[str, Any]) -> dict[str, Any]:
    return {**profile, "hash": profile_hash(profile)}


def profiles_match(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False
    return all(_normalized(left.get(key)) == _normalized(right.get(key)) for key in PROFILE_KEYS)


def _normalized(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Compatibility constants backed by the canonical RAG settings resolver.

The resolver is the source of truth for paths, model, and dimensions. This
module preserves constant-style access for supported script entry points.
"""

from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path

from rag_settings import resolve_rag_settings
from rag_active_source import resolve_active_rag_index
from rag_reranker import build_reranker_policy

try:
    from data_foundation.secret_store import read_secret
except ImportError:  # pragma: no cover - direct legacy script fallback
    read_secret = None  # type: ignore


_SETTINGS = resolve_rag_settings()
_ACTIVE_INDEX = resolve_active_rag_index(_SETTINGS)

DIARY_ROOT = _SETTINGS.diary_source_root
INDEX_DIR = _SETTINGS.legacy_index_path.parent
INDEX_FILE = _ACTIVE_INDEX.index_path or _SETTINGS.legacy_index_path
LEGACY_INDEX_FILE = _SETTINGS.legacy_index_path
LESSONS_FILE = _SETTINGS.lessons_path
INDEX_SOURCE = _ACTIVE_INDEX.source
INDEX_READY = _ACTIVE_INDEX.ready
INDEX_UNAVAILABLE_REASON = _ACTIVE_INDEX.reason

PRODUCTION_MODE = _SETTINGS.embedding_provider
PROVIDER_ID = _SETTINGS.embedding_provider_id
LANGUAGE_PROFILE = _SETTINGS.language_profile

MODEL_NAME = _SETTINGS.embedding_model
EMBEDDING_DIM = _SETTINGS.embedding_dimension
EMBEDDING_PROFILE = {
    "mode": PRODUCTION_MODE,
    "providerId": PROVIDER_ID,
    "model": MODEL_NAME,
    "dimension": EMBEDDING_DIM,
}
EMBEDDING_PROFILE_HASH = hashlib.sha256(
    json.dumps(EMBEDDING_PROFILE, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
EMBEDDING_DEVICE = _SETTINGS.embedding_device

SIMILARITY_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3
TIME_DECAY_HALF_LIFE = _SETTINGS.recency_half_life_days
DEFAULT_TOP_K = _SETTINGS.retrieval_top_k
SEARCH_LATENCY_BUDGET_SECONDS = _SETTINGS.retrieval_latency_budget_seconds
MAX_CONCURRENT_SEARCHES = _SETTINGS.retrieval_max_concurrent_searches
RERANKER_ENABLED = _SETTINGS.reranker_enabled
RERANKER_PROVIDER = _SETTINGS.reranker_provider
RERANKER_POLICY = build_reranker_policy(_SETTINGS)

CLOUD_API_KEY_ENV = _SETTINGS.embedding_api_key_env
CLOUD_API_KEY = ""
if (
    _SETTINGS.embedding_secret_ref
    and not _SETTINGS.embedding_secret_migration_required
    and read_secret is not None
):
    try:
        CLOUD_API_KEY = read_secret(
            _SETTINGS.embedding_secret_ref,
            **(
                {"runtime_home": _SETTINGS.runtime_home}
                if _SETTINGS.embedding_secret_ref.get("backend") == "runtime-file"
                else {}
            ),
        )
    except Exception:
        CLOUD_API_KEY = ""
if not CLOUD_API_KEY:
    secret_ref = _SETTINGS.embedding_secret_ref or {}
    env_name = str(secret_ref.get("account") or CLOUD_API_KEY_ENV) if secret_ref.get("backend") == "process-env" else CLOUD_API_KEY_ENV
    CLOUD_API_KEY = os.environ.get(env_name, "")
CLOUD_URL = _SETTINGS.embedding_endpoint
CLOUD_MODEL = _SETTINGS.embedding_model

SERVER_HOST = _SETTINGS.server_host
SERVER_PORT = _SETTINGS.server_port
SERVER_HEALTH_PATH = _SETTINGS.server_health_path
V2_STORE_PATH = _SETTINGS.v2_store_path


def as_dict() -> dict:
    return {
        "diaryRoot": str(DIARY_ROOT),
        "indexDir": str(INDEX_DIR),
        "indexFile": str(INDEX_FILE),
        "legacyIndexFile": str(LEGACY_INDEX_FILE),
        "indexSource": INDEX_SOURCE,
        "indexReady": INDEX_READY,
        "indexUnavailableReason": INDEX_UNAVAILABLE_REASON,
        "lessonsFile": str(LESSONS_FILE),
        "productionMode": PRODUCTION_MODE,
        "providerId": PROVIDER_ID,
        "languageProfile": LANGUAGE_PROFILE,
        "modelName": MODEL_NAME,
        "embeddingDim": EMBEDDING_DIM,
        "embeddingProfile": EMBEDDING_PROFILE,
        "embeddingProfileHash": EMBEDDING_PROFILE_HASH,
        "embeddingDevice": EMBEDDING_DEVICE,
        "defaultTopK": DEFAULT_TOP_K,
        "searchLatencyBudgetSeconds": SEARCH_LATENCY_BUDGET_SECONDS,
        "maxConcurrentSearches": MAX_CONCURRENT_SEARCHES,
        "cloudApiKeyEnv": CLOUD_API_KEY_ENV,
        "cloudUrl": CLOUD_URL,
        "cloudModel": CLOUD_MODEL,
        "serverHost": SERVER_HOST,
        "serverPort": SERVER_PORT,
        "serverHealthPath": SERVER_HEALTH_PATH,
        "v2StorePath": str(V2_STORE_PATH),
        "rerankerEnabled": RERANKER_ENABLED,
        "rerankerProvider": RERANKER_PROVIDER,
        "reranker": RERANKER_POLICY.to_dict(),
    }

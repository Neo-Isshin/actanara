"""RAG v2 shadow storage primitives.

These helpers only manage v2 metadata under ``$ACTANARA_HOME/reserved/rag/v2``.
They do not read, rebuild, compact, delete or replace the legacy production
RAG index.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .rag_settings import RagSettings, resolve_rag_settings
from .rag_profile import profile_hash, settings_embedding_profile


SCHEMA_VERSION = 1


class RagV2OperationLockError(RuntimeError):
    """Raised when a mutating RAG v2 operation is already in progress."""

    def __init__(self, lock_path: Path, operation: str):
        self.lock_path = lock_path
        self.operation = operation
        super().__init__(f"nova-RAG v2 operation already running; lock={lock_path}")


def rag_v2_operation_lock_path(settings: RagSettings | None = None) -> Path:
    resolved = settings or resolve_rag_settings()
    return resolved.v2_store_path / "locks" / "sync-promote.lock"


@contextlib.contextmanager
def rag_v2_operation_lock(settings: RagSettings | None = None, *, operation: str = "rag-v2") -> Iterator[dict[str, Any]]:
    resolved = settings or resolve_rag_settings()
    lock_path = rag_v2_operation_lock_path(resolved)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RagV2OperationLockError(lock_path, operation) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"operation": operation, "lockedAt": _now_iso()}, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        yield {"lockPath": str(lock_path), "operation": operation}
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def ensure_v2_store(settings: RagSettings | None = None) -> dict[str, Any]:
    resolved = settings or resolve_rag_settings()
    root = resolved.v2_store_path
    for directory in (
        root,
        root / "indexes" / "active",
        root / "indexes" / "candidates",
        root / "logs",
        root / "locks",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    config_payload = _config_payload(resolved)
    if not config_path.exists():
        _write_json_atomic(config_path, config_payload)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        _write_json_atomic(manifest_path, _empty_manifest(resolved))
    return {
        "storePath": str(root),
        "configPath": str(config_path),
        "manifestPath": str(manifest_path),
        "buildRunsPath": str(root / "build-runs.jsonl"),
    }


def initialize_shadow_build(
    settings: RagSettings | None = None,
    *,
    requested_by: str = "operator",
    reason: str = "shadow-index-request",
) -> dict[str, Any]:
    """Create a candidate build run without performing indexing."""
    resolved = settings or resolve_rag_settings()
    paths = ensure_v2_store(resolved)
    root = resolved.v2_store_path
    run_id = _new_run_id()
    candidate_dir = root / "indexes" / "candidates" / run_id
    candidate_dir.mkdir(parents=True, exist_ok=False)
    now = _now_iso()
    run = {
        "runId": run_id,
        "schemaVersion": SCHEMA_VERSION,
        "status": "initialized",
        "phase": "shadow-storage",
        "requestedBy": requested_by,
        "reason": reason,
        "createdAt": now,
        "updatedAt": now,
        "model": resolved.embedding_model,
        "dimension": resolved.embedding_dimension,
        "languageProfile": resolved.language_profile,
        "embeddingProvider": resolved.embedding_provider,
        "embeddingProviderId": resolved.embedding_provider_id,
        "embeddingProfile": settings_embedding_profile(resolved),
        "embeddingProfileHash": profile_hash(settings_embedding_profile(resolved)),
        "legacyIndexPath": str(resolved.legacy_index_path),
        "candidatePath": str(candidate_dir),
        "activePromotionAllowed": False,
        "notes": "Candidate storage initialized; embeddings have not been generated yet and no legacy index was mutated.",
    }
    candidate_manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "indexVersion": run_id,
        "status": "initialized",
        "createdAt": now,
        "updatedAt": now,
        "completedAt": None,
        "model": resolved.embedding_model,
        "dimension": resolved.embedding_dimension,
        "languageProfile": resolved.language_profile,
        "embeddingProvider": resolved.embedding_provider,
        "embeddingProviderId": resolved.embedding_provider_id,
        "embeddingProfile": settings_embedding_profile(resolved),
        "embeddingProfileHash": profile_hash(settings_embedding_profile(resolved)),
        "sourceSets": [],
        "documentCount": 0,
        "chunkCount": 0,
        "embeddingCount": 0,
        "byteSize": 0,
        "checksum": None,
        "activeIndexPath": None,
        "lastBuildRunId": run_id,
        "lastError": None,
    }
    _write_json_atomic(candidate_dir / "manifest.json", candidate_manifest)
    _append_jsonl(root / "build-runs.jsonl", run)
    existing_root_manifest = _read_json(root / "manifest.json")
    _write_json_atomic(
        root / "manifest.json",
        _root_manifest_for_candidate_update(
            resolved,
            existing_root_manifest=existing_root_manifest,
            candidate_manifest={
                **candidate_manifest,
                "candidatePath": str(candidate_dir),
            },
            root_status="candidate-initialized",
            now=now,
        ),
    )
    return {
        "accepted": True,
        "status": "initialized",
        "run": run,
        "candidateManifest": str(candidate_dir / "manifest.json"),
        "store": paths,
    }


def promote_candidate(settings: RagSettings | None, run_id: str) -> dict[str, Any]:
    """Promote a ready candidate manifest to active.

    Promotion intentionally requires the candidate to have already passed
    indexing validation and been marked ``ready``.
    """
    resolved = settings or resolve_rag_settings()
    root = resolved.v2_store_path
    candidate_manifest_path = root / "indexes" / "candidates" / run_id / "manifest.json"
    candidate = _read_json(candidate_manifest_path)
    if candidate.get("status") != "ready":
        raise ValueError("candidate must be ready before promotion")
    now = _now_iso()
    active_dir = root / "indexes" / "active"
    active_dir.mkdir(parents=True, exist_ok=True)
    promoted = {
        **candidate,
        "status": "active",
        "updatedAt": now,
        "activeIndexPath": str(active_dir),
    }
    _write_json_atomic(root / "manifest.json", promoted)
    _append_jsonl(
        root / "build-runs.jsonl",
        {
            "runId": f"promote-{run_id}",
            "schemaVersion": SCHEMA_VERSION,
            "status": "promoted",
            "createdAt": now,
            "updatedAt": now,
            "candidateRunId": run_id,
            "activeIndexPath": str(active_dir),
        },
    )
    return {"status": "promoted", "runId": run_id, "manifestPath": str(root / "manifest.json")}


def latest_build_run(settings: RagSettings | None = None) -> dict[str, Any] | None:
    resolved = settings or resolve_rag_settings()
    runs_path = resolved.v2_store_path / "build-runs.jsonl"
    latest = None
    try:
        with runs_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    latest = value
    except OSError:
        return None
    return latest


def _config_payload(settings: RagSettings) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "createdAt": _now_iso(),
        "mode": settings.mode,
        "languageProfile": settings.language_profile,
        "embedding": {
            "provider": settings.embedding_provider,
            "providerId": settings.embedding_provider_id,
            "model": settings.embedding_model,
            "dimension": settings.embedding_dimension,
            "batchSize": settings.embedding_batch_size,
            "device": settings.embedding_device,
        },
        "legacy": {"indexPath": str(settings.legacy_index_path)},
        "retrieval": {
            "topK": settings.retrieval_top_k,
            "recencyHalfLifeDays": settings.recency_half_life_days,
            "reranker": {
                "enabled": settings.reranker_enabled,
                "provider": settings.reranker_provider,
                "model": settings.reranker_model,
            },
        },
    }


def _empty_manifest(settings: RagSettings) -> dict[str, Any]:
    now = _now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "indexVersion": None,
        "status": "empty",
        "createdAt": now,
        "updatedAt": now,
        "completedAt": None,
        "model": settings.embedding_model,
        "dimension": settings.embedding_dimension,
        "languageProfile": settings.language_profile,
        "embeddingProvider": settings.embedding_provider,
        "embeddingProviderId": settings.embedding_provider_id,
        "embeddingProfile": settings_embedding_profile(settings),
        "embeddingProfileHash": profile_hash(settings_embedding_profile(settings)),
        "sourceSets": [],
        "documentCount": 0,
        "chunkCount": 0,
        "embeddingCount": 0,
        "byteSize": 0,
        "checksum": None,
        "activeIndexPath": None,
        "lastBuildRunId": None,
        "lastError": None,
    }


def _root_manifest_for_candidate_update(
    settings: RagSettings,
    *,
    existing_root_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
    root_status: str,
    now: str,
) -> dict[str, Any]:
    latest_candidate = {
        "runId": candidate_manifest.get("lastBuildRunId") or candidate_manifest.get("indexVersion"),
        "status": candidate_manifest.get("status"),
        "candidatePath": candidate_manifest.get("candidatePath"),
        "candidateIndexPath": candidate_manifest.get("candidateIndexPath"),
        "manifestPath": candidate_manifest.get("manifestPath"),
        "chunkCount": candidate_manifest.get("chunkCount"),
        "embeddingCount": candidate_manifest.get("embeddingCount"),
        "dimension": candidate_manifest.get("dimension"),
        "checksum": candidate_manifest.get("checksum"),
        "activePromotionAllowed": bool(candidate_manifest.get("activePromotionAllowed")),
    }
    if existing_root_manifest.get("status") == "active":
        return {
            **existing_root_manifest,
            "updatedAt": now,
            "lastBuildRunId": latest_candidate["runId"],
            "lastCandidate": latest_candidate,
            "lastCandidateStatus": root_status,
            "lastError": None,
        }
    return {
        **_empty_manifest(settings),
        **candidate_manifest,
        "status": root_status,
        "updatedAt": now,
        "lastBuildRunId": latest_candidate["runId"],
        "lastCandidate": latest_candidate,
        "activePromotionAllowed": False,
        "activeIndexPath": None,
        "lastError": None,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _new_run_id() -> str:
    return f"{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')}-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()

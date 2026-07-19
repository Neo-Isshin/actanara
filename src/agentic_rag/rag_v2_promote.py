"""Guarded RAG v2 candidate promotion.

Promotion prepares an active v2 snapshot under ``$ACTANARA_HOME/reserved/rag/v2``.
It does not switch ``rag.mode``, mutate the legacy index, rebuild candidates, or
start/stop the embedding server.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .rag_settings import RagSettings, effective_indexing_source_sets, resolve_rag_settings
from .rag_v2_store import SCHEMA_VERSION, RagV2OperationLockError, rag_v2_operation_lock
from .rag_profile import profile_hash, settings_embedding_profile, source_profile_hash
from .rag_v2_indexer import _source_profile
from .rag_v2_retention import (
    DEFAULT_KEEP_ACTIVE_RUNS,
    DEFAULT_KEEP_CANDIDATES,
    prune_v2_index_store,
    retention_policy_manifest,
    retention_result_manifest,
)


def required_v2_promotion_confirmation(run_id: str) -> str:
    return f"PROMOTE RAG V2 {run_id}"


def promote_v2_candidate(
    settings: RagSettings | None = None,
    *,
    run_id: str,
    confirm: bool,
    confirmation_text: str,
    requested_by: str = "operator",
    reason: str = "operator validation",
    acquire_lock: bool = True,
) -> dict[str, Any]:
    """Promote a ready candidate into an active v2 snapshot."""
    resolved = settings or resolve_rag_settings()
    if acquire_lock:
        try:
            with rag_v2_operation_lock(resolved, operation="promote"):
                return _promote_v2_candidate_locked(
                    resolved,
                    run_id=run_id,
                    confirm=confirm,
                    confirmation_text=confirmation_text,
                    requested_by=requested_by,
                    reason=reason,
                )
        except RagV2OperationLockError as exc:
            raise RuntimeError(str(exc)) from exc
    return _promote_v2_candidate_locked(
        resolved,
        run_id=run_id,
        confirm=confirm,
        confirmation_text=confirmation_text,
        requested_by=requested_by,
        reason=reason,
    )


def _promote_v2_candidate_locked(
    resolved: RagSettings,
    *,
    run_id: str,
    confirm: bool,
    confirmation_text: str,
    requested_by: str,
    reason: str,
) -> dict[str, Any]:
    clean_run_id = str(run_id or "").strip()
    if not clean_run_id:
        raise ValueError("runId is required")
    required = required_v2_promotion_confirmation(clean_run_id)
    if confirm is not True or str(confirmation_text or "") != required:
        raise ValueError(f"confirmationText must be exactly: {required}")

    root = resolved.v2_store_path
    candidate_dir = root / "indexes" / "candidates" / clean_run_id
    active_dir = root / "indexes" / "active" / clean_run_id
    candidate_manifest_path = candidate_dir / "manifest.json"
    root_manifest_path = root / "manifest.json"
    candidate_manifest = _read_json(candidate_manifest_path)
    _validate_candidate(candidate_manifest, resolved, candidate_dir)
    comparison = {
        "status": "retired",
        "available": False,
        "reason": "legacy comparison is retired; promotion validates the v2 candidate manifest and source profile.",
    }
    if active_dir.exists():
        raise ValueError(f"active snapshot already exists for runId: {clean_run_id}")

    source_files = _candidate_files(candidate_manifest, candidate_dir)
    for path in source_files.values():
        _require_inside(path, root)
        if not path.exists() or not path.is_file():
            raise ValueError(f"candidate file is missing: {path}")

    now = _now_iso()
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    backup_dir = root / "manifest.backups"
    backup_path = backup_dir / f"{timestamp}-before-promote-{clean_run_id}.json"
    active_dir.mkdir(parents=True, exist_ok=False)
    backup_created = False
    retention: dict[str, Any] | None = None
    try:
        active_files = {
            "activeIndexPath": active_dir / "index.jsonl",
            "chunksPath": active_dir / "chunks.jsonl",
            "embeddingsPath": active_dir / "embeddings.jsonl",
            "sourcesPath": active_dir / "sources.jsonl",
        }
        for source_key, target_key in (
            ("index", "activeIndexPath"),
            ("chunks", "chunksPath"),
            ("embeddings", "embeddingsPath"),
            ("sources", "sourcesPath"),
        ):
            shutil.copy2(source_files[source_key], active_files[target_key])

        if root_manifest_path.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root_manifest_path, backup_path)
            backup_created = True

        active_manifest_path = active_dir / "manifest.json"
        active_manifest = _active_manifest(
            candidate_manifest,
            resolved,
            clean_run_id,
            now=now,
            requested_by=requested_by,
            reason=reason,
            active_files=active_files,
            active_manifest_path=active_manifest_path,
            backup_path=backup_path if backup_created else None,
        )
        _write_json_atomic(active_manifest_path, active_manifest)
        _write_json_atomic(root_manifest_path, active_manifest)
        retention = prune_v2_index_store(
            resolved,
            active_run_id=clean_run_id,
            keep_active_runs=DEFAULT_KEEP_ACTIVE_RUNS,
            keep_candidates=DEFAULT_KEEP_CANDIDATES,
        )
        event = {
            "runId": f"promote-{clean_run_id}-{timestamp}",
            "schemaVersion": SCHEMA_VERSION,
            "status": "promoted",
            "phase": "v2-candidate-promotion",
            "createdAt": now,
            "updatedAt": now,
            "candidateRunId": clean_run_id,
            "activeRunId": clean_run_id,
            "requestedBy": requested_by,
            "reason": reason,
            "activeIndexPath": str(active_files["activeIndexPath"]),
            "activeManifestPath": str(active_manifest_path),
            "previousManifestBackupPath": str(backup_path) if backup_created else None,
            "comparisonRequired": False,
            "retention": retention_result_manifest(retention),
        }
        _append_jsonl(root / "build-runs.jsonl", event)
        _append_jsonl(root / "logs" / "promotions.jsonl", event)
    except Exception:
        shutil.rmtree(active_dir, ignore_errors=True)
        raise

    return {
        "accepted": True,
        "status": "promoted",
        "runId": clean_run_id,
        "activeRunId": clean_run_id,
        "manifestPath": str(root_manifest_path),
        "activeManifestPath": str(active_dir / "manifest.json"),
        "activeIndexPath": str(active_dir / "index.jsonl"),
        "previousManifestBackupPath": str(backup_path) if backup_created else None,
        "requiredConfirmation": required,
        "comparison": comparison,
        "retention": retention,
        "mutationPolicy": {
            "legacyMutated": False,
            "settingsMutated": False,
            "serverLifecycleChanged": False,
            "candidateFilesMutated": bool(retention and (retention.get("deleted") or {}).get("candidates")),
            "rootManifestMutated": True,
            "activeSnapshotCreated": True,
            "activeSnapshotsDeleted": bool(retention and (retention.get("deleted") or {}).get("activeRuns")),
            "writesRestrictedToV2Store": True,
        },
    }


def _validate_candidate(manifest: dict[str, Any], settings: RagSettings, candidate_dir: Path) -> None:
    if not manifest:
        raise ValueError("candidate manifest is missing")
    if manifest.get("status") != "ready":
        raise ValueError("candidate must be ready before promotion")
    if manifest.get("activePromotionAllowed") is not True:
        raise ValueError("candidate is not marked activePromotionAllowed")
    if int(manifest.get("dimension") or 0) != int(settings.embedding_dimension):
        raise ValueError("candidate embedding dimension does not match configured dimension")
    expected_embedding_hash = profile_hash(settings_embedding_profile(settings))
    if str(manifest.get("embeddingProfileHash") or "") != expected_embedding_hash:
        raise ValueError("candidate embeddingProfileHash does not match current RAG settings")
    source_sets = tuple(str(item) for item in manifest.get("sourceSets") or effective_indexing_source_sets(settings))
    expected_source_hash = source_profile_hash(_source_profile(settings, source_sets))
    if str(manifest.get("sourceProfileHash") or "") != expected_source_hash:
        raise ValueError("candidate sourceProfileHash does not match current RAG settings")
    _require_inside(candidate_dir, settings.v2_store_path)


def _candidate_files(manifest: dict[str, Any], candidate_dir: Path) -> dict[str, Path]:
    return {
        "index": _path_from_manifest(manifest, "candidateIndexPath", candidate_dir / "index.jsonl"),
        "chunks": _path_from_manifest(manifest, "chunksPath", candidate_dir / "chunks.jsonl"),
        "embeddings": _path_from_manifest(manifest, "embeddingsPath", candidate_dir / "embeddings.jsonl"),
        "sources": _path_from_manifest(manifest, "sourcesPath", candidate_dir / "sources.jsonl"),
    }


def _active_manifest(
    candidate: dict[str, Any],
    settings: RagSettings,
    run_id: str,
    *,
    now: str,
    requested_by: str,
    reason: str,
    active_files: dict[str, Path],
    active_manifest_path: Path,
    backup_path: Path | None,
) -> dict[str, Any]:
    promoted = dict(candidate)
    for key in ("candidatePath", "candidateIndexPath", "buildReportPath", "manifestPath"):
        promoted.pop(key, None)
    promoted.update(
        {
            "status": "active",
            "updatedAt": now,
            "promotedAt": now,
            "promotedBy": requested_by,
            "promotionReason": reason,
            "activeRunId": run_id,
            "promotedFromRunId": run_id,
            "activeIndexPath": str(active_files["activeIndexPath"]),
            "activeManifestPath": str(active_manifest_path),
            "chunksPath": str(active_files["chunksPath"]),
            "embeddingsPath": str(active_files["embeddingsPath"]),
            "sourcesPath": str(active_files["sourcesPath"]),
            "previousManifestBackupPath": str(backup_path) if backup_path else None,
            "rollbackMode": "previous-v2-manifest",
            "activePromotionAllowed": False,
            "retentionPolicy": retention_policy_manifest(),
            "promotionMutationPolicy": {
                "legacyMutated": False,
                "settingsMutated": False,
                "serverLifecycleChanged": False,
                "candidateFilesMutated": True,
                "retentionPruneEnabled": True,
            },
            "promotionProvenance": {
                "candidateRunId": candidate.get("lastBuildRunId") or candidate.get("indexVersion") or run_id,
                "candidateStatus": candidate.get("status"),
                "candidateChecksum": candidate.get("checksum"),
                "candidateByteSize": candidate.get("byteSize"),
            },
            "notes": "Active v2 snapshot; rag.mode was not changed by promotion.",
        }
    )
    return promoted


def _path_from_manifest(manifest: dict[str, Any], key: str, fallback: Path) -> Path:
    value = manifest.get(key)
    return Path(str(value)).expanduser() if value else fallback


def _require_inside(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path is outside the v2 store: {path}") from exc


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


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()

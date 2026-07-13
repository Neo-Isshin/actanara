"""Guarded RAG v2 manifest rollback.

Rollback restores a previous v2 root manifest backup. It does not switch
``rag.mode``, mutate legacy storage, delete active snapshots, or manage server
processes.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .rag_settings import RagSettings, resolve_rag_settings
from .rag_v2_store import SCHEMA_VERSION


def required_v2_manifest_rollback_confirmation(backup_name: str) -> str:
    return f"ROLLBACK RAG V2 MANIFEST {backup_name}"


def rollback_v2_manifest(
    settings: RagSettings | None = None,
    *,
    backup_name: str,
    confirm: bool,
    confirmation_text: str,
    requested_by: str = "operator",
    reason: str = "operator rollback",
) -> dict[str, Any]:
    """Restore the root v2 manifest from a previous backup."""
    resolved = settings or resolve_rag_settings()
    clean_backup_name = str(backup_name or "").strip()
    if not clean_backup_name:
        raise ValueError("backupName is required")
    if Path(clean_backup_name).name != clean_backup_name or clean_backup_name in {".", ".."}:
        raise ValueError("backupName must be a file name under manifest.backups")
    required = required_v2_manifest_rollback_confirmation(clean_backup_name)
    if confirm is not True or str(confirmation_text or "") != required:
        raise ValueError(f"confirmationText must be exactly: {required}")

    root = resolved.v2_store_path
    backup_path = root / "manifest.backups" / clean_backup_name
    root_manifest_path = root / "manifest.json"
    _require_inside(backup_path, root / "manifest.backups")
    if not backup_path.is_file():
        raise ValueError("backup manifest file is missing")
    backup_manifest = _read_json(backup_path)
    _validate_restore_manifest(backup_manifest, resolved)

    now = _now_iso()
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    current_backup_path = root / "manifest.backups" / f"{timestamp}-before-rollback-{clean_backup_name}"
    current_backup_created = False
    if root_manifest_path.exists():
        current_backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root_manifest_path, current_backup_path)
        current_backup_created = True

    restored_manifest = {
        **backup_manifest,
        "restoredAt": now,
        "restoredBy": requested_by,
        "restoreReason": reason,
        "restoredFromBackupPath": str(backup_path),
        "previousManifestBackupPath": str(current_backup_path) if current_backup_created else None,
        "rollbackMutationPolicy": {
            "legacyMutated": False,
            "settingsMutated": False,
            "serverLifecycleChanged": False,
            "activeSnapshotsDeleted": False,
            "candidateFilesMutated": False,
        },
    }
    _write_json_atomic(root_manifest_path, restored_manifest)
    event = {
        "runId": f"rollback-{timestamp}",
        "schemaVersion": SCHEMA_VERSION,
        "status": "rolled-back",
        "phase": "v2-manifest-rollback",
        "createdAt": now,
        "updatedAt": now,
        "requestedBy": requested_by,
        "reason": reason,
        "restoredFromBackupPath": str(backup_path),
        "previousManifestBackupPath": str(current_backup_path) if current_backup_created else None,
        "restoredStatus": restored_manifest.get("status"),
        "restoredActiveIndexPath": restored_manifest.get("activeIndexPath"),
        "legacyIndexPath": str(resolved.legacy_index_path),
    }
    _append_jsonl(root / "build-runs.jsonl", event)
    _append_jsonl(root / "logs" / "rollbacks.jsonl", event)
    return {
        "accepted": True,
        "status": "rolled-back",
        "manifestPath": str(root_manifest_path),
        "restoredFromBackupPath": str(backup_path),
        "previousManifestBackupPath": str(current_backup_path) if current_backup_created else None,
        "restoredManifestStatus": restored_manifest.get("status"),
        "restoredActiveIndexPath": restored_manifest.get("activeIndexPath"),
        "requiredConfirmation": required,
        "mutationPolicy": {
            "legacyMutated": False,
            "settingsMutated": False,
            "serverLifecycleChanged": False,
            "activeSnapshotsDeleted": False,
            "candidateFilesMutated": False,
            "rootManifestMutated": True,
            "writesRestrictedToV2Store": True,
        },
    }


def _validate_restore_manifest(manifest: dict[str, Any], settings: RagSettings) -> None:
    if not manifest:
        raise ValueError("backup manifest is missing or invalid")
    if "schemaVersion" not in manifest or "status" not in manifest:
        raise ValueError("backup manifest does not look like a RAG v2 manifest")
    status = str(manifest.get("status") or "")
    if status not in {"active", "candidate-ready", "candidate-partial", "candidate-initialized", "ready", "empty"}:
        raise ValueError(f"backup manifest status is not restorable: {status}")
    active_index = manifest.get("activeIndexPath")
    if status == "active":
        if not active_index:
            raise ValueError("active backup manifest must include activeIndexPath")
        active_path = Path(str(active_index)).expanduser()
        _require_inside(active_path, settings.v2_store_path / "indexes" / "active")
        if not active_path.exists() or not active_path.is_file():
            raise ValueError(f"active backup index is missing: {active_path}")
        if int(manifest.get("dimension") or 0) != int(settings.embedding_dimension):
            raise ValueError("active backup manifest dimension does not match configured dimension")


def _require_inside(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path is outside the v2 store boundary: {path}") from exc


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

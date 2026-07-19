"""Dashboard facade for private Actanara data backups.

The facade owns no backup persistence logic.  It resolves the selected runtime,
uses the dedicated settings transaction helper, and returns deliberately small
public payloads that exclude source-runtime paths, manifests, and secret data.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
import re
import threading
from typing import Any
from uuid import uuid4

from agentic_rag.rag_settings import resolve_rag_settings
from data_foundation import backup as backup_engine
from data_foundation.paths import RuntimePaths, load_paths
from data_foundation.settings import read_settings, write_backup_settings
from data_foundation.time import resolve_timezone


BACKUP_CONFIRMATION = "BACK UP ACTANARA DATA"
BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_PENDING: dict[str, dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()
_SCHEDULE_LOCK = threading.Lock()


class BackupFacadeError(ValueError):
    """A stable, response-safe Dashboard backup error."""

    def __init__(self, code: str, message: str, *, status_code: int = 400):
        self.code = str(code or "backup-invalid-request")
        self.status_code = int(status_code)
        super().__init__(message)


def get_backup_status() -> dict[str, Any]:
    paths = load_paths()
    settings = _backup_settings(paths)
    engine_status = _read_engine_status(paths)
    return _public_status(paths, settings, engine_status)


def update_backup_settings(payload: dict[str, Any]) -> dict[str, Any]:
    paths = load_paths()
    update = payload.get("backup") if isinstance(payload, dict) and isinstance(payload.get("backup"), dict) else payload
    if not isinstance(update, dict):
        raise BackupFacadeError("backup-settings-invalid", "Backup settings must be an object.")

    def readiness() -> None:
        saved = _backup_settings(paths)
        target = str(saved.get("targetDirectory") or "").strip()
        schedule = saved.get("schedule") if isinstance(saved.get("schedule"), dict) else {}
        if not target:
            if schedule.get("enabled"):
                raise ValueError("Backup target directory is required for scheduled backups.")
            return
        _require_target_ready(paths, saved)

    write_backup_settings(update, paths, readiness_verifier=readiness)
    return get_backup_status()


def queue_backup(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = payload if isinstance(payload, dict) else {}
    if str(request.get("confirmationText") or "") != BACKUP_CONFIRMATION:
        raise BackupFacadeError(
            "backup-confirmation-mismatch",
            f"confirmationText must be exactly: {BACKUP_CONFIRMATION}",
        )
    paths = load_paths()
    settings = _backup_settings(paths)
    _require_target_ready(paths, settings)
    job_id = f"backup-{uuid4().hex}"
    with _PENDING_LOCK:
        _PENDING[job_id] = {"paths": paths, "settings": settings, "trigger": "manual"}
    return {
        "accepted": True,
        "status": "queued",
        "jobId": job_id,
        "confirmationTextRequired": BACKUP_CONFIRMATION,
    }


def execute_backup(job_id: str) -> dict[str, Any]:
    with _PENDING_LOCK:
        request = _PENDING.pop(str(job_id), None)
    if request is None:
        raise BackupFacadeError("backup-job-not-found", "Backup job was not found.", status_code=404)
    result = _create_backup(
        request["paths"],
        request["settings"],
        trigger=str(request.get("trigger") or "manual"),
    )
    return _public_run(result, request["paths"]) or {"status": "completed"}


def verify_backup_by_id(backup_id: str) -> dict[str, Any]:
    clean_id = str(backup_id or "").strip()
    if not BACKUP_ID_RE.fullmatch(clean_id) or clean_id in {".", ".."}:
        raise BackupFacadeError("backup-id-invalid", "Backup identifier is invalid.")
    paths = load_paths()
    settings = _backup_settings(paths)
    target = str(settings.get("targetDirectory") or "").strip()
    if not target:
        raise BackupFacadeError("backup-target-not-configured", "Backup target directory is not configured.")
    _require_target_ready(paths, settings)
    backup_path = Path(target) / clean_id
    try:
        result = backup_engine.verify_backup(
            backup_path,
            expected_runtime_id=backup_engine.source_runtime_id(paths),
        )
    except Exception as exc:
        raise _facade_error(exc, paths) from None
    return _public_verification(result, paths)


def run_due_backup(now: datetime | None = None) -> dict[str, Any]:
    """Run one due in-process scheduled backup, without creating a system task."""

    with _SCHEDULE_LOCK:
        paths = load_paths()
        settings = _backup_settings(paths)
        schedule = settings.get("schedule") if isinstance(settings.get("schedule"), dict) else {}
        if schedule.get("enabled") is not True:
            return {"status": "skipped", "reason": "schedule-disabled"}

        timezone = resolve_timezone(
            paths,
            settings=read_settings(
                paths,
                redact_secrets=False,
                persist_defaults=False,
            ),
            group="schedule",
        )
        if now is None:
            local_now = datetime.now(timezone)
        elif now.tzinfo is None:
            local_now = now.replace(tzinfo=timezone)
        else:
            local_now = now.astimezone(timezone)
        time_of_day = str(schedule.get("timeOfDay") or "05:00")
        if local_now.strftime("%H:%M") < time_of_day:
            return {"status": "skipped", "reason": "before-scheduled-time", "scheduledTime": time_of_day}

        frequency = str(schedule.get("frequency") or "weekly")
        bucket = backup_engine.backup_due_bucket(frequency, local_now)
        status_before = _read_engine_status(paths)
        if str(status_before.get("lastSuccessfulScheduleBucket") or "") == bucket:
            return {"status": "skipped", "reason": "schedule-bucket-complete", "scheduleBucket": bucket}

        _require_target_ready(paths, settings)
        result = _create_backup(
            paths,
            settings,
            trigger="scheduled",
            schedule_bucket=bucket,
        )
        return {
            "status": str(result.get("status") or "completed"),
            "scheduleBucket": bucket,
            "backup": _public_run(result, paths),
        }


def _backup_settings(paths: RuntimePaths) -> dict[str, Any]:
    settings = read_settings(paths, redact_secrets=False, persist_defaults=False)
    backup = settings.get("backup") if isinstance(settings.get("backup"), dict) else {}
    return {
        "targetDirectory": str(backup.get("targetDirectory") or ""),
        "include": dict(backup.get("include") or {}),
        "retention": dict(backup.get("retention") or {}),
        "schedule": dict(backup.get("schedule") or {}),
    }


def _require_target_ready(paths: RuntimePaths, settings: dict[str, Any]) -> dict[str, Any]:
    target = str(settings.get("targetDirectory") or "").strip()
    if not target:
        raise BackupFacadeError("backup-target-not-configured", "Backup target directory is not configured.")
    try:
        validation = backup_engine.validate_backup_target(
            paths,
            target,
            include=settings.get("include"),
            rag_v2_root=resolve_rag_settings(paths).v2_store_path,
        )
    except Exception as exc:
        raise _facade_error(exc, paths) from None
    payload = _mapping(validation)
    valid = payload.get("valid")
    if valid is None:
        valid = payload.get("ready")
    if valid is False:
        code = str(payload.get("code") or "backup-target-invalid")
        raise BackupFacadeError(code, "Backup target directory failed safety validation.")
    return payload


def _create_backup(
    paths: RuntimePaths,
    settings: dict[str, Any],
    *,
    trigger: str,
    schedule_bucket: str | None = None,
) -> dict[str, Any]:
    retention = settings.get("retention") if isinstance(settings.get("retention"), dict) else {}
    try:
        return backup_engine.create_backup(
            paths,
            target_directory=str(settings.get("targetDirectory") or ""),
            include=settings.get("include"),
            retention={
                "maxBackups": int(retention.get("maxBackups") or 7),
                "maxAgeDays": int(retention.get("maxAgeDays") or 30),
            },
            trigger=trigger,
            schedule_bucket=schedule_bucket,
            rag_v2_root=resolve_rag_settings(paths).v2_store_path,
        )
    except Exception as exc:
        raise _facade_error(exc, paths) from None


def _read_engine_status(paths: RuntimePaths) -> dict[str, Any]:
    try:
        return _mapping(backup_engine.read_backup_status(paths))
    except Exception:
        return {}


def _public_status(
    paths: RuntimePaths,
    settings: dict[str, Any],
    engine_status: dict[str, Any],
) -> dict[str, Any]:
    target = str(settings.get("targetDirectory") or "").strip()
    readiness: dict[str, Any]
    if not target:
        readiness = {"configured": False, "ready": False, "code": "backup-target-not-configured"}
    else:
        try:
            validated = _require_target_ready(paths, settings)
            readiness = {
                "configured": True,
                "ready": True,
                **_allow(validated, ("freeBytes", "requiredBytes", "warnings")),
            }
        except BackupFacadeError as exc:
            readiness = {"configured": True, "ready": False, "code": exc.code, "error": str(exc)}
    return {
        "schemaVersion": 1,
        "settings": settings,
        "targetReadiness": readiness,
        "latestRun": _public_run(engine_status, paths),
        "lastSuccessfulScheduleBucket": engine_status.get("lastSuccessfulScheduleBucket"),
        "lastSuccessfulScheduledBackupId": engine_status.get("lastSuccessfulScheduledBackupId"),
        "capabilities": {
            "runNow": True,
            "scheduled": True,
            "verify": True,
            "restore": False,
        },
        "confirmationTextRequired": BACKUP_CONFIRMATION,
    }


def _public_run(value: Any, paths: RuntimePaths) -> dict[str, Any] | None:
    payload = _mapping(value)
    if not payload or not any(payload.get(key) for key in ("status", "backupId", "runId", "startedAt", "completedAt")):
        return None
    result = _allow(
        payload,
        (
            "runId",
            "backupId",
            "status",
            "trigger",
            "startedAt",
            "completedAt",
            "createdAt",
            "fileCount",
            "totalBytes",
            "manifestSha256",
            "errorCode",
            "warnings",
        ),
    )
    error = payload.get("error") or payload.get("errorDetail") or payload.get("errorMessage")
    if error:
        result["error"] = _safe_error_record(error, paths)
    verification = payload.get("verification")
    if isinstance(verification, dict):
        result["verification"] = _public_verification(verification, paths)
    retention = payload.get("retention")
    if isinstance(retention, dict):
        result["retention"] = {
            **_allow(retention, ("status", "deleted", "skipped", "verifiedBackupCount")),
            "errors": [
                _safe_error_record(item, paths)
                for item in (retention.get("errors") or [])[:50]
            ],
        }
    return result


def _public_verification(value: Any, paths: RuntimePaths) -> dict[str, Any]:
    payload = _mapping(value)
    result = _allow(payload, ("valid", "backupId", "fileCount", "totalBytes", "createdAt", "manifestSha256"))
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    result["errors"] = [_safe_error_record(item, paths) for item in errors[:50]]
    return result


def _safe_error_record(value: Any, paths: RuntimePaths) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            key: _safe_text(nested, paths)
            for key, nested in value.items()
            if key in {"code", "message", "reason", "type"}
        }
    return {"message": _safe_text(value, paths)}


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    return {}


def _allow(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload.get(key) for key in keys if key in payload}


def _facade_error(exc: Exception, paths: RuntimePaths) -> BackupFacadeError:
    if isinstance(exc, BackupFacadeError):
        return exc
    code = str(getattr(exc, "code", None) or "backup-operation-failed")
    status_code = 404 if "not-found" in code or "missing" in code else (409 if any(word in code for word in ("busy", "locked", "space", "conflict")) else 400)
    message = (
        _safe_text(str(exc), paths)
        if isinstance(exc, backup_engine.BackupError)
        else "Backup operation failed."
    )
    return BackupFacadeError(code, message or "Backup operation failed.", status_code=status_code)


def _safe_text(value: Any, paths: RuntimePaths) -> str:
    text = " ".join(str(value or "").split())[:500]
    replacements = {
        str(paths.home): "$ACTANARA_HOME",
        str(paths.db_path): "$ACTANARA_DATABASE",
        str(paths.diary_dir): "$ACTANARA_DIARY",
        str(paths.reports_dir): "$ACTANARA_REPORTS",
    }
    for raw, replacement in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if raw:
            text = text.replace(raw, replacement)
    return text

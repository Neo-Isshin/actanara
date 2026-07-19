"""Consistent, verifiable Actanara data backups.

This module deliberately has no restore entry point.  It snapshots a strict
allowlist of runtime data into a sibling staging directory, verifies the
result, and only then publishes it with a same-filesystem atomic rename.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import quote

from .paths import RuntimePaths
from .version import product_version


BACKUP_SCHEMA_VERSION = 1
BACKUP_FORMAT = "actanara-ai-assets-backup"
BACKUP_DIRECTORY_RE = re.compile(
    r"^actanara-backup-v1-\d{8}T\d{6}Z-[0-9a-f]{12}$"
)
BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RUNTIME_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
COPY_BUFFER_BYTES = 1024 * 1024
DISK_HEADROOM_BYTES = 8 * 1024 * 1024
SQLITE_BACKUP_TIMEOUT_SECONDS = 120.0

BACKUP_SELECTION_KEYS = (
    "database",
    "diaryMarkdown",
    "periodReports",
    "ragV2",
    "novaTaskExports",
    "settings",
    "workspaceAttribution",
    "runtimeManifests",
)
DEFAULT_BACKUP_SELECTION = {key: True for key in BACKUP_SELECTION_KEYS}
DEFAULT_RETENTION = {"maxBackups": 7, "maxAgeDays": 30}

_DIARY_SUFFIXES = {".md", ".markdown"}
_REPORT_SUFFIXES = {".md", ".markdown", ".json"}
_NOVA_TASK_SUFFIXES = {".md", ".json", ".jsonl", ".yaml", ".yml"}
_RAG_ROOT_FILES = ("manifest.json", "config.json", "build-runs.jsonl")
_RAG_ACTIVE_FILES = {
    "manifest.json",
    "index.jsonl",
    "chunks.jsonl",
    "embeddings.jsonl",
    "sources.jsonl",
    "build-report.json",
}
_RAG_MANIFEST_PATH_FIELDS = (
    "activeIndexPath",
    "activeManifestPath",
    "chunksPath",
    "embeddingsPath",
    "sourcesPath",
)
_NOVA_TASK_STATE_DIRS = (
    "work-graph",
    "candidate-reconciliation",
    "planning-import",
)
_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|password|secret|credential|private[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|authorization|cookie|headers?)",
    re.IGNORECASE,
)


class BackupError(RuntimeError):
    """A stable, user-safe backup failure."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = str(message)
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class _PlannedFile:
    item: str
    source: Path
    relative_path: str


@dataclass(frozen=True)
class _GeneratedFile:
    item: str
    relative_path: str
    content: bytes


@dataclass
class _BackupPlan:
    files: list[_PlannedFile] = field(default_factory=list)
    generated: list[_GeneratedFile] = field(default_factory=list)
    sqlite_source: Path | None = None
    sqlite_relative_path: str = "payload/database/actanara_data.sqlite3"
    item_states: dict[str, str] = field(default_factory=dict)
    estimated_bytes: int = 0


def normalize_backup_selection(include: Mapping[str, Any] | None = None) -> dict[str, bool]:
    """Return a complete strict allowlist selection."""

    if include is None:
        return dict(DEFAULT_BACKUP_SELECTION)
    if not isinstance(include, Mapping):
        raise BackupError("invalid-selection", "backup include selection must be an object")
    unknown = sorted(set(include) - set(BACKUP_SELECTION_KEYS))
    if unknown:
        raise BackupError(
            "invalid-selection",
            "unsupported backup include items: " + ", ".join(unknown),
        )
    selected = dict(DEFAULT_BACKUP_SELECTION)
    for key, value in include.items():
        if type(value) is not bool:
            raise BackupError("invalid-selection", f"backup include.{key} must be a boolean")
        selected[key] = value
    if not any(selected.values()):
        raise BackupError("invalid-selection", "at least one backup item must be selected")
    return selected


def source_runtime_id(paths: RuntimePaths) -> str:
    """Return a stable opaque identifier without exposing the runtime path."""

    canonical = str(paths.home.expanduser().resolve(strict=False))
    digest = hashlib.sha256(
        ("actanara-backup-runtime-v1\0" + canonical).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def validate_backup_target(
    paths: RuntimePaths,
    target_directory: str | os.PathLike[str],
    *,
    include: Mapping[str, Any] | None = None,
    rag_v2_root: Path | None = None,
) -> Path:
    """Validate and canonicalize an existing backup target directory."""

    selection = normalize_backup_selection(include)
    raw_text = os.fspath(target_directory)
    if not raw_text or "\0" in raw_text:
        raise BackupError("unsafe-target", "backup target must be a non-empty absolute path")
    raw = Path(raw_text)
    if not raw.is_absolute():
        raise BackupError("unsafe-target", "backup target must be an absolute path")
    if ".." in raw.parts:
        raise BackupError("unsafe-target", "backup target must not contain traversal components")
    try:
        raw_info = raw.lstat()
    except FileNotFoundError:
        raise BackupError("target-missing", "backup target directory does not exist") from None
    except OSError:
        raise BackupError("target-unavailable", "backup target directory is unavailable") from None
    if stat.S_ISLNK(raw_info.st_mode):
        raise BackupError("unsafe-target", "backup target must not be a symlink")
    if not stat.S_ISDIR(raw_info.st_mode):
        raise BackupError("unsafe-target", "backup target must be a directory")
    target = raw.resolve(strict=True)
    if not os.access(target, os.W_OK | os.X_OK):
        raise BackupError("target-not-writable", "backup target directory is not writable")

    for source in _selected_source_roots(paths, selection, rag_v2_root=rag_v2_root):
        canonical_source = source.expanduser().resolve(strict=False)
        if _is_relative_to(target, canonical_source) or _is_relative_to(canonical_source, target):
            raise BackupError(
                "target-source-overlap",
                "backup target must not overlap the runtime or a selected source",
            )
    return target


def read_backup_status(paths: RuntimePaths) -> dict[str, Any]:
    path = _backup_status_path(paths)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {
            "schemaVersion": BACKUP_SCHEMA_VERSION,
            "status": "never-run",
            "updatedAt": None,
        }
    return value if isinstance(value, dict) else {
        "schemaVersion": BACKUP_SCHEMA_VERSION,
        "status": "unavailable",
        "updatedAt": None,
    }


def create_backup(
    paths: RuntimePaths,
    *,
    target_directory: str | os.PathLike[str],
    include: Mapping[str, Any] | None = None,
    retention: Mapping[str, Any] | None = None,
    trigger: str = "manual",
    schedule_bucket: str | None = None,
    rag_v2_root: Path | None = None,
    actanara_version: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create and atomically publish one verified backup."""

    selection = normalize_backup_selection(include)
    retention_policy = _normalize_retention(retention)
    trigger_value = str(trigger or "manual").strip()
    if trigger_value not in {"manual", "scheduled"}:
        raise BackupError("invalid-trigger", "backup trigger must be manual or scheduled")
    clean_schedule_bucket = str(schedule_bucket or "").strip() or None
    if clean_schedule_bucket is not None and (
        len(clean_schedule_bucket) > 32
        or not re.fullmatch(r"[0-9A-Za-z-]+", clean_schedule_bucket)
    ):
        raise BackupError("invalid-schedule-bucket", "backup schedule bucket is invalid")
    if clean_schedule_bucket is not None and trigger_value != "scheduled":
        raise BackupError(
            "invalid-schedule-bucket",
            "backup schedule bucket is only valid for scheduled backups",
        )
    current = _normalized_now(now)
    run_id = uuid.uuid4().hex[:12]
    runtime_id = source_runtime_id(paths)

    with _backup_operation_lock(paths, run_id):
        previous_status = read_backup_status(paths)
        previous_schedule_fields = _successful_schedule_fields(previous_status)
        _write_backup_status(
            paths,
            {
                "schemaVersion": BACKUP_SCHEMA_VERSION,
                "runId": run_id,
                "status": "running",
                "trigger": trigger_value,
                "startedAt": current.isoformat(),
                "updatedAt": current.isoformat(),
                "sourceRuntimeId": runtime_id,
                **previous_schedule_fields,
            },
        )
        staging: Path | None = None
        published: Path | None = None
        published_identity: tuple[int, int] | None = None
        try:
            target = validate_backup_target(
                paths,
                target_directory,
                include=selection,
                rag_v2_root=rag_v2_root,
            )
            backup_id = (
                "actanara-backup-v1-"
                + current.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                + f"-{run_id}"
            )
            staging = target / f".{backup_id}.staging"
            published = target / backup_id
            if staging.exists() or published.exists():
                raise BackupError("backup-id-conflict", "backup destination already exists")
            staging.mkdir(mode=0o700)
            _fsync_directory(target)

            resolved_rag_root = rag_v2_root or paths.home / "reserved" / "rag" / "v2"
            with _rag_snapshot_lock(resolved_rag_root, enabled=selection["ragV2"]):
                plan = _build_backup_plan(
                    paths,
                    selection,
                    rag_v2_root=resolved_rag_root,
                )
                _require_disk_space(target, plan.estimated_bytes)
                records = _materialize_plan(plan, staging)

            manifest = _build_manifest(
                plan,
                records,
                backup_id=backup_id,
                run_id=run_id,
                created_at=current,
                version=actanara_version or _actanara_version(),
                runtime_id=runtime_id,
                trigger=trigger_value,
                selection=selection,
            )
            manifest_bytes = _json_bytes(manifest)
            _write_bytes_atomic(staging / "manifest.json", manifest_bytes)
            manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
            _write_bytes_atomic(
                staging / "manifest.sha256",
                (manifest_digest + "\n").encode("ascii"),
            )
            verification = _verify_backup_directory(
                staging,
                expected_runtime_id=runtime_id,
                require_final_name=False,
            )
            if not verification["valid"]:
                raise BackupError("self-verification-failed", "backup self-verification failed")
            _fsync_tree(staging)
            os.replace(staging, published)
            staging = None
            published_info = published.lstat()
            published_identity = (published_info.st_dev, published_info.st_ino)
            _fsync_directory(target)
            published_verification = _verify_backup_directory(
                published,
                expected_runtime_id=runtime_id,
                require_final_name=True,
            )
            if not published_verification["valid"]:
                raise BackupError(
                    "published-verification-failed",
                    "published backup verification failed",
                )

            try:
                retention_result = _apply_retention_locked(
                    target,
                    runtime_id=runtime_id,
                    max_backups=retention_policy["maxBackups"],
                    max_age_days=retention_policy["maxAgeDays"],
                    now=current,
                    keep_path=published,
                )
            except Exception as error:  # publication is already valid
                retention_result = {
                    "status": "warning",
                    "deleted": [],
                    "skipped": [],
                    "errors": [{"code": "retention-failed", "type": type(error).__name__}],
                }
            final_status = "completed" if not retention_result.get("errors") else "completed_with_warnings"
            result = {
                "schemaVersion": BACKUP_SCHEMA_VERSION,
                "runId": run_id,
                "status": final_status,
                "trigger": trigger_value,
                "backupId": backup_id,
                "backupPath": str(published),
                "manifestPath": str(published / "manifest.json"),
                "manifestSha256": manifest_digest,
                "fileCount": manifest["summary"]["fileCount"],
                "totalBytes": manifest["summary"]["totalBytes"],
                "sourceRuntimeId": runtime_id,
                "verification": published_verification,
                "retention": retention_result,
                "startedAt": current.isoformat(),
                "completedAt": datetime.now(timezone.utc).isoformat(),
                **previous_schedule_fields,
            }
            if trigger_value == "scheduled" and clean_schedule_bucket is not None:
                result["lastSuccessfulScheduleBucket"] = clean_schedule_bucket
                result["lastSuccessfulScheduledBackupId"] = backup_id
            _write_backup_status(paths, {**result, "updatedAt": result["completedAt"]})
            return result
        except Exception as error:
            if staging is not None:
                _remove_owned_staging(staging)
            if published is not None and published_identity is not None:
                _quarantine_owned_published(published, published_identity)
            safe_error = _safe_backup_error(error)
            failed_at = datetime.now(timezone.utc).isoformat()
            _write_backup_status(
                paths,
                {
                    "schemaVersion": BACKUP_SCHEMA_VERSION,
                    "runId": run_id,
                    "status": "failed",
                    "trigger": trigger_value,
                    "startedAt": current.isoformat(),
                    "completedAt": failed_at,
                    "updatedAt": failed_at,
                    "sourceRuntimeId": runtime_id,
                    "error": safe_error.as_dict(),
                    **previous_schedule_fields,
                },
            )
            raise safe_error from None


def verify_backup(
    backup_path: str | os.PathLike[str],
    *,
    expected_runtime_id: str | None = None,
) -> dict[str, Any]:
    """Verify a published backup without modifying it."""

    return _verify_backup_directory(
        Path(backup_path),
        expected_runtime_id=expected_runtime_id,
        require_final_name=True,
    )


def apply_retention(
    paths: RuntimePaths,
    target_directory: str | os.PathLike[str],
    *,
    runtime_id: str | None = None,
    max_backups: int = 7,
    max_age_days: int = 30,
    now: datetime | None = None,
    keep_path: Path | None = None,
) -> dict[str, Any]:
    """Delete only verified backups belonging to the selected runtime."""

    target = validate_backup_target(paths, target_directory)
    selected_runtime_id = runtime_id or source_runtime_id(paths)
    policy = _normalize_retention(
        {"maxBackups": max_backups, "maxAgeDays": max_age_days}
    )
    with _backup_operation_lock(paths, f"retention-{uuid.uuid4().hex[:12]}"):
        return _apply_retention_locked(
            target,
            runtime_id=selected_runtime_id,
            max_backups=policy["maxBackups"],
            max_age_days=policy["maxAgeDays"],
            now=_normalized_now(now),
            keep_path=keep_path,
        )


def backup_due_bucket(frequency: str, now: datetime) -> str:
    """Return the scheduler bucket for daily, weekly, or monthly cadence."""

    value = str(frequency or "").strip().lower()
    current = _schedule_now(now)
    if value == "daily":
        return current.date().isoformat()
    if value == "weekly":
        year, week, _ = current.isocalendar()
        return f"{year:04d}-W{week:02d}"
    if value == "monthly":
        return current.strftime("%Y-%m")
    raise BackupError("invalid-frequency", "backup frequency must be daily, weekly, or monthly")


def is_backup_due(*, frequency: str, now: datetime, last_success_bucket: str | None) -> bool:
    return backup_due_bucket(frequency, now) != str(last_success_bucket or "")


def _build_backup_plan(
    paths: RuntimePaths,
    selection: Mapping[str, bool],
    *,
    rag_v2_root: Path,
) -> _BackupPlan:
    plan = _BackupPlan(
        item_states={key: "not-selected" for key in BACKUP_SELECTION_KEYS}
    )
    for key, selected in selection.items():
        if selected:
            plan.item_states[key] = "not-present"

    if selection["database"]:
        _require_regular_file(paths.db_path, role="database")
        plan.sqlite_source = paths.db_path
        plan.estimated_bytes += _sqlite_estimated_bytes(paths.db_path)
        plan.item_states["database"] = "complete"

    if selection["diaryMarkdown"]:
        _add_tree(
            plan,
            item="diaryMarkdown",
            source_root=paths.diary_dir,
            destination_root="payload/diary",
            suffixes=_DIARY_SUFFIXES,
        )
    if selection["periodReports"]:
        _add_tree(
            plan,
            item="periodReports",
            source_root=paths.reports_dir,
            destination_root="payload/period-reports",
            suffixes=_REPORT_SUFFIXES,
        )
    if selection["ragV2"]:
        _add_rag_v2(plan, rag_v2_root)
    if selection["novaTaskExports"]:
        _add_exact_file(
            plan,
            item="novaTaskExports",
            source=paths.task_board_path,
            relative_path="payload/nova-task/TASK_BOARD.md",
        )
        _add_tree(
            plan,
            item="novaTaskExports",
            source_root=paths.task_intelligence_dir,
            destination_root="payload/nova-task/intelligence",
            suffixes=_NOVA_TASK_SUFFIXES,
        )
        for name in _NOVA_TASK_STATE_DIRS:
            _add_tree(
                plan,
                item="novaTaskExports",
                source_root=paths.state_dir / "nova-task" / name,
                destination_root=f"payload/nova-task/state/{name}",
                suffixes=_NOVA_TASK_SUFFIXES,
            )

    if selection["workspaceAttribution"]:
        for name in ("rules.json", "catalog.json"):
            _add_exact_file(
                plan,
                item="workspaceAttribution",
                source=paths.state_dir / "workspace-attribution" / name,
                relative_path=f"payload/workspace-attribution/{name}",
            )

    if selection["settings"] or selection["runtimeManifests"]:
        _add_config_snapshots(plan, paths, selection)

    _validate_plan_destinations(plan)
    return plan


def _add_config_snapshots(
    plan: _BackupPlan,
    paths: RuntimePaths,
    selection: Mapping[str, bool],
) -> None:
    with _settings_snapshot_lock(paths):
        if selection["settings"]:
            settings_path = paths.config_dir / "settings.json"
            if _path_entry_exists(settings_path):
                settings = _read_json_regular(settings_path, role="settings")
                sanitized = _sanitize_json(settings, paths)
                _assert_secret_safe(sanitized)
                _add_generated(
                    plan,
                    item="settings",
                    relative_path="payload/settings/settings.json",
                    payload=sanitized,
                )
        if selection["runtimeManifests"]:
            for name in ("runtime.json", "projects-registry.json", "sources-registry.json"):
                source = paths.config_dir / name
                if not _path_entry_exists(source):
                    continue
                payload = _read_json_regular(source, role="runtime-manifest")
                sanitized = _sanitize_json(payload, paths)
                if name == "runtime.json":
                    sanitized["instanceId"] = source_runtime_id(paths)
                    sanitized["backupSanitized"] = True
                _assert_secret_safe(sanitized)
                _add_generated(
                    plan,
                    item="runtimeManifests",
                    relative_path=f"payload/runtime-manifests/{name}",
                    payload=sanitized,
                )


def _add_rag_v2(plan: _BackupPlan, root: Path) -> None:
    if not _path_entry_exists(root):
        return
    _require_directory(root, role="nova-RAG v2 store")
    added: set[Path] = set()
    for name in _RAG_ROOT_FILES:
        source = root / name
        if _path_entry_exists(source):
            _add_exact_file(
                plan,
                item="ragV2",
                source=source,
                relative_path=f"payload/nova-rag-v2/{name}",
            )
            added.add(source.resolve(strict=True))

    manifest_path = root / "manifest.json"
    if not _path_entry_exists(manifest_path):
        return
    manifest = _read_json_regular(manifest_path, role="nova-RAG v2 manifest")
    active_roots: set[Path] = set()
    for field_name in _RAG_MANIFEST_PATH_FIELDS:
        value = manifest.get(field_name)
        if not value:
            continue
        raw = Path(str(value))
        if ".." in raw.parts:
            raise BackupError("unsafe-rag-manifest", "nova-RAG v2 manifest contains traversal")
        candidate = raw if raw.is_absolute() else root / raw
        if not _path_entry_exists(candidate):
            raise BackupError(
                "rag-active-file-missing",
                "nova-RAG v2 manifest referenced a missing active file",
            )
        _require_regular_file(candidate, role="nova-RAG v2 active file")
        resolved = candidate.expanduser().resolve(strict=False)
        if not _is_relative_to(resolved, root.resolve(strict=True)):
            raise BackupError("unsafe-rag-manifest", "nova-RAG v2 manifest path escaped its store")
        if resolved.name not in _RAG_ACTIVE_FILES:
            raise BackupError("unsafe-rag-manifest", "nova-RAG v2 manifest referenced an unsupported file")
        active_roots.add(resolved.parent)
    for active_root in sorted(active_roots, key=lambda path: path.as_posix()):
        if not _is_relative_to(active_root, root.resolve(strict=True) / "indexes" / "active"):
            raise BackupError("unsafe-rag-manifest", "nova-RAG v2 active directory escaped its boundary")
        for name in sorted(_RAG_ACTIVE_FILES):
            source = active_root / name
            if not _path_entry_exists(source):
                continue
            resolved = source.resolve(strict=True)
            if resolved in added:
                continue
            relative = resolved.relative_to(root.resolve(strict=True)).as_posix()
            _add_exact_file(
                plan,
                item="ragV2",
                source=resolved,
                relative_path=f"payload/nova-rag-v2/{relative}",
            )
            added.add(resolved)


def _add_tree(
    plan: _BackupPlan,
    *,
    item: str,
    source_root: Path,
    destination_root: str,
    suffixes: set[str],
) -> None:
    if not _path_entry_exists(source_root):
        return
    _require_directory(source_root, role=item)
    plan.item_states[item] = "complete"
    for source in _iter_allowed_tree(source_root, suffixes=suffixes):
        relative = source.relative_to(source_root).as_posix()
        _add_exact_file(
            plan,
            item=item,
            source=source,
            relative_path=f"{destination_root}/{relative}",
        )


def _add_exact_file(
    plan: _BackupPlan,
    *,
    item: str,
    source: Path,
    relative_path: str,
) -> None:
    if not _path_entry_exists(source):
        return
    info = _require_regular_file(source, role=item)
    _validate_relative_path(relative_path)
    plan.files.append(_PlannedFile(item, source, relative_path))
    plan.estimated_bytes += int(info.st_size)
    plan.item_states[item] = "complete"


def _add_generated(
    plan: _BackupPlan,
    *,
    item: str,
    relative_path: str,
    payload: Mapping[str, Any],
) -> None:
    content = _json_bytes(dict(payload))
    _validate_relative_path(relative_path)
    plan.generated.append(_GeneratedFile(item, relative_path, content))
    plan.estimated_bytes += len(content)
    plan.item_states[item] = "complete"


def _validate_plan_destinations(plan: _BackupPlan) -> None:
    destinations = [entry.relative_path for entry in plan.files]
    destinations.extend(entry.relative_path for entry in plan.generated)
    if plan.sqlite_source is not None:
        destinations.append(plan.sqlite_relative_path)
    if len(destinations) != len(set(destinations)):
        raise BackupError("destination-conflict", "backup source mapping produced duplicate paths")


def _materialize_plan(plan: _BackupPlan, staging: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if plan.sqlite_source is not None:
        destination = _destination(staging, plan.sqlite_relative_path)
        _copy_sqlite_database(plan.sqlite_source, destination)
        records.append(_file_record(destination, staging, item="database"))
    for entry in sorted(plan.files, key=lambda value: value.relative_path):
        destination = _destination(staging, entry.relative_path)
        _copy_regular_file(entry.source, destination)
        records.append(_file_record(destination, staging, item=entry.item))
    for entry in sorted(plan.generated, key=lambda value: value.relative_path):
        destination = _destination(staging, entry.relative_path)
        _write_bytes_atomic(destination, entry.content)
        records.append(_file_record(destination, staging, item=entry.item))
    records.sort(key=lambda value: value["path"])
    return records


def _build_manifest(
    plan: _BackupPlan,
    records: Sequence[dict[str, Any]],
    *,
    backup_id: str,
    run_id: str,
    created_at: datetime,
    version: str,
    runtime_id: str,
    trigger: str,
    selection: Mapping[str, bool],
) -> dict[str, Any]:
    item_records: list[dict[str, Any]] = []
    for item in BACKUP_SELECTION_KEYS:
        if not selection[item]:
            continue
        selected_records = [record for record in records if record["item"] == item]
        item_records.append(
            {
                "id": item,
                "status": plan.item_states[item],
                "fileCount": len(selected_records),
                "totalBytes": sum(int(record["size"]) for record in selected_records),
            }
        )
    manifest_files = [
        {key: record[key] for key in ("path", "sha256", "size")}
        for record in records
    ]
    return {
        "schemaVersion": BACKUP_SCHEMA_VERSION,
        "format": BACKUP_FORMAT,
        "backupId": backup_id,
        "runId": run_id,
        "createdAt": created_at.isoformat(),
        "actanaraVersion": str(version or "unknown"),
        "sourceRuntimeId": runtime_id,
        "trigger": trigger,
        "includedItems": item_records,
        "files": manifest_files,
        "summary": {
            "fileCount": len(manifest_files),
            "totalBytes": sum(int(record["size"]) for record in manifest_files),
        },
        "restoreContract": {
            "implemented": False,
            "mode": "future-verified-manifest-restore",
        },
    }


def _verify_backup_directory(
    root: Path,
    *,
    expected_runtime_id: str | None,
    require_final_name: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "valid": False,
        "backupId": root.name,
        "manifestPath": str(root / "manifest.json"),
        "sourceRuntimeId": None,
        "fileCount": 0,
        "totalBytes": 0,
        "errors": [],
    }
    try:
        if ".." in root.parts:
            raise BackupError("unsafe-backup-path", "backup path contains traversal")
        info = root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise BackupError("unsafe-backup-path", "backup path must be a real directory")
        if require_final_name and not BACKUP_DIRECTORY_RE.fullmatch(root.name):
            raise BackupError("unrecognized-backup", "backup directory name is not recognized")
        manifest_path = root / "manifest.json"
        digest_path = root / "manifest.sha256"
        manifest_bytes = _read_regular_bytes(
            manifest_path,
            role="backup manifest",
            max_bytes=MAX_MANIFEST_BYTES,
        )
        declared_digest = _read_regular_bytes(
            digest_path,
            role="backup manifest digest",
            max_bytes=256,
        ).decode("ascii").strip()
        actual_digest = hashlib.sha256(manifest_bytes).hexdigest()
        if not SHA256_RE.fullmatch(declared_digest) or declared_digest != actual_digest:
            raise BackupError("manifest-digest-mismatch", "backup manifest digest did not match")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BackupError("invalid-manifest", "backup manifest is not valid JSON") from None
        if not isinstance(manifest, dict):
            raise BackupError("invalid-manifest", "backup manifest must be an object")
        if manifest.get("schemaVersion") != BACKUP_SCHEMA_VERSION or manifest.get("format") != BACKUP_FORMAT:
            raise BackupError("invalid-manifest", "backup manifest schema or format is unsupported")
        if require_final_name and manifest.get("backupId") != root.name:
            raise BackupError("invalid-manifest", "backup manifest id does not match its directory")
        runtime_id = str(manifest.get("sourceRuntimeId") or "")
        if not RUNTIME_ID_RE.fullmatch(runtime_id):
            raise BackupError("invalid-manifest", "backup source runtime id is invalid")
        if expected_runtime_id is not None and runtime_id != expected_runtime_id:
            raise BackupError("runtime-mismatch", "backup belongs to a different runtime")
        try:
            datetime.fromisoformat(str(manifest["createdAt"]))
        except (KeyError, TypeError, ValueError):
            raise BackupError("invalid-manifest", "backup creation time is invalid") from None
        files = manifest.get("files")
        if not isinstance(files, list):
            raise BackupError("invalid-manifest", "backup manifest files must be a list")
        declared_paths: list[str] = []
        total_bytes = 0
        for record in files:
            if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
                raise BackupError("invalid-manifest", "backup manifest file record is invalid")
            relative = str(record.get("path") or "")
            _validate_relative_path(relative)
            if not SHA256_RE.fullmatch(str(record.get("sha256") or "")):
                raise BackupError("invalid-manifest", "backup manifest contains an invalid hash")
            if type(record.get("size")) is not int or record["size"] < 0:
                raise BackupError("invalid-manifest", "backup manifest contains an invalid size")
            candidate = _destination(root, relative)
            actual = _file_record(candidate, root, item="verify")
            if actual["sha256"] != record["sha256"] or actual["size"] != record["size"]:
                raise BackupError("payload-mismatch", "backup payload did not match its manifest")
            declared_paths.append(relative)
            total_bytes += int(record["size"])
        if declared_paths != sorted(declared_paths) or len(declared_paths) != len(set(declared_paths)):
            raise BackupError("invalid-manifest", "backup manifest paths must be sorted and unique")
        actual_paths = sorted(_all_payload_paths(root))
        if actual_paths != declared_paths:
            raise BackupError("unlisted-payload", "backup contains unlisted or missing payload files")
        summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
        if summary.get("fileCount") != len(files) or summary.get("totalBytes") != total_bytes:
            raise BackupError("invalid-manifest", "backup summary does not match its files")
        result.update(
            {
                "valid": True,
                "backupId": str(manifest.get("backupId") or root.name),
                "sourceRuntimeId": runtime_id,
                "fileCount": len(files),
                "totalBytes": total_bytes,
                "manifestSha256": actual_digest,
            }
        )
    except Exception as error:
        safe = _safe_backup_error(error)
        result["errors"] = [safe.as_dict()]
    return result


def _apply_retention_locked(
    target: Path,
    *,
    runtime_id: str,
    max_backups: int,
    max_age_days: int,
    now: datetime,
    keep_path: Path | None,
) -> dict[str, Any]:
    verified: list[tuple[datetime, Path, os.stat_result]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for candidate in sorted(target.iterdir(), key=lambda path: path.name):
        if not BACKUP_DIRECTORY_RE.fullmatch(candidate.name):
            continue
        try:
            info = candidate.lstat()
        except OSError:
            skipped.append({"backupId": candidate.name, "reason": "unavailable"})
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            skipped.append({"backupId": candidate.name, "reason": "unsafe-type"})
            continue
        verification = _verify_backup_directory(
            candidate,
            expected_runtime_id=runtime_id,
            require_final_name=True,
        )
        if not verification["valid"]:
            skipped.append({"backupId": candidate.name, "reason": "verification-failed"})
            continue
        try:
            manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
            created = datetime.fromisoformat(str(manifest["createdAt"]))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            skipped.append({"backupId": candidate.name, "reason": "invalid-created-at"})
            continue
        verified.append((created, candidate, info))
    verified.sort(key=lambda value: (value[0], value[1].name), reverse=True)
    keep_resolved = keep_path.resolve(strict=False) if keep_path is not None else None
    delete: list[tuple[Path, os.stat_result]] = []
    for index, (created, candidate, info) in enumerate(verified):
        if keep_resolved is not None and candidate.resolve(strict=False) == keep_resolved:
            continue
        age_days = max(0, (_normalized_now(now) - created.astimezone(timezone.utc)).days)
        if index >= max_backups or age_days >= max_age_days:
            delete.append((candidate, info))
    deleted: list[str] = []
    for candidate, verified_info in delete:
        try:
            current_info = candidate.lstat()
            if (
                stat.S_ISLNK(current_info.st_mode)
                or not stat.S_ISDIR(current_info.st_mode)
                or (current_info.st_dev, current_info.st_ino)
                != (verified_info.st_dev, verified_info.st_ino)
            ):
                skipped.append({"backupId": candidate.name, "reason": "changed-after-verification"})
                continue
            if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
                skipped.append({"backupId": candidate.name, "reason": "safe-delete-unavailable"})
                continue
            shutil.rmtree(candidate)
            _fsync_directory(target)
            deleted.append(candidate.name)
        except OSError as error:
            errors.append({"code": "retention-delete-failed", "type": type(error).__name__})
    return {
        "status": "completed" if not errors else "warning",
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
        "verifiedBackupCount": len(verified),
    }


def _selected_source_roots(
    paths: RuntimePaths,
    selection: Mapping[str, bool],
    *,
    rag_v2_root: Path | None,
) -> list[Path]:
    roots = [paths.home]
    if selection["database"]:
        roots.append(paths.db_path)
    if selection["diaryMarkdown"]:
        roots.append(paths.diary_dir)
    if selection["periodReports"]:
        roots.append(paths.reports_dir)
    if selection["ragV2"]:
        roots.append(rag_v2_root or paths.home / "reserved" / "rag" / "v2")
    if selection["novaTaskExports"]:
        roots.extend((paths.task_board_path, paths.task_intelligence_dir, paths.state_dir / "nova-task"))
    if selection["workspaceAttribution"]:
        roots.append(paths.state_dir / "workspace-attribution")
    return roots


def _iter_allowed_tree(root: Path, *, suffixes: set[str]) -> Iterator[Path]:
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError:
            raise BackupError("source-read-failed", "selected backup source could not be read") from None
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                raise BackupError("source-read-failed", "selected backup source changed during scan") from None
            if stat.S_ISLNK(info.st_mode):
                raise BackupError("unsafe-source", "selected backup source contains a symlink")
            candidate = Path(entry.path)
            if stat.S_ISDIR(info.st_mode):
                pending.append(candidate)
            elif stat.S_ISREG(info.st_mode):
                if candidate.suffix.lower() in suffixes:
                    yield candidate
            else:
                raise BackupError("unsupported-source-type", "selected backup source contains an unsupported file type")


def _require_directory(path: Path, *, role: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError:
        raise BackupError("source-unavailable", f"{role} is unavailable") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise BackupError("unsafe-source", f"{role} must be a real directory")
    return info


def _require_regular_file(path: Path, *, role: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError:
        raise BackupError("source-unavailable", f"{role} is unavailable") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise BackupError("unsupported-source-type", f"{role} must be a regular file")
    return info


def _copy_regular_file(source: Path, destination: Path) -> None:
    before = _require_regular_file(source, role="backup source file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source, source_flags)
    except OSError:
        raise BackupError("source-open-failed", "backup source file could not be opened safely") from None
    destination_fd: int | None = None
    try:
        opened = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise BackupError("source-changed", "backup source file changed during validation")
        destination_fd = os.open(destination, destination_flags, 0o600)
        while True:
            chunk = os.read(source_fd, COPY_BUFFER_BYTES)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        os.fsync(destination_fd)
        after = os.fstat(source_fd)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(getattr(after, key) != getattr(before, key) for key in stable_fields):
            raise BackupError("source-changed", "backup source file changed while it was copied")
    except BackupError:
        raise
    except OSError:
        raise BackupError("file-copy-failed", "backup file copy failed") from None
    finally:
        os.close(source_fd)
        if destination_fd is not None:
            os.close(destination_fd)


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    _require_regular_file(source, role="database")
    destination.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{quote(str(source), safe='/')}?mode=ro"
    started = time.monotonic()

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() - started > SQLITE_BACKUP_TIMEOUT_SECONDS:
            raise BackupError("sqlite-timeout", "SQLite backup exceeded its time limit")

    try:
        source_connection = sqlite3.connect(uri, uri=True, timeout=10.0)
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.execute("PRAGMA query_only=ON")
            source_connection.execute("PRAGMA busy_timeout=10000")
            source_connection.backup(
                destination_connection,
                pages=256,
                progress=progress,
                sleep=0.05,
            )
            destination_connection.commit()
            destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            journal_mode = str(
                destination_connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            ).lower()
            if journal_mode != "delete":
                raise BackupError(
                    "sqlite-journal-finalize-failed",
                    "SQLite backup journal mode could not be finalized",
                )
            destination_connection.commit()
        finally:
            destination_connection.close()
            source_connection.close()
        check_connection = sqlite3.connect(
            f"file:{quote(str(destination), safe='/')}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            rows = [str(row[0]) for row in check_connection.execute("PRAGMA quick_check")]
        finally:
            check_connection.close()
        if rows != ["ok"]:
            raise BackupError("sqlite-quick-check-failed", "SQLite backup quick_check failed")
        with destination.open("rb") as handle:
            os.fsync(handle.fileno())
    except BackupError:
        raise
    except (OSError, sqlite3.Error):
        raise BackupError("sqlite-backup-failed", "SQLite consistent backup failed") from None


def _sqlite_estimated_bytes(source: Path) -> int:
    uri = f"file:{quote(str(source), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            return max(source.stat().st_size, page_count * page_size)
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise BackupError("sqlite-size-failed", "SQLite backup size could not be estimated") from None


def _require_disk_space(target: Path, estimate: int) -> None:
    try:
        free = int(shutil.disk_usage(target).free)
    except OSError:
        raise BackupError("disk-space-unavailable", "backup target free space could not be determined") from None
    required = max(0, int(estimate)) + max(DISK_HEADROOM_BYTES, int(estimate) // 20)
    if free < required:
        raise BackupError("insufficient-disk-space", "backup target does not have enough free space")


def _file_record(path: Path, root: Path, *, item: str) -> dict[str, Any]:
    info = _require_regular_file(path, role="backup payload file")
    relative = path.relative_to(root).as_posix()
    _validate_relative_path(relative)
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise BackupError("payload-changed", "backup payload changed during verification")
            while True:
                chunk = os.read(descriptor, COPY_BUFFER_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                raise BackupError("payload-changed", "backup payload changed during verification")
        finally:
            os.close(descriptor)
    except BackupError:
        raise
    except OSError:
        raise BackupError("payload-read-failed", "backup payload could not be read safely") from None
    return {
        "item": item,
        "path": relative,
        "sha256": digest.hexdigest(),
        "size": int(info.st_size),
    }


def _all_payload_paths(root: Path) -> list[str]:
    found: list[str] = []
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            raise BackupError("payload-read-failed", "backup payload tree could not be read") from None
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            if relative in {"manifest.json", "manifest.sha256"}:
                continue
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise BackupError("unsafe-payload", "backup payload contains a symlink")
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                found.append(relative)
            else:
                raise BackupError("unsafe-payload", "backup payload contains an unsupported file type")
    return found


def _read_regular_bytes(path: Path, *, role: str, max_bytes: int | None = None) -> bytes:
    info = _require_regular_file(path, role=role)
    if max_bytes is not None and info.st_size > max_bytes:
        raise BackupError("file-too-large", f"{role} exceeds its size limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise BackupError("source-changed", f"{role} changed during validation")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, COPY_BUFFER_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise BackupError("file-too-large", f"{role} exceeds its size limit")
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                raise BackupError("source-changed", f"{role} changed while it was read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except BackupError:
        raise
    except OSError:
        raise BackupError("source-read-failed", f"{role} could not be read safely") from None


def _read_json_regular(path: Path, *, role: str) -> dict[str, Any]:
    content = _read_regular_bytes(path, role=role, max_bytes=MAX_MANIFEST_BYTES)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BackupError("invalid-json-source", f"{role} is not valid JSON") from None
    if not isinstance(value, dict):
        raise BackupError("invalid-json-source", f"{role} must be a JSON object")
    return value


def _sanitize_json(value: Any, paths: RuntimePaths, *, key: str = "") -> Any:
    normalized_key = key.lower().replace("_", "").replace("-", "")
    if normalized_key.endswith("apikeyenv"):
        return copy.deepcopy(value)
    if normalized_key == "secretref":
        return {"configured": bool(value)}
    if _SECRET_KEY_RE.search(key):
        if key.lower().replace("_", "") in {"apikey", "password"}:
            return ""
        return None
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_json(child_value, paths, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_json(item, paths, key=key) for item in value]
    if isinstance(value, str) and (value.startswith("/") or value.startswith("~/")):
        candidate = Path(value).expanduser().resolve(strict=False)
        home = paths.home.expanduser().resolve(strict=False)
        if _is_relative_to(candidate, home):
            relative = candidate.relative_to(home).as_posix()
            return "${ACTANARA_HOME}" + (f"/{relative}" if relative != "." else "")
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return f"<redacted-external-path:{digest}>"
    return copy.deepcopy(value)


def _assert_secret_safe(value: Any, *, key: str = "") -> None:
    normalized = key.lower().replace("_", "").replace("-", "")
    if normalized.endswith("apikeyenv"):
        return
    if _SECRET_KEY_RE.search(key):
        if normalized == "secretref" and value in ({"configured": True}, {"configured": False}):
            return
        if value not in (None, "", {}, []):
            raise BackupError("settings-redaction-failed", "sanitized settings retained a secret-like value")
        return
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _assert_secret_safe(child_value, key=str(child_key))
    elif isinstance(value, list):
        for child in value:
            _assert_secret_safe(child, key=key)


@contextmanager
def _settings_snapshot_lock(paths: RuntimePaths) -> Iterator[None]:
    root = paths.state_dir / "settings-transactions"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _require_private_state_directory(root, role="settings transaction state")
    lock_path = root / ".lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            for transaction_dir in root.iterdir():
                if not transaction_dir.is_dir():
                    continue
                journal_path = transaction_dir / "journal.json"
                try:
                    journal = json.loads(journal_path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    continue
                except (OSError, json.JSONDecodeError):
                    raise BackupError(
                        "settings-transaction-unresolved",
                        "settings transaction state must be recovered before backup",
                    ) from None
                if not isinstance(journal, dict) or journal.get("status") not in {"committed", "compensated"}:
                    raise BackupError(
                        "settings-transaction-unresolved",
                        "settings transaction state must be recovered before backup",
                    )
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _backup_operation_lock(paths: RuntimePaths, run_id: str) -> Iterator[None]:
    root = paths.state_dir / "backup"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _require_private_state_directory(root, role="backup state")
    try:
        os.chmod(root, 0o700)
    except OSError:
        raise BackupError("backup-state-unsafe", "backup state directory is unavailable") from None
    lock_path = root / "operation.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("backup-busy", "another backup operation is already running") from None
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps({"runId": run_id, "lockedAt": datetime.now(timezone.utc).isoformat()}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _rag_snapshot_lock(root: Path, *, enabled: bool) -> Iterator[None]:
    if not enabled or not root.exists():
        yield
        return
    _require_directory(root, role="nova-RAG v2 store")
    locks = root / "locks"
    if locks.exists():
        _require_directory(locks, role="nova-RAG v2 lock directory")
    else:
        locks.mkdir(mode=0o700)
    lock_path = locks / "sync-promote.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("rag-v2-busy", "nova-RAG v2 is busy; retry the backup later") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _backup_status_path(paths: RuntimePaths) -> Path:
    return paths.state_dir / "backup" / "status.json"


def _write_backup_status(paths: RuntimePaths, payload: Mapping[str, Any]) -> None:
    _write_bytes_atomic(_backup_status_path(paths), _json_bytes(dict(payload)))


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _destination(root: Path, relative_path: str) -> Path:
    _validate_relative_path(relative_path)
    candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    canonical_root = root.resolve(strict=True)
    canonical_candidate = candidate.resolve(strict=False)
    if not _is_relative_to(canonical_candidate, canonical_root):
        raise BackupError("path-escape", "backup relative path escaped its boundary")
    return candidate


def _validate_relative_path(value: str) -> None:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise BackupError("unsafe-relative-path", "backup manifest contains an unsafe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupError("unsafe-relative-path", "backup manifest contains an unsafe relative path")


def _remove_owned_staging(path: Path) -> None:
    if not path.name.startswith(".actanara-backup-v1-") or not path.name.endswith(".staging"):
        return
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return
    if getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        shutil.rmtree(path)
        _fsync_directory(path.parent)


def _quarantine_owned_published(path: Path, identity: tuple[int, int]) -> None:
    """Remove a just-published run from the recognizable success namespace."""

    if not BACKUP_DIRECTORY_RE.fullmatch(path.name):
        return
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != identity
    ):
        return
    quarantine = path.parent / f".failed-{path.name}-{uuid.uuid4().hex[:8]}"
    try:
        os.replace(path, quarantine)
        _fsync_directory(path.parent)
    except OSError:
        return
    _remove_failed_quarantine(quarantine)


def _remove_failed_quarantine(path: Path) -> None:
    if not path.name.startswith(".failed-actanara-backup-v1-"):
        return
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return
    if getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        shutil.rmtree(path)
        _fsync_directory(path.parent)


def _fsync_tree(root: Path) -> None:
    directories = [root]
    for current, dirnames, _filenames in os.walk(root, followlinks=False):
        base = Path(current)
        for name in dirnames:
            directories.append(base / name)
    for directory in reversed(directories):
        _fsync_directory(directory)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _normalize_retention(value: Mapping[str, Any] | None) -> dict[str, int]:
    payload = dict(DEFAULT_RETENTION if value is None else value)
    unknown = sorted(set(payload) - {"maxBackups", "maxAgeDays"})
    if unknown:
        raise BackupError("invalid-retention", "unsupported retention fields: " + ", ".join(unknown))
    result: dict[str, int] = {}
    for key in ("maxBackups", "maxAgeDays"):
        raw = payload.get(key, DEFAULT_RETENTION[key])
        if type(raw) is bool:
            raise BackupError("invalid-retention", f"retention {key} must be a positive integer")
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            raise BackupError("invalid-retention", f"retention {key} must be a positive integer") from None
        if parsed <= 0:
            raise BackupError("invalid-retention", f"retention {key} must be a positive integer")
        result[key] = parsed
    return result


def _normalized_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _schedule_now(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _successful_schedule_fields(status: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    bucket = str(status.get("lastSuccessfulScheduleBucket") or "").strip()
    backup_id = str(status.get("lastSuccessfulScheduledBackupId") or "").strip()
    if bucket:
        result["lastSuccessfulScheduleBucket"] = bucket
    if backup_id:
        result["lastSuccessfulScheduledBackupId"] = backup_id
    return result


def _safe_backup_error(error: Exception) -> BackupError:
    if isinstance(error, BackupError):
        return error
    return BackupError("backup-failed", f"backup failed ({type(error).__name__})")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _actanara_version() -> str:
    return product_version()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise BackupError("source-unavailable", "selected backup source is unavailable") from None


def _require_private_state_directory(path: Path, *, role: str) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise BackupError("backup-state-unsafe", f"{role} is unavailable") from None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
    ):
        raise BackupError("backup-state-unsafe", f"{role} failed ownership or link validation")


__all__ = [
    "BACKUP_FORMAT",
    "BACKUP_SCHEMA_VERSION",
    "BACKUP_SELECTION_KEYS",
    "BackupError",
    "apply_retention",
    "backup_due_bucket",
    "create_backup",
    "is_backup_due",
    "normalize_backup_selection",
    "read_backup_status",
    "source_runtime_id",
    "validate_backup_target",
    "verify_backup",
]

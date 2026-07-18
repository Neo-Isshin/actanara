"""Runtime asset location selection and non-destructive legacy import support."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

DEFAULT_ACTANARA_HOME = Path.home() / ".actanara"
DEFAULT_LEGACY_DIARY_ROOT = DEFAULT_ACTANARA_HOME / "artifacts" / "diary"
DEFAULT_BOOTSTRAP_PATH = Path("~/.config/actanara/location.json").expanduser()
RUNTIME_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RuntimePaths:
    home: Path
    config_dir: Path
    db_path: Path
    archives_dir: Path
    diary_dir: Path
    reports_dir: Path
    task_board_path: Path
    task_intelligence_dir: Path
    snapshots_dir: Path
    state_dir: Path
    legacy_diary_root: Path | None
    legacy_rag_root: Path | None


@dataclass(frozen=True)
class PathValidation:
    candidate: Path
    exists: bool
    initialized: bool
    writable: bool
    valid: bool
    issues: tuple[str, ...]


@dataclass(frozen=True)
class LegacyImportResult:
    copied: int
    matched: int
    skipped: int
    conflicts: tuple[str, ...]


def _absolute(candidate: Path) -> Path:
    return candidate.expanduser().absolute()


def _bootstrap_path() -> Path:
    return _absolute(Path(os.getenv("ACTANARA_LOCATION_FILE", str(DEFAULT_BOOTSTRAP_PATH))))


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _runtime_paths(home: Path, manifest: dict | None = None) -> RuntimePaths:
    home = _absolute(home)
    manifest = manifest or _read_json(home / "config" / "runtime.json")
    generated_value = manifest.get("generatedDiaryRoot")
    diary_dir = _absolute(Path(generated_value)) if generated_value else home / "artifacts" / "diary"
    legacy_value = manifest.get("legacyDiaryRoot")
    legacy_root = _absolute(Path(legacy_value)) if legacy_value else None
    db_value = manifest.get("databasePath")
    archives_value = manifest.get("archivesRoot")
    reports_value = manifest.get("reportsRoot")
    task_board_value = manifest.get("taskBoardPath")
    task_intelligence_value = manifest.get("taskIntelligenceRoot")
    snapshots_value = manifest.get("snapshotsRoot")
    return RuntimePaths(
        home=home,
        config_dir=home / "config",
        db_path=_absolute(Path(db_value)) if db_value else home / "data" / "actanara_data.sqlite3",
        archives_dir=_absolute(Path(archives_value)) if archives_value else home / "sources" / "archives",
        diary_dir=diary_dir,
        reports_dir=_absolute(Path(reports_value)) if reports_value else home / "artifacts" / "reports",
        task_board_path=_absolute(Path(task_board_value)) if task_board_value else home / "artifacts" / "tasks" / "TASK_BOARD.md",
        task_intelligence_dir=_absolute(Path(task_intelligence_value)) if task_intelligence_value else home / "artifacts" / "tasks" / "intelligence",
        snapshots_dir=_absolute(Path(snapshots_value)) if snapshots_value else home / "snapshots",
        state_dir=home / "state",
        legacy_diary_root=legacy_root,
        legacy_rag_root=(legacy_root / "__diary_rag") if legacy_root else None,
    )


def load_paths() -> RuntimePaths:
    """Resolve the selected instance without creating directories."""
    env_home = os.getenv("ACTANARA_HOME")
    if env_home:
        return _runtime_paths(Path(env_home))
    bootstrap = _read_json(_bootstrap_path())
    selected = bootstrap.get("actanaraHome")
    return _runtime_paths(Path(selected) if selected else DEFAULT_ACTANARA_HOME)


def runtime_paths_for_home(candidate: Path, *, legacy_diary_root: Path | None = None) -> RuntimePaths:
    """Build runtime paths for a candidate home without creating files or directories."""
    home = _absolute(candidate)
    legacy_root = _absolute(legacy_diary_root) if legacy_diary_root else None
    manifest = _read_json(home / "config" / "runtime.json")
    if not manifest:
        manifest = {"generatedDiaryRoot": str(home / "artifacts" / "diary")}
        if legacy_root:
            manifest["legacyDiaryRoot"] = str(legacy_root)
    return _runtime_paths(home, manifest)


def default_oneliner_runtime_home() -> Path:
    """Return the user-facing one-liner default runtime home."""
    return _absolute(Path("~/.actanara"))


def validate_home(candidate: Path) -> PathValidation:
    home = _absolute(candidate)
    issues: list[str] = []
    exists = home.exists()
    if exists and not home.is_dir():
        issues.append("candidate is not a directory")
    manifest = home / "config" / "runtime.json"
    initialized = manifest.exists()
    if initialized:
        content = _read_json(manifest)
        if content.get("schemaVersion") != RUNTIME_SCHEMA_VERSION:
            issues.append("unsupported runtime schema version")
    check_at = home if exists and home.is_dir() else home.parent
    writable = check_at.exists() and os.access(check_at, os.W_OK)
    if not writable:
        issues.append("candidate is not writable")
    return PathValidation(
        candidate=home,
        exists=exists,
        initialized=initialized,
        writable=writable,
        valid=not issues,
        issues=tuple(issues),
    )


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def initialize_home(candidate: Path, *, legacy_diary_root: Path | None = None) -> RuntimePaths:
    """Create the Runtime directory layout without touching the reserved RAG namespace."""
    home = _absolute(candidate)
    legacy_root = _absolute(legacy_diary_root) if legacy_diary_root else None
    manifest_seed = {"generatedDiaryRoot": str(home / "artifacts" / "diary")}
    if legacy_root:
        manifest_seed["legacyDiaryRoot"] = str(legacy_root)
    paths = _runtime_paths(home, manifest_seed)
    directories = (
        paths.config_dir,
        paths.db_path.parent,
        paths.archives_dir,
        paths.home / "sources" / "manifests",
        paths.diary_dir,
        paths.reports_dir / "weekly",
        paths.reports_dir / "monthly",
        paths.home / "artifacts" / "learning",
        paths.task_intelligence_dir,
        paths.home / "assets" / "tools",
        paths.home / "assets" / "skills",
        paths.home / "assets" / "storage",
        paths.snapshots_dir / "dashboard",
        paths.snapshots_dir / "reports",
        paths.state_dir / "jobs",
        paths.state_dir / "locks",
        paths.state_dir / "cache",
        paths.state_dir / "logs",
        paths.state_dir / "tmp",
        paths.state_dir / "backups",
        paths.state_dir / "migration",
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    manifest_path = paths.config_dir / "runtime.json"
    if not manifest_path.exists():
        _write_json_atomic(
            manifest_path,
            {
                "instanceId": "local-primary",
                "schemaVersion": RUNTIME_SCHEMA_VERSION,
                "createdAt": datetime.now().astimezone().isoformat(),
                "generatedDiaryRoot": str(home / "artifacts" / "diary"),
                "ragMode": "legacy-external",
                **({"legacyDiaryRoot": str(legacy_root)} if legacy_root else {}),
            },
        )
    for registry_name, registry_key in (
        ("projects-registry.json", "projects"),
        ("sources-registry.json", "sources"),
    ):
        registry_path = paths.config_dir / registry_name
        if not registry_path.exists():
            _write_json_atomic(registry_path, {"version": RUNTIME_SCHEMA_VERSION, registry_key: []})
    return _runtime_paths(home)


def update_runtime_diary_root(candidate: Path, diary_root: Path, *, legacy_diary_root: Path | None = None) -> RuntimePaths:
    """Persist the generated diary root in the runtime manifest."""
    return update_runtime_manifest_paths(candidate, generated_diary_root=diary_root, legacy_diary_root=legacy_diary_root)


def update_runtime_manifest_paths(
    candidate: Path,
    *,
    generated_diary_root: Path | None = None,
    legacy_diary_root: Path | None = None,
    database_path: Path | None = None,
    snapshots_root: Path | None = None,
    reports_root: Path | None = None,
    archives_root: Path | None = None,
    task_board_path: Path | None = None,
    task_intelligence_root: Path | None = None,
) -> RuntimePaths:
    """Persist path selections in the runtime manifest and create their parent dirs."""
    home = _absolute(candidate)
    manifest_path = home / "config" / "runtime.json"
    manifest = _read_json(manifest_path)
    if not manifest:
        manifest = {
            "instanceId": "local-primary",
            "schemaVersion": RUNTIME_SCHEMA_VERSION,
            "createdAt": datetime.now().astimezone().isoformat(),
            "ragMode": "legacy-external",
        }
    manifest["schemaVersion"] = RUNTIME_SCHEMA_VERSION
    if generated_diary_root is not None:
        manifest["generatedDiaryRoot"] = str(_absolute(generated_diary_root))
    if legacy_diary_root is not None:
        manifest["legacyDiaryRoot"] = str(_absolute(legacy_diary_root))
    updates = {
        "databasePath": database_path,
        "snapshotsRoot": snapshots_root,
        "reportsRoot": reports_root,
        "archivesRoot": archives_root,
        "taskBoardPath": task_board_path,
        "taskIntelligenceRoot": task_intelligence_root,
    }
    for key, value in updates.items():
        if value is not None:
            manifest[key] = str(_absolute(value))
    _write_json_atomic(manifest_path, manifest)
    paths = _runtime_paths(home)
    for directory in (
        paths.db_path.parent,
        paths.archives_dir,
        paths.diary_dir,
        paths.reports_dir / "weekly",
        paths.reports_dir / "monthly",
        paths.task_board_path.parent,
        paths.task_intelligence_dir,
        paths.snapshots_dir / "dashboard",
        paths.snapshots_dir / "reports",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    if paths.legacy_diary_root:
        paths.legacy_diary_root.mkdir(parents=True, exist_ok=True)
    return _runtime_paths(home)


def _persist_selection(paths: RuntimePaths) -> None:
    _write_json_atomic(
        _bootstrap_path(),
        {
            "actanaraHome": str(paths.home),
            "selectedAt": datetime.now().astimezone().isoformat(),
            "version": RUNTIME_SCHEMA_VERSION,
        },
    )


def persist_runtime_selection(paths: RuntimePaths) -> dict:
    """Persist the selected runtime pointer without modifying the runtime home."""
    _persist_selection(paths)
    return {
        "bootstrapPath": str(_bootstrap_path()),
        "actanaraHome": str(paths.home),
        "selectedAt": datetime.now().astimezone().isoformat(),
        "version": RUNTIME_SCHEMA_VERSION,
    }


def select_home(
    candidate: Path, mode: Literal["use", "initialize", "import_legacy"] = "use"
) -> RuntimePaths:
    if mode == "use":
        check = validate_home(candidate)
        if not check.valid or not check.initialized:
            raise ValueError(f"invalid initialized ACTANARA_HOME: {', '.join(check.issues) or 'runtime manifest missing'}")
        paths = _runtime_paths(candidate)
    elif mode in {"initialize", "import_legacy"}:
        paths = initialize_home(candidate)
        if mode == "import_legacy":
            import_legacy_assets(paths)
    else:
        raise ValueError(f"unknown selection mode: {mode}")
    _persist_selection(paths)
    return paths


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_tree(source: Path, destination: Path) -> LegacyImportResult:
    copied = matched = skipped = 0
    conflicts: list[str] = []
    if not source.exists():
        return LegacyImportResult(copied, matched, skipped, tuple(conflicts))
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        target = destination / item.relative_to(source)
        if target.exists():
            if _checksum(item) == _checksum(target):
                matched += 1
            else:
                conflicts.append(str(target))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied += 1
    return LegacyImportResult(copied, matched, skipped, tuple(conflicts))


def import_legacy_assets(paths: RuntimePaths, legacy_root: Path | None = None) -> LegacyImportResult:
    """Copy legacy non-RAG assets into an initialized home without altering the source."""
    source = _absolute(legacy_root or paths.legacy_diary_root or DEFAULT_LEGACY_DIARY_ROOT)
    totals = [0, 0, 0]
    conflicts: list[str] = []

    daily_root = source / "__diary_daily"
    if daily_root.exists():
        for date_dir in daily_root.iterdir():
            if not date_dir.is_dir():
                continue
            result = _copy_tree(date_dir / "_filtered", paths.archives_dir / date_dir.name / "filtered")
            totals = [a + b for a, b in zip(totals, (result.copied, result.matched, result.skipped))]
            conflicts.extend(result.conflicts)
            for raw_dir in date_dir.iterdir():
                if raw_dir.is_dir() and raw_dir.name != "_filtered":
                    result = _copy_tree(raw_dir, paths.archives_dir / date_dir.name / "raw" / raw_dir.name)
                    totals = [a + b for a, b in zip(totals, (result.copied, result.matched, result.skipped))]
                    conflicts.extend(result.conflicts)

    mappings = (
        (source, paths.diary_dir, "diary-*"),
    )
    for root, target, pattern in mappings:
        if pattern:
            for directory in root.glob(pattern):
                if directory.is_dir():
                    result = _copy_tree(directory, target / directory.name.removeprefix("diary-"))
                    totals = [a + b for a, b in zip(totals, (result.copied, result.matched, result.skipped))]
                    conflicts.extend(result.conflicts)
        else:
            result = _copy_tree(root, target)
            totals = [a + b for a, b in zip(totals, (result.copied, result.matched, result.skipped))]
            conflicts.extend(result.conflicts)
    for source_file, target in (
        (source / "TASK_BOARD.md", paths.task_board_path),
        (source / "lessons.jsonl", paths.home / "artifacts" / "learning" / "lessons.jsonl"),
        (source / "infrastructure.jsonl", paths.home / "artifacts" / "learning" / "infrastructure.jsonl"),
    ):
        if not source_file.exists():
            continue
        if target.exists():
            if _checksum(source_file) == _checksum(target):
                totals[1] += 1
            else:
                conflicts.append(str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
            totals[0] += 1
    for report in source.glob("summary-*.md"):
        category = "weekly" if "week" in report.name.lower() else "monthly"
        target = paths.reports_dir / category / report.name
        if target.exists():
            if _checksum(report) == _checksum(target):
                totals[1] += 1
            else:
                conflicts.append(str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report, target)
            totals[0] += 1
    return LegacyImportResult(totals[0], totals[1], totals[2], tuple(conflicts))

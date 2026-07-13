"""Retention pruning for nova-RAG v2 index snapshots."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .rag_settings import RagSettings, resolve_rag_settings


DEFAULT_KEEP_ACTIVE_RUNS = 1
DEFAULT_KEEP_CANDIDATES = 0


def prune_v2_index_store(
    settings: RagSettings | None = None,
    *,
    active_run_id: str,
    keep_active_runs: int = DEFAULT_KEEP_ACTIVE_RUNS,
    keep_candidates: int = DEFAULT_KEEP_CANDIDATES,
) -> dict[str, Any]:
    """Prune v2 active/candidate run directories inside the v2 store.

    The default production policy keeps only the currently promoted active run
    and no candidate snapshots. Historical manifests and logs are left intact.
    """
    resolved = settings or resolve_rag_settings()
    current_run_id = str(active_run_id or "").strip()
    if not current_run_id:
        raise ValueError("active_run_id is required")
    keep_active_count = int(keep_active_runs)
    keep_candidate_count = int(keep_candidates)
    if keep_active_count < 1:
        raise ValueError("keep_active_runs must be at least 1")
    if keep_candidate_count < 0:
        raise ValueError("keep_candidates must be non-negative")

    root = resolved.v2_store_path
    active_root = root / "indexes" / "active"
    candidates_root = root / "indexes" / "candidates"
    active_entries = _run_entries(active_root)
    candidate_entries = _run_entries(candidates_root)

    keep_active_ids = {current_run_id}
    extra_active = keep_active_count - 1
    for entry in _newest_first(active_entries):
        if entry.name == current_run_id:
            continue
        if extra_active > 0:
            keep_active_ids.add(entry.name)
            extra_active -= 1

    keep_candidate_ids = {entry.name for entry in _newest_first(candidate_entries)[:keep_candidate_count]}
    active_deleted, active_errors = _prune_entries(active_entries, active_root, keep_active_ids)
    candidate_deleted, candidate_errors = _prune_entries(candidate_entries, candidates_root, keep_candidate_ids)
    return {
        "status": "completed" if not active_errors and not candidate_errors else "partial",
        "activeRunId": current_run_id,
        "policy": {
            "keepActiveRuns": keep_active_count,
            "keepCandidates": keep_candidate_count,
        },
        "roots": {
            "active": str(active_root),
            "candidates": str(candidates_root),
        },
        "kept": {
            "activeRunIds": sorted(keep_active_ids),
            "candidateRunIds": sorted(keep_candidate_ids),
        },
        "deleted": {
            "activeRuns": active_deleted,
            "candidates": candidate_deleted,
        },
        "errors": active_errors + candidate_errors,
        "mutationPolicy": {
            "legacyMutated": False,
            "settingsMutated": False,
            "serverLifecycleChanged": False,
            "rootManifestMutated": False,
            "writesRestrictedToV2Store": True,
        },
    }


def retention_policy_manifest() -> dict[str, Any]:
    return {
        "activeRuns": {
            "keep": DEFAULT_KEEP_ACTIVE_RUNS,
            "mode": "current-active-only",
        },
        "candidates": {
            "keep": DEFAULT_KEEP_CANDIDATES,
            "mode": "delete-after-successful-promotion",
        },
        "manifestBackups": {
            "keep": "all",
            "mode": "small-file-audit-log",
        },
    }


def retention_result_manifest(result: dict[str, Any]) -> dict[str, Any]:
    deleted = result.get("deleted") if isinstance(result.get("deleted"), dict) else {}
    return {
        "status": result.get("status"),
        "policy": result.get("policy"),
        "deletedCounts": {
            "activeRuns": len(deleted.get("activeRuns") or []),
            "candidates": len(deleted.get("candidates") or []),
        },
        "errorCount": len(result.get("errors") or []),
    }


def _run_entries(root: Path) -> list[Path]:
    try:
        return sorted(
            [entry for entry in root.iterdir() if entry.is_dir() or entry.is_symlink()],
            key=lambda entry: entry.name,
        )
    except OSError:
        return []


def _newest_first(entries: list[Path]) -> list[Path]:
    return sorted(entries, key=lambda entry: (_mtime_ns(entry), entry.name), reverse=True)


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _prune_entries(entries: list[Path], root: Path, keep_ids: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deleted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for entry in entries:
        if entry.name in keep_ids:
            continue
        try:
            bytes_removed = _remove_run_entry(entry, root)
            deleted.append({"runId": entry.name, "path": str(entry), "bytes": bytes_removed})
        except Exception as exc:
            errors.append({"runId": entry.name, "path": str(entry), "error": str(exc)})
    return deleted, errors


def _remove_run_entry(path: Path, root: Path) -> int:
    _require_contained_direct_child(path, root)
    if not path.exists() and not path.is_symlink():
        return 0
    size = _tree_size(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)
    return size


def _require_contained_direct_child(path: Path, root: Path) -> None:
    root_resolved = root.resolve()
    parent_resolved = path.parent.resolve()
    if parent_resolved != root_resolved:
        raise ValueError(f"refusing to delete non-run path: {path}")
    path_resolved = path.resolve()
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path is outside the v2 index store boundary: {path}") from exc
    if path_resolved == root_resolved:
        raise ValueError(f"refusing to delete index root: {path}")


def _tree_size(path: Path) -> int:
    try:
        if path.is_symlink() or path.is_file():
            return path.lstat().st_size
        total = 0
        for child in path.rglob("*"):
            try:
                if child.is_file() or child.is_symlink():
                    total += child.lstat().st_size
            except OSError:
                continue
        return total
    except OSError:
        return 0

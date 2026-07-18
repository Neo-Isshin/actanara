"""Public identity for the source tree that loaded a running service."""

from __future__ import annotations

import json
import re
from pathlib import Path
import tomllib
from typing import Any


_FULL_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_RUNTIME_SOURCE_MANIFEST = ".actanara-runtime-source.json"
_MAX_MANIFEST_PARENT_DEPTH = 8
_MAX_RUNTIME_SOURCE_MANIFEST_BYTES = 1024 * 1024
_MAX_PROJECT_METADATA_BYTES = 256 * 1024


def _manifest_commit(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    if (
        type(payload.get("schemaVersion")) is not int
        or payload.get("schemaVersion") != 2
        or payload.get("product") != "actanara"
    ):
        return None
    git = payload.get("git")
    if not isinstance(git, dict) or git.get("available") is not True:
        return None
    commit = git.get("commit")
    if not isinstance(commit, str) or not _FULL_COMMIT_RE.fullmatch(commit):
        return None
    return commit


def _is_actanara_project_root(root: Path) -> bool:
    metadata = root / "pyproject.toml"
    try:
        details = metadata.lstat()
        if metadata.is_symlink() or not metadata.is_file():
            return False
        if details.st_size > _MAX_PROJECT_METADATA_BYTES:
            return False
        payload = tomllib.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    project = payload.get("project")
    return isinstance(project, dict) and project.get("name") == "actanara"


def _project_root_for_loaded_file(loaded_file: Path) -> Path | None:
    for depth, parent in enumerate(loaded_file.parents):
        if depth >= _MAX_MANIFEST_PARENT_DEPTH:
            break
        metadata = parent / "pyproject.toml"
        if not metadata.exists() and not metadata.is_symlink():
            continue
        if not _is_actanara_project_root(parent):
            return None
        try:
            relative = loaded_file.relative_to(parent)
        except ValueError:
            return None
        if not relative.parts or relative.parts[0] not in {"advanced", "src"}:
            return None
        return parent
    return None


def _read_runtime_source_manifest(project_root: Path) -> Any:
    manifest = project_root / _RUNTIME_SOURCE_MANIFEST
    try:
        details = manifest.lstat()
        if manifest.is_symlink() or not manifest.is_file():
            return None
        if details.st_size > _MAX_RUNTIME_SOURCE_MANIFEST_BYTES:
            return None
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None


def loaded_source_commit(module_file: str | Path) -> str | None:
    """Return only the full commit from the release containing ``module_file``.

    The module path is resolved so a stable Runtime symlink still identifies the
    concrete release that Python actually loaded.  Missing or invalid metadata
    is represented as ``None``; no filesystem path or manifest content escapes
    this helper.
    """

    try:
        loaded_file = Path(module_file).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not loaded_file.is_file():
        return None

    project_root = _project_root_for_loaded_file(loaded_file)
    if project_root is None:
        return None
    return _manifest_commit(_read_runtime_source_manifest(project_root))


__all__ = ["loaded_source_commit"]

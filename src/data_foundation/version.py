"""Actanara product-version authority for source and installed runtimes."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
import tomllib


PROJECT_NAME = "actanara"
UNKNOWN_VERSION = "unknown"


def product_version() -> str:
    """Return the active source version, then fall back to package metadata."""

    source_version = _source_version()
    if source_version:
        return source_version
    try:
        installed = str(importlib.metadata.version(PROJECT_NAME)).strip()
    except importlib.metadata.PackageNotFoundError:
        return UNKNOWN_VERSION
    return installed or UNKNOWN_VERSION


def _source_version() -> str | None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = payload.get("project") if isinstance(payload, dict) else None
    if not isinstance(project, dict) or project.get("name") != PROJECT_NAME:
        return None
    version = str(project.get("version") or "").strip()
    return version or None


__all__ = ["PROJECT_NAME", "UNKNOWN_VERSION", "product_version"]

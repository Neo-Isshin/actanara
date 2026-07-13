"""Resolve the effective RAG read index for legacy/v2 modes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .rag_settings import RagSettings, resolve_rag_settings
except ImportError:  # pragma: no cover - direct script fallback
    from rag_settings import RagSettings, resolve_rag_settings  # type: ignore


@dataclass(frozen=True)
class ActiveRagIndex:
    source: str
    index_path: Path | None
    ready: bool
    reason: str
    manifest_path: Path | None = None
    manifest_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "indexPath": str(self.index_path) if self.index_path else None,
            "ready": self.ready,
            "reason": self.reason,
            "manifestPath": str(self.manifest_path) if self.manifest_path else None,
            "manifestStatus": self.manifest_status,
        }


def resolve_active_rag_index(settings: RagSettings | None = None) -> ActiveRagIndex:
    """Return the index file that search should read for the configured mode."""
    resolved = settings or resolve_rag_settings()
    if not resolved.enabled or resolved.mode == "disabled":
        return ActiveRagIndex(
            source="disabled",
            index_path=None,
            ready=False,
            reason="nova-RAG is disabled",
        )
    if resolved.mode in {"legacy", "v2-shadow"}:
        return ActiveRagIndex(
            source="retired",
            index_path=None,
            ready=False,
            reason=f"{resolved.mode}-mode-retired; v2 active manifest is the only production search source",
        )
    if resolved.mode == "v2":
        return _resolve_v2_active_index(resolved)
    return ActiveRagIndex(
        source="retired",
        index_path=None,
        ready=False,
        reason="unknown-mode; legacy fallback is retired",
    )


def _resolve_v2_active_index(settings: RagSettings) -> ActiveRagIndex:
    manifest_path = settings.v2_store_path / "manifest.json"
    manifest = _read_json(manifest_path)
    status = str(manifest.get("status") or "missing")
    if status != "active":
        return ActiveRagIndex(
            source="v2",
            index_path=None,
            ready=False,
            reason=f"v2-manifest-not-active:{status}",
            manifest_path=manifest_path,
            manifest_status=status,
        )
    active = _active_index_path(manifest)
    if active and active.exists():
        return ActiveRagIndex(
            source="v2",
            index_path=active,
            ready=True,
            reason="v2-active-ready",
            manifest_path=manifest_path,
            manifest_status=status,
        )
    return ActiveRagIndex(
        source="v2",
        index_path=active,
        ready=False,
        reason="v2-active-index-missing",
        manifest_path=manifest_path,
        manifest_status=status,
    )


def _active_index_path(manifest: dict[str, Any]) -> Path | None:
    value = manifest.get("activeIndexPath")
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if path.suffix == ".jsonl":
        return path
    nested = path / "index.jsonl"
    if nested.exists():
        return nested
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

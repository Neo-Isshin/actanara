"""Shared read-only adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Protocol


@dataclass(frozen=True)
class Cursor:
    value: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceArtifact:
    tool_key: str
    path: Path
    artifact_type: str
    fingerprint: str | None = None


@dataclass(frozen=True)
class NormalizedEvent:
    tool_key: str
    external_event_key: str
    external_session_key: str
    occurred_at: datetime
    event_type: str
    payload: dict[str, Any]


class ToolAdapter(Protocol):
    tool_key: str
    adapter_version: str
    capabilities: set[str]

    def discover_sources(self) -> Iterable[SourceArtifact]: ...

    def read_incremental(
        self, artifact: SourceArtifact, cursor: Cursor | None
    ) -> Iterable[NormalizedEvent]: ...

    def fingerprint(self, artifact: SourceArtifact) -> str: ...

"""Shared read-only adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Protocol


@dataclass
class Cursor:
    """Persistent adapter cursor plus transient state for one ingestion read.

    ``value`` is the last cursor committed with the artifact's normalized
    events.  The remaining fields are deliberately transient: adapters fill
    them while reading and ingestion persists ``next_value`` only after the
    surrounding artifact transaction succeeds.
    """

    value: dict[str, Any] = field(default_factory=dict)
    requested_business_dates: frozenset[str] = field(default_factory=frozenset)
    policy_hash: str = ""
    next_value: dict[str, Any] | None = field(default=None, init=False)
    managed: bool = field(default=False, init=False)
    read_mode: str = field(default="full", init=False)
    reset_required: bool = field(default=False, init=False)
    force_full_reset: bool = field(default=False, init=False)
    write_business_dates: frozenset[str] = field(default_factory=frozenset, init=False)
    reset_business_dates: frozenset[str] = field(default_factory=frozenset, init=False)
    data_bytes_read: int = field(default=0, init=False)
    validation_bytes_read: int = field(default=0, init=False)
    source_stat_at_open: tuple[int, int, int, int, int] | None = field(default=None, init=False, repr=False)
    _observed_min_date: str | None = field(default=None, init=False, repr=False)
    _observed_max_date: str | None = field(default=None, init=False, repr=False)
    _event_counts: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def observe_business_date(self, value: str) -> None:
        if not self.managed:
            return
        if self._observed_min_date is None or value < self._observed_min_date:
            self._observed_min_date = value
        if self._observed_max_date is None or value > self._observed_max_date:
            self._observed_max_date = value
        self._event_counts[value] = self._event_counts.get(value, 0) + 1

    def covered_event_count(self, values: Iterable[str]) -> int:
        return sum(self._event_counts.get(value, 0) for value in values)


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

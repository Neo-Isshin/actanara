"""Persistent source registry independent of consumer applications."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from ..db import connect
from ..paths import RuntimePaths


@dataclass(frozen=True)
class RegisteredTool:
    tool_key: str
    display_name: str
    adapter_version: str
    capabilities: tuple[str, ...]
    enabled: bool
    retired_at: str | None


class ToolRegistry:
    def __init__(self, paths: RuntimePaths):
        self.paths = paths

    def register(
        self,
        *,
        tool_key: str,
        display_name: str,
        adapter_version: str,
        capabilities: set[str],
        enabled: bool = False,
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        encoded = json.dumps(sorted(capabilities))
        with connect(self.paths) as connection:
            connection.execute(
                """
                INSERT INTO tool_sources(
                    tool_key, display_name, adapter_version, capabilities_json,
                    enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tool_key) DO UPDATE SET
                    display_name=excluded.display_name,
                    adapter_version=excluded.adapter_version,
                    capabilities_json=excluded.capabilities_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (tool_key, display_name, adapter_version, encoded, int(enabled), now, now),
            )

    def set_enabled(self, tool_key: str, enabled: bool) -> None:
        with connect(self.paths) as connection:
            connection.execute(
                "UPDATE tool_sources SET enabled = ?, updated_at = ? WHERE tool_key = ?",
                (int(enabled), datetime.now().astimezone().isoformat(), tool_key),
            )

    def retire(self, tool_key: str) -> None:
        now = datetime.now().astimezone().isoformat()
        with connect(self.paths) as connection:
            connection.execute(
                "UPDATE tool_sources SET enabled = 0, retired_at = ?, updated_at = ? WHERE tool_key = ?",
                (now, now, tool_key),
            )

    def list(self) -> list[RegisteredTool]:
        with connect(self.paths, read_only=True) as connection:
            rows = connection.execute("SELECT * FROM tool_sources ORDER BY tool_key").fetchall()
        return [
            RegisteredTool(
                tool_key=row["tool_key"],
                display_name=row["display_name"],
                adapter_version=row["adapter_version"],
                capabilities=tuple(json.loads(row["capabilities_json"])),
                enabled=bool(row["enabled"]),
                retired_at=row["retired_at"],
            )
            for row in rows
        ]

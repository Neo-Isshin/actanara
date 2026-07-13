"""Nova system component registry.

This registry records local system surfaces such as Dashboard, RAG v2,
Foundation pipeline and skill inventories. It is operational metadata only; it
does not grant write permissions or switch runtime sources.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .db import connect, migrate
from .paths import RuntimePaths, initialize_home, load_paths


REGISTRY_VERSION = "system-registry-v1"


@dataclass(frozen=True)
class SystemComponent:
    component_key: str
    display_name: str
    component_type: str
    authority: str
    status: str
    version: str | None
    capabilities: tuple[str, ...]
    entrypoints: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]
    enabled: bool
    retired_at: str | None


def default_system_components(paths: RuntimePaths | None = None) -> list[dict[str, Any]]:
    selected = paths or load_paths()
    return [
        {
            "component_key": "foundation.sqlite",
            "display_name": "Nova Data Foundation SQLite",
            "component_type": "foundation",
            "authority": "NOVA_HOME",
            "status": "active",
            "version": REGISTRY_VERSION,
            "capabilities": ["sqlite-read-model", "snapshot-store", "materialization-state"],
            "entrypoints": [{"kind": "sqlite", "path": str(selected.db_path)}],
            "metadata": {"writesAllowedByRegistry": False},
        },
        {
            "component_key": "foundation.pipeline",
            "display_name": "Daily Foundation Pipeline",
            "component_type": "pipeline",
            "authority": "checked-in command",
            "status": "active",
            "version": REGISTRY_VERSION,
            "capabilities": ["daily-diary-generation", "foundation-materialization", "rag-v2-final-sync"],
            "entrypoints": [{"kind": "command", "value": "python advanced/pipeline/run_daily_pipeline.py [YYYY-MM-DD]"}],
            "metadata": {"stableBoundary": True},
        },
        {
            "component_key": "dashboard.server",
            "display_name": "Dashboard Server",
            "component_type": "dashboard",
            "authority": "settings.json + app routers",
            "status": "active",
            "version": REGISTRY_VERSION,
            "capabilities": ["settings-api", "foundation-ops-api", "rag-readonly-api", "diary-ui"],
            "entrypoints": [{"kind": "module", "value": "src/dashboard/app/main.py"}],
            "metadata": {"mutationScope": "settings-and-approved-ops"},
        },
        {
            "component_key": "rag.v2",
            "display_name": "RAG v2",
            "component_type": "rag",
            "authority": "rag.mode=v2",
            "status": "active",
            "version": REGISTRY_VERSION,
            "capabilities": ["index-sync", "read-only-search", "agentic-evidence"],
            "entrypoints": [
                {"kind": "command", "value": "python src/agentic_rag/rag_v2_sync.py"},
                {"kind": "module", "value": "src/agentic_rag/embedding_server.py"},
            ],
            "metadata": {"legacyRagRetired": True, "externalAgentApi": "read-only"},
        },
        {
            "component_key": "skills.inventory",
            "display_name": "Skill Inventory",
            "component_type": "skill",
            "authority": "observed local skill roots",
            "status": "observed",
            "version": REGISTRY_VERSION,
            "capabilities": ["skill-discovery", "skill-readonly-inspection"],
            "entrypoints": [{"kind": "service", "value": "src/dashboard/app/services/skills.py"}],
            "metadata": {"writesAllowedByRegistry": False},
        },
        {
            "component_key": "agents.inventory",
            "display_name": "Agent Inventory",
            "component_type": "agent",
            "authority": "observed local session roots",
            "status": "observed",
            "version": REGISTRY_VERSION,
            "capabilities": ["agent-session-readonly-inspection"],
            "entrypoints": [{"kind": "service", "value": "src/dashboard/app/services/agents.py"}],
            "metadata": {"writesAllowedByRegistry": False},
        },
        {
            "component_key": "llm.provider.catalog",
            "display_name": "LLM Provider Catalog",
            "component_type": "provider",
            "authority": "Nova scrubbed OpenClaw-derived catalog",
            "status": "active",
            "version": REGISTRY_VERSION,
            "capabilities": ["provider-presets", "secret-redaction", "custom-provider"],
            "entrypoints": [{"kind": "module", "value": "src/data_foundation/llm_provider_catalog.py"}],
            "metadata": {"secretsInCatalog": False},
        },
    ]


def register_default_system_components(paths: RuntimePaths | None = None) -> dict[str, Any]:
    selected = _paths(paths)
    migrate(selected)
    now = datetime.now().astimezone().isoformat()
    rows = default_system_components(selected)
    with connect(selected) as connection:
        for row in rows:
            connection.execute(
                """
                INSERT INTO system_components(
                    component_key, display_name, component_type, authority, status, version,
                    capabilities_json, entrypoints_json, metadata_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(component_key) DO UPDATE SET
                    display_name=excluded.display_name,
                    component_type=excluded.component_type,
                    authority=excluded.authority,
                    status=excluded.status,
                    version=excluded.version,
                    capabilities_json=excluded.capabilities_json,
                    entrypoints_json=excluded.entrypoints_json,
                    metadata_json=excluded.metadata_json,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    row["component_key"],
                    row["display_name"],
                    row["component_type"],
                    row["authority"],
                    row["status"],
                    row.get("version"),
                    json.dumps(sorted(row.get("capabilities", []))),
                    json.dumps(row.get("entrypoints", []), ensure_ascii=False),
                    json.dumps(row.get("metadata", {}), ensure_ascii=False, sort_keys=True),
                    int(row.get("enabled", True)),
                    now,
                    now,
                ),
            )
    return system_registry_status(selected)


def system_registry_status(paths: RuntimePaths | None = None) -> dict[str, Any]:
    selected = _paths(paths)
    try:
        migrate(selected)
        rows = _list_components(selected)
        parse_status = "ok"
        issues: list[dict[str, str]] = []
    except Exception as error:
        rows = []
        parse_status = "error"
        issues = [{"code": "registry-read-error", "message": str(error)}]
    components = [_component_to_dict(row) for row in rows]
    return {
        "status": "ok" if parse_status == "ok" else "attention",
        "parseStatus": parse_status,
        "registryVersion": REGISTRY_VERSION,
        "authority": {
            "writesAllowed": False,
            "sourceSwitchAllowed": False,
            "taskWriteAllowed": False,
            "promptMutationAllowed": False,
        },
        "counts": {
            "components": len(components),
            "enabled": sum(1 for item in components if item["enabled"]),
            "active": sum(1 for item in components if item["status"] == "active"),
            "byType": _counts_by_type(components),
        },
        "components": components,
        "issues": issues,
    }


def _list_components(paths: RuntimePaths) -> list[SystemComponent]:
    with connect(paths, read_only=True) as connection:
        rows = connection.execute("SELECT * FROM system_components ORDER BY component_type, component_key").fetchall()
    return [
        SystemComponent(
            component_key=row["component_key"],
            display_name=row["display_name"],
            component_type=row["component_type"],
            authority=row["authority"],
            status=row["status"],
            version=row["version"],
            capabilities=tuple(json.loads(row["capabilities_json"])),
            entrypoints=tuple(json.loads(row["entrypoints_json"])),
            metadata=json.loads(row["metadata_json"]),
            enabled=bool(row["enabled"]),
            retired_at=row["retired_at"],
        )
        for row in rows
    ]


def _component_to_dict(component: SystemComponent) -> dict[str, Any]:
    return {
        "componentKey": component.component_key,
        "displayName": component.display_name,
        "componentType": component.component_type,
        "authority": component.authority,
        "status": component.status,
        "version": component.version,
        "capabilities": list(component.capabilities),
        "entrypoints": list(component.entrypoints),
        "metadata": component.metadata,
        "enabled": component.enabled,
        "retiredAt": component.retired_at,
    }


def _counts_by_type(components: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for component in components:
        key = str(component.get("componentType") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _paths(paths: RuntimePaths | None) -> RuntimePaths:
    if paths:
        return paths
    return load_paths()

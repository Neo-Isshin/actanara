"""Supported external tool catalog and path rediscovery helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .external_tool_definitions import TOOL_CATALOG, fields_for_tool_home
from .paths import RuntimePaths
from .settings import default_external_tool_settings, read_settings, resolve_external_tool_paths, write_operator_settings


def supported_external_tool_catalog() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "tools": [
            {"id": tool_id, **definition}
            for tool_id, definition in TOOL_CATALOG.items()
        ],
    }


def rediscover_external_tools(paths: RuntimePaths | None = None) -> dict[str, Any]:
    configured = resolve_external_tool_paths(paths)
    candidates = _candidate_homes(Path.home())
    discoveries = []
    updates: dict[str, dict[str, str]] = {}
    for tool_id, definition in TOOL_CATALOG.items():
        current_home = _path_str((configured.get(tool_id) or {}).get("home"))
        homes = [home for home in candidates.get(tool_id, []) if _matches_tool(home, definition)]
        for home in homes:
            same_home = bool(current_home) and _same_path(home, Path(current_home))
            if same_home:
                status = "unchanged"
            elif current_home and tool_id != "openclaw":
                status = "changed"
            else:
                status = "new"
            instance_id = tool_id
            if status == "new" and tool_id == "openclaw" and current_home:
                instance_id = _next_instance_id("openclaw", configured)
            update = _fields_for_home(tool_id, home)
            discoveries.append(
                {
                    "tool": tool_id,
                    "instanceId": instance_id,
                    "name": definition["name"],
                    "path": str(home),
                    "configuredPath": current_home,
                    "status": status,
                    "update": update,
                }
            )
            if status in {"new", "changed"}:
                updates[instance_id] = update
    return {
        "schemaVersion": 1,
        "checkedAt": datetime.now().astimezone().isoformat(),
        "catalog": supported_external_tool_catalog(),
        "discoveries": discoveries,
        "suggestedUpdates": updates,
        "summary": {
            "detected": len(discoveries),
            "new": sum(1 for item in discoveries if item["status"] == "new"),
            "changed": sum(1 for item in discoveries if item["status"] == "changed"),
        },
    }


def add_external_tool_instance(tool_id: str, home_path: str, paths: RuntimePaths | None = None, *, instance_id: str | None = None) -> dict[str, Any]:
    if tool_id not in TOOL_CATALOG:
        raise ValueError(f"unsupported external tool: {tool_id}")
    home = Path(home_path).expanduser().absolute()
    if not home.exists() or not home.is_dir():
        raise ValueError("tool home path must be an existing directory")
    settings = read_settings(paths)
    external = settings.get("externalTools") if isinstance(settings.get("externalTools"), dict) else {}
    target_id = str(instance_id or tool_id).strip() or tool_id
    if target_id in external and _path_str((external.get(target_id) or {}).get("home")) and not _same_path(home, Path(_path_str(external[target_id]["home"]))):
        target_id = _next_instance_id(tool_id, external)
    update = {target_id: _fields_for_home(tool_id, home)}
    updated = write_operator_settings({"externalTools": update}, paths)
    return {"added": target_id, "tool": tool_id, "path": str(home), "externalTools": updated.get("externalTools", {})}


def _candidate_homes(home: Path) -> dict[str, list[Path]]:
    return {tool_id: _existing_dirs(_expand_home_candidates(home, definition)) for tool_id, definition in TOOL_CATALOG.items()}


def _expand_home_candidates(home: Path, definition: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    for pattern in definition.get("homeCandidates") or []:
        raw = str(pattern)
        if raw.startswith("~/"):
            relative = raw[2:]
            if any(char in relative for char in "*?["):
                candidates.extend(home.glob(relative))
            else:
                candidates.append(home / relative)
        else:
            candidate = Path(raw).expanduser()
            if any(char in raw for char in "*?["):
                candidates.extend(candidate.parent.glob(candidate.name))
            else:
                candidates.append(candidate)
    return candidates


def _existing_dirs(paths: list[Path]) -> list[Path]:
    result = []
    seen = set()
    for path in paths:
        expanded = path.expanduser().absolute()
        marker = str(expanded)
        if marker not in seen and expanded.is_dir():
            seen.add(marker)
            result.append(expanded)
    return result


def _matches_tool(home: Path, definition: dict[str, Any]) -> bool:
    return any((home / marker).exists() for marker in definition.get("homeMarkers") or [])


def _fields_for_home(tool_id: str, home: Path) -> dict[str, str]:
    return fields_for_tool_home(tool_id, home)


def _path_str(value: Any) -> str:
    return str(value or "").strip()


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return left.expanduser().absolute() == right.expanduser().absolute()


def _next_instance_id(base: str, configured: dict[str, Any]) -> str:
    idx = 2
    while f"{base}-{idx}" in configured:
        idx += 1
    return f"{base}-{idx}"

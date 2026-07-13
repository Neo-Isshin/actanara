"""Read-only dependency profile checks for onboarding."""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DependencyItem:
    kind: str
    name: str
    required: bool = True
    description: str = ""


PROFILE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "core-foundation": {
        "label": "Core/Foundation",
        "defaultEnabled": True,
        "items": (
            DependencyItem("python", "python3", description="Python 3 runtime"),
            DependencyItem("module", "sqlite3", description="SQLite standard library module"),
            DependencyItem("module", "zoneinfo", description="IANA timezone support"),
        ),
    },
    "dashboard": {
        "label": "Dashboard",
        "defaultEnabled": True,
        "items": (
            DependencyItem("module", "fastapi", description="Dashboard API framework"),
            DependencyItem("module", "uvicorn", description="Dashboard ASGI server"),
        ),
    },
    "rag-local": {
        "label": "nova-RAG Local Runtime",
        "defaultEnabled": False,
        "items": (
            DependencyItem("module", "sentence_transformers", description="Local embedding model runtime"),
            DependencyItem("module", "torch", description="Local embedding tensor runtime"),
            DependencyItem("module", "numpy", description="Vector matrix and cosine search runtime"),
            DependencyItem("module", "fastapi", description="nova-RAG embedding/search server API framework"),
            DependencyItem("module", "uvicorn", description="nova-RAG embedding/search server ASGI runtime"),
            DependencyItem("module", "pydantic", description="nova-RAG request model validation"),
        ),
    },
    "scheduler-macos": {
        "label": "Scheduler macOS",
        "defaultEnabled": platform.system() == "Darwin",
        "items": (
            DependencyItem("binary", "launchctl", description="macOS launchd control"),
        ),
    },
    "scheduler-linux": {
        "label": "Scheduler Linux",
        "defaultEnabled": platform.system() == "Linux",
        "items": (
            DependencyItem("binary", "systemctl", required=False, description="systemd service/timer control"),
            DependencyItem("binary", "crontab", required=False, description="cron fallback control"),
        ),
    },
    "dev-test": {
        "label": "Dev/Test",
        "defaultEnabled": False,
        "items": (
            DependencyItem("module", "unittest", description="Python standard test runner"),
            DependencyItem("binary", "node", required=False, description="Dashboard JavaScript syntax checks"),
        ),
    },
}


def dependency_profiles_status(selected: list[str] | None = None) -> dict[str, Any]:
    """Return read-only dependency status grouped by onboarding profile."""
    selected_ids = set(selected or PROFILE_DEFINITIONS)
    profiles = []
    total_missing_required = 0
    for profile_id, definition in PROFILE_DEFINITIONS.items():
        if profile_id not in selected_ids:
            continue
        checks = [_check_item(item) for item in definition["items"]]
        missing_required = [item for item in checks if item["required"] and not item["available"]]
        total_missing_required += len(missing_required)
        profiles.append(
            {
                "id": profile_id,
                "label": definition["label"],
                "defaultEnabled": bool(definition["defaultEnabled"]),
                "status": "ready" if not missing_required else "missing-required",
                "missingRequired": [item["name"] for item in missing_required],
                "checks": checks,
            }
        )
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "summary": {
            "profiles": len(profiles),
            "missingRequired": total_missing_required,
        },
        "profiles": profiles,
    }


def _check_item(item: DependencyItem) -> dict[str, Any]:
    if item.kind == "module":
        available = importlib.util.find_spec(item.name) is not None
        detected = item.name if available else None
    elif item.kind == "binary":
        detected = shutil.which(item.name)
        available = detected is not None
    elif item.kind == "python":
        detected = sys.executable
        available = sys.version_info >= (3, 10)
    else:
        detected = None
        available = False
    return {
        "kind": item.kind,
        "name": item.name,
        "required": item.required,
        "available": available,
        "detected": detected,
        "description": item.description,
    }

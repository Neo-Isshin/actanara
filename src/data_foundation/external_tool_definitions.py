"""Canonical supported external tool definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any


TOOL_CATALOG: dict[str, dict[str, Any]] = {
    "openclaw": {
        "name": "OpenClaw",
        "emoji": "🦞",
        "homeCandidates": ["~/.openclaw", "~/.openclaw-*"],
        "homeMarkers": ["config.json", "agents", "workspace"],
        "fields": {
            "home": "{home}",
            "agentsRoot": "{home}/agents",
            "configPath": "{home}/config.json",
            "credentialsPath": "{home}/credentials.json",
            "workspaceRoot": "{home}/workspace",
            "workspaceCoderRoot": "{home}/workspace-coder",
            "projectsRoot": "{home}/workspace/PROJECTS",
            "skillsRoot": "{home}/workspace/skills",
            "systemSkillsRoot": "{home}/skills",
            "memoryRoot": "{home}/memory",
            "cronJobsPath": "{home}/cron/jobs.json",
            "cronJobsMigratedPath": "{home}/cron/jobs.json.migrated",
            "cronRunsRoot": "{home}/cron/runs",
            "toolConfigSnapshotPath": "{home}/workspace/.dashboard-tool-configs.json",
        },
        "globalSkillRegistration": {
            "method": "copy-or-link skill folders into workspace skillsRoot or systemSkillsRoot",
            "targets": ["skillsRoot", "systemSkillsRoot"],
        },
    },
    "claudeCode": {
        "name": "Claude Code",
        "emoji": "✳️",
        "homeCandidates": ["~/.claude"],
        "homeMarkers": ["projects", "settings.json"],
        "fields": {
            "home": "{home}",
            "projectsRoot": "{home}/projects",
            "skillsRoot": "{home}/skills",
            "commandsRoot": "{home}/commands",
            "pluginsRoot": "{home}/plugins",
            "configPath": "{home}/settings.json",
            "binaryCandidates": ["/opt/homebrew/bin/claude", "/Applications/cmux.app/Contents/Resources/bin/claude"],
        },
        "globalSkillRegistration": {
            "method": "install skills under skillsRoot or commands under commandsRoot",
            "targets": ["skillsRoot", "commandsRoot"],
        },
    },
    "codex": {
        "name": "Codex",
        "emoji": "🤖",
        "homeCandidates": ["~/.codex"],
        "homeMarkers": ["sessions", "config.toml"],
        "fields": {
            "home": "{home}",
            "sessionsRoot": "{home}/sessions",
            "skillsRoot": "{home}/skills",
            "configPath": "{home}/config.toml",
        },
        "globalSkillRegistration": {"method": "install Codex skills under skillsRoot", "targets": ["skillsRoot"]},
    },
    "geminiCli": {
        "name": "Gemini CLI",
        "emoji": "✨",
        "homeCandidates": ["~/.gemini"],
        "homeMarkers": ["projects.json", "settings.json", "tmp"],
        "fields": {
            "home": "{home}",
            "chatsRoot": "{home}/tmp/ssd/chats",
            "projectsPath": "{home}/projects.json",
            "skillsRoot": "{home}/skills",
            "configPath": "{home}/settings.json",
        },
        "globalSkillRegistration": {"method": "install skills under skillsRoot", "targets": ["skillsRoot"]},
    },
    "hermes": {
        "name": "Hermes",
        "emoji": "⚕️",
        "homeCandidates": ["~/.hermes"],
        "homeMarkers": ["state.db", "profiles", "config.yaml"],
        "fields": {
            "home": "{home}",
            "stateDbPath": "{home}/state.db",
            "sessionsRoot": "{home}/sessions",
            "skillsRoot": "{home}/hermes-agent/skills",
            "optionalSkillsRoot": "{home}/hermes-agent/optional-skills",
            "pluginsRoot": "{home}/hermes-agent/plugins",
            "profilesRoot": "{home}/profiles",
            "configPath": "{home}/config.yaml",
            "binaryCandidates": ["{userHome}/.local/bin/hermes"],
        },
        "globalSkillRegistration": {"method": "install skills under skillsRoot", "targets": ["skillsRoot"]},
    },
}


def fields_for_tool_home(tool_id: str, home: Path, *, user_home: Path | None = None) -> dict[str, Any]:
    definition = TOOL_CATALOG[tool_id]
    context = {
        "home": str(home.expanduser().absolute()),
        "userHome": str((user_home or Path.home()).expanduser().absolute()),
    }
    return {key: _format_field_template(value, context) for key, value in definition["fields"].items()}


def default_external_tool_settings_from_catalog(home: Path | None = None) -> dict[str, dict[str, Any]]:
    user_home = (home or Path.home()).expanduser().absolute()
    defaults: dict[str, dict[str, Any]] = {}
    for tool_id, definition in TOOL_CATALOG.items():
        candidates = definition.get("homeCandidates") or []
        first = str(candidates[0]) if candidates else f"~/.{tool_id}"
        tool_home = user_home / first[2:] if first.startswith("~/") else Path(first).expanduser()
        defaults[tool_id] = fields_for_tool_home(tool_id, tool_home, user_home=user_home)
    return defaults


def _format_field_template(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**context)
    if isinstance(value, list):
        return [_format_field_template(item, context) for item in value]
    return value

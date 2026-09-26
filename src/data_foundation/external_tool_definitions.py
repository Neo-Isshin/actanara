"""Canonical supported external tool definitions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


TOOL_CATALOG: dict[str, dict[str, Any]] = {
    "openclaw": {
        "name": "OpenClaw",
        "emoji": "🦞",
        "homeCandidates": ["~/.openclaw", "~/.openclaw-*"],
        "homeMarkers": ["config.json", "agents", "workspace"],
        "detectionFields": ["agentsRoot", "configPath", "workspaceRoot"],
        "binaryNames": ["openclaw"],
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
        "detectionFields": ["projectsRoot", "configPath"],
        "binaryNames": ["claude"],
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
        "detectionFields": ["sessionsRoot", "configPath"],
        "binaryNames": ["codex"],
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
        "detectionFields": ["chatsRoot", "projectsPath", "configPath"],
        "binaryNames": ["gemini"],
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
        "detectionFields": ["stateDbPath", "profilesRoot", "configPath"],
        "binaryNames": ["hermes"],
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
    "opencode": {
        "name": "OpenCode",
        "emoji": "🐙",
        "color": "#0EA5A4",
        "homeEnvironment": "OPENCODE_HOME",
        "xdgDataRelative": "opencode",
        "homeCandidates": ["{xdgDataHome}/opencode", "~/.local/share/opencode", "~/.opencode"],
        "homeMarkers": ["opencode.db", "storage/session", "storage/project"],
        "detectionFields": ["databasePath", "storageRoot", "configPath"],
        "binaryNames": ["opencode"],
        "capabilities": ["session", "dialogue", "usage", "workspace", "model"],
        "fields": {
            "home": "{home}",
            "databasePath": "{home}/opencode.db",
            "storageRoot": "{home}/storage",
            "configHome": "{xdgConfigHome}/opencode",
            "configPath": "{xdgConfigHome}/opencode/opencode.jsonc",
            "binaryCandidates": [
                "{userHome}/.opencode/bin/opencode",
                "{userHome}/.local/bin/opencode",
            ],
        },
        "globalSkillRegistration": {
            "method": "not managed; runtime recognition is read-only",
            "targets": [],
        },
    },
    "antigravity": {
        "name": "Antigravity",
        "emoji": "🛡️",
        "color": "#6366F1",
        "homeCandidates": ["~/.gemini"],
        "homeMarkers": [
            "antigravity-cli/conversations",
            "antigravity-ide/conversations",
            "antigravity/conversations",
        ],
        "detectionFields": [
            "cliHome",
            "ideHome",
            "appHome",
            "cliConversationsRoot",
            "ideConversationsRoot",
            "appConversationsRoot",
            "cliHistoryPath",
            "cliBrainRoot",
            "ideBrainRoot",
            "appBrainRoot",
        ],
        "binaryNames": ["agy", "antigravity"],
        "capabilities": ["session", "dialogue-partial", "usage-partial", "workspace", "source-documents"],
        "fields": {
            "home": "{home}",
            "cliHome": "{home}/antigravity-cli",
            "ideHome": "{home}/antigravity-ide",
            "appHome": "{home}/antigravity",
            "cliConversationsRoot": "{home}/antigravity-cli/conversations",
            "ideConversationsRoot": "{home}/antigravity-ide/conversations",
            "appConversationsRoot": "{home}/antigravity/conversations",
            "cliHistoryPath": "{home}/antigravity-cli/history.jsonl",
            "cliBrainRoot": "{home}/antigravity-cli/brain",
            "ideBrainRoot": "{home}/antigravity-ide/brain",
            "appBrainRoot": "{home}/antigravity/brain",
            "binaryCandidates": [
                "{userHome}/.local/bin/agy",
                "{userHome}/.local/bin/antigravity",
            ],
        },
        "globalSkillRegistration": {
            "method": "not managed; runtime recognition is read-only",
            "targets": [],
        },
    },
    "cursor": {
        "name": "Cursor",
        "emoji": "🖱️",
        "color": "#7C3AED",
        "homeEnvironment": "CURSOR_AGENT_HOME",
        "homeCandidates": ["~/.cursor"],
        "homeMarkers": ["chats", "projects", "acp-sessions", "cli-config.json"],
        "detectionCandidates": [
            "{userHome}/Library/Application Support/Cursor/User/globalStorage/state.vscdb",
            "{xdgConfigHome}/Cursor/User/globalStorage/state.vscdb",
        ],
        "detectionFields": [
            "chatsRoot",
            "projectsRoot",
            "acpSessionsRoot",
            "configPath",
            "ideStateDbCandidates",
            "workspaceStorageRoots",
        ],
        "binaryNames": ["cursor-agent", "cursor"],
        "capabilities": ["session", "dialogue-partial", "workspace", "model", "usage-unavailable"],
        "fields": {
            "home": "{home}",
            "chatsRoot": "{home}/chats",
            "skillsRoot": "{home}/skills",
            "projectsRoot": "{home}/projects",
            "acpSessionsRoot": "{home}/acp-sessions",
            "configPath": "{home}/cli-config.json",
            "ideStateDbCandidates": [
                "{userHome}/Library/Application Support/Cursor/User/globalStorage/state.vscdb",
                "{xdgConfigHome}/Cursor/User/globalStorage/state.vscdb",
            ],
            "workspaceStorageRoots": [
                "{userHome}/Library/Application Support/Cursor/User/workspaceStorage",
                "{xdgConfigHome}/Cursor/User/workspaceStorage",
            ],
            "binaryCandidates": [
                "{userHome}/.local/bin/cursor-agent",
                "{userHome}/.local/bin/cursor",
                "{userHome}/.local/bin/agent",
            ],
        },
        "globalSkillRegistration": {
            "method": "install managed skills under skillsRoot; preserve customized files",
            "targets": ["skillsRoot"],
        },
    },
}

# New integrations share the same catalog, path settings, discovery and UI.
# Coverage describes the parser boundary, not the presence of an installation.
TOOL_CATALOG.update({
    "zcode": {
        "name": "ZCode", "emoji": "ⓩ", "color": "#587BBD",
        "homeCandidates": ["~/.zcode"], "homeMarkers": ["cli/db/db.sqlite"],
        "detectionFields": ["databasePath"], "binaryNames": ["zcode"],
        "capabilities": ["session", "dialogue", "usage-partial", "workspace", "source-documents", "skill-registration"],
        "coverage": "local-records", "validation": "local-sample",
        "fields": {"home": "{home}", "databasePath": "{home}/cli/db/db.sqlite", "skillsRoot": "{home}/skills"},
        "globalSkillRegistration": {"method": "install managed SKILL.md under skillsRoot", "targets": ["skillsRoot"]},
    },
    "qwenCode": {
        "name": "Qwen Code", "emoji": "🌿", "color": "#6B9F72",
        "homeCandidates": ["~/.qwen"], "homeMarkers": ["projects", "exports"],
        "detectionFields": ["projectsRoot", "exportsRoot"], "binaryNames": ["qwen"],
        "capabilities": ["session", "dialogue-partial", "workspace", "skill-registration", "usage-partial"],
        "coverage": "local-records", "validation": "schema-fixtures",
        "fields": {"home": "{home}", "projectsRoot": "{home}/projects", "exportsRoot": "{home}/exports", "skillsRoot": "{home}/skills"},
        "globalSkillRegistration": {"method": "install managed SKILL.md under skillsRoot", "targets": ["skillsRoot"]},
    },
    "copilotCli": {
        "name": "Copilot CLI", "emoji": "🤝", "color": "#9973BB",
        "homeEnvironment": "COPILOT_HOME", "homeCandidates": ["~/.copilot"],
        "homeMarkers": ["session-state"], "detectionFields": ["sessionsRoot"], "binaryNames": ["copilot"],
        "capabilities": ["session", "dialogue-partial", "workspace", "source-documents", "skill-registration", "usage-unavailable"],
        "coverage": "cli-only", "validation": "schema-fixtures",
        "fields": {"home": "{home}", "sessionsRoot": "{home}/session-state", "skillsRoot": "{home}/skills"},
        "globalSkillRegistration": {"method": "install managed SKILL.md under skillsRoot", "targets": ["skillsRoot"]},
    },
    "cline": {
        "name": "Cline", "emoji": "🧰", "color": "#BA8270",
        "homeCandidates": ["~/.cline"], "homeMarkers": ["data/tasks", "tasks"],
        "detectionFields": ["tasksRoot", "taskRootCandidates"], "binaryNames": ["cline"],
        "capabilities": ["session", "dialogue-partial", "usage-partial", "workspace"],
        "coverage": "extension-history", "validation": "schema-fixtures",
        "fields": {"home": "{home}", "tasksRoot": "{home}/data/tasks", "taskRootCandidates": [
            "{home}/tasks",
            "{userHome}/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/tasks",
            "{userHome}/Library/Application Support/Cursor/User/globalStorage/saoudrizwan.claude-dev/tasks",
            "{xdgConfigHome}/Code/User/globalStorage/saoudrizwan.claude-dev/tasks",
            "{xdgConfigHome}/Cursor/User/globalStorage/saoudrizwan.claude-dev/tasks"]},
        "globalSkillRegistration": {"method": "read-only; no automatic registration", "targets": []},
    },
    "continue": {
        "name": "Continue", "emoji": "▶", "color": "#6B98AC",
        "homeCandidates": ["~/.continue"], "homeMarkers": ["sessions"],
        "detectionFields": ["sessionsRoot"], "binaryNames": ["cn"],
        "capabilities": ["session", "dialogue-partial", "workspace", "usage-unavailable"],
        "coverage": "ide-history", "validation": "schema-fixtures",
        "fields": {"home": "{home}", "sessionsRoot": "{home}/sessions"},
        "globalSkillRegistration": {"method": "read-only; no automatic registration", "targets": []},
    },
    "aider": {
        "name": "Aider", "emoji": "🤲", "color": "#B6A062",
        "homeCandidates": ["~/.aider"], "homeMarkers": ["imports"],
        "detectionFields": ["importsRoot", "historyFiles"], "binaryNames": ["aider"],
        "capabilities": ["session", "source-documents", "workspace", "usage-unavailable"],
        "coverage": "explicit-history-files", "validation": "schema-fixtures",
        "fields": {"home": "{home}", "importsRoot": "{home}/imports", "historyFiles": []},
        "globalSkillRegistration": {"method": "read-only; no automatic registration", "targets": []},
    },
    "grokBot": {
        "name": "Grok Bot", "emoji": "✦", "color": "#707B8C",
        "homeCandidates": ["~/.grokbot"], "homeMarkers": [".grokbot-data-root-v1"],
        "detectionFields": ["cacheRoots"], "binaryNames": [],
        "capabilities": ["session", "dialogue-partial", "usage-unavailable"],
        "coverage": "cache-only", "validation": "local-sample", "experimental": True,
        "fields": {"home": "{home}", "cacheRoots": [
            "{userHome}/Library/Application Support/Grok Bot/sand-client-persistence",
            "{xdgConfigHome}/Grok Bot/sand-client-persistence"]},
        "globalSkillRegistration": {"method": "read-only cache; no cloud access or registration", "targets": []},
    },
})

CATALOG_TO_FOUNDATION = {key: {"claudeCode": "claude-code", "geminiCli": "gemini-cli", "qwenCode": "qwen-code", "copilotCli": "copilot-cli", "grokBot": "grok-bot"}.get(key, key) for key in TOOL_CATALOG}
FOUNDATION_TO_CATALOG = {value: key for key, value in CATALOG_TO_FOUNDATION.items()}


def fields_for_tool_home(
    tool_id: str,
    home: Path,
    *,
    user_home: Path | None = None,
    xdg_data_home: Path | None = None,
    xdg_config_home: Path | None = None,
) -> dict[str, Any]:
    definition = TOOL_CATALOG[tool_id]
    selected_user_home = (user_home or Path.home()).expanduser().absolute()
    context = {
        "home": str(home.expanduser().absolute()),
        "userHome": str(selected_user_home),
        "xdgDataHome": str((xdg_data_home or selected_user_home / ".local" / "share").expanduser().absolute()),
        "xdgConfigHome": str((xdg_config_home or selected_user_home / ".config").expanduser().absolute()),
    }
    return {key: _format_field_template(value, context) for key, value in definition["fields"].items()}


def default_external_tool_settings_from_catalog(home: Path | None = None) -> dict[str, dict[str, Any]]:
    explicit_home = home is not None
    user_home = (home or Path.home()).expanduser().absolute()
    xdg_data_home = _environment_path("XDG_DATA_HOME") if not explicit_home else None
    xdg_config_home = _environment_path("XDG_CONFIG_HOME") if not explicit_home else None
    xdg_data_home = xdg_data_home or user_home / ".local" / "share"
    xdg_config_home = xdg_config_home or user_home / ".config"
    defaults: dict[str, dict[str, Any]] = {}
    for tool_id, definition in TOOL_CATALOG.items():
        candidates = definition.get("homeCandidates") or []
        first = str(candidates[0]) if candidates else f"~/.{tool_id}"
        environment_home = (
            _environment_path(str(definition.get("homeEnvironment") or ""))
            if not explicit_home
            else None
        )
        if environment_home is not None:
            tool_home = environment_home
        elif definition.get("xdgDataRelative"):
            tool_home = xdg_data_home / str(definition["xdgDataRelative"])
        else:
            context = {
                "userHome": str(user_home),
                "xdgDataHome": str(xdg_data_home),
                "xdgConfigHome": str(xdg_config_home),
            }
            first = first.format(**context)
            tool_home = user_home / first[2:] if first.startswith("~/") else Path(first).expanduser()
        defaults[tool_id] = fields_for_tool_home(
            tool_id,
            tool_home,
            user_home=user_home,
            xdg_data_home=xdg_data_home,
            xdg_config_home=xdg_config_home,
        )
    return defaults


def _format_field_template(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**context)
    if isinstance(value, list):
        return [_format_field_template(item, context) for item in value]
    return value


def _environment_path(name: str) -> Path | None:
    if not name:
        return None
    value = str(os.getenv(name) or "").strip()
    return Path(value).expanduser().absolute() if value else None

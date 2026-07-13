"""Read-only settings hardcode and secret hygiene audit."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .settings import (
    OPERATOR_SETTINGS_WRITE_PROTECTED_TOP_LEVEL,
    SETTINGS_AUTHORITY_GROUPS,
    resolve_llm_provider,
    settings_authority_inventory,
)


SECRET_ENV_KEYS = {
    "LLM_API_KEY",
    "OPENCLAW_GATEWAY_TOKEN",
    "NOVA_RAG_CLOUD_API_KEY",
    "RAG_CLOUD_API_KEY",
}

BOOTSTRAP_ENV_KEYS = {
    "NOVA_HOME",
    "NOVA_LOCATION_FILE",
}

DIAGNOSTIC_ENV_KEYS = {
    "DASHBOARD_READ_SOURCE",
    "DIARY_MEMORY_SOURCE",
    "DIARY_METRICS_SOURCE",
    "DIARY_TASKS_SOURCE",
    "REPORT_READ_SOURCE",
    "TASK_AUDIT_SINK",
}

INSTALLER_TRANSPORT_ENV_PREFIXES = ("NOVA_INSTALL_",)

AUDIT_TARGETS = (
    {
        "id": "workspace-dotenv-llm-key",
        "category": "secret",
        "severity": "high",
        "path": ".env",
        "patterns": ("LLM_API_KEY",),
        "settingTarget": "llmProvider.apiKey",
        "recommendation": "Rotate out-of-band and move long-lived secrets into the approved runtime secret policy; do not print or silently migrate values.",
    },
    {
        "id": "legacy-diary-summary-direct-llm-config",
        "category": "legacy-helper",
        "severity": "medium",
        "path": "src/diary_generator/diary_summary.py",
        "patterns": ("config.LLM_API_KEY", "config.LLM_HOST", "config.LLM_MODEL_NAME"),
        "settingTarget": "llmProvider",
        "recommendation": "Keep migration-only unless separately approved; supported production LLM paths must use resolve_llm_provider().",
    },
    {
        "id": "legacy-diary-summary-openclaw-credentials",
        "category": "external-secret",
        "severity": "medium",
        "path": "src/diary_generator/diary_summary.py",
        "patterns": ("OPENCLAW_GATEWAY_TOKEN", "credentials.json"),
        "settingTarget": "externalTools.openclaw.credentialsPath",
        "recommendation": "Keep credential path visible in settings; do not load or expose credential contents in audit/status surfaces.",
    },
    {
        "id": "rag-cloud-env-keys",
        "category": "secret",
        "severity": "medium",
        "path": "src/agentic_rag/rag_config.py",
        "patterns": ("NOVA_RAG_CLOUD_API_KEY", "RAG_CLOUD_API_KEY"),
        "settingTarget": "rag.embedding.cloudApiKey",
        "recommendation": "Keep audit-only until a dedicated RAG secret policy is approved.",
    },
    {
        "id": "dashboard-shell-launch-defaults",
        "category": "settings-routed-launch-wrapper",
        "severity": "info",
        "path": "advanced/dashboard/run_dashboard_server.sh",
        "patterns": ("resolve_dashboard_settings",),
        "settingTarget": "dashboard",
        "recommendation": "Keep shell wrapper launch values routed through dashboard settings.",
    },
    {
        "id": "token-clock-external-tool-paths",
        "category": "settings-routed-path",
        "severity": "info",
        "path": "src/dashboard/app/services/token_clock.py",
        "patterns": ("external_tool_path(",),
        "settingTarget": "externalTools",
        "recommendation": "Keep live collector discovery routed through externalTools settings helpers.",
    },
    {
        "id": "dashboard-agents-openclaw-path",
        "category": "settings-routed-path",
        "severity": "info",
        "path": "src/dashboard/app/services/agents.py",
        "patterns": ("external_tool_path(",),
        "settingTarget": "externalTools.openclaw.agentsRoot",
        "recommendation": "Keep diagnostic agent inventory routed through externalTools.openclaw.agentsRoot.",
    },
)


def settings_hardcode_audit(root: Path | None = None, *, paths: Any | None = None) -> dict[str, Any]:
    """Return a deterministic audit of known settings/secret multi-head risks."""
    repo = root or Path(__file__).resolve().parents[2]
    findings = [_evaluate_target(repo, target) for target in AUDIT_TARGETS]
    dotenv = _dotenv_secret_presence(repo / ".env")
    if dotenv:
        for finding in findings:
            if finding["id"] == "workspace-dotenv-llm-key":
                finding["secretKeysPresent"] = sorted(dotenv)
                finding["matched"] = "LLM_API_KEY" in dotenv
                finding["status"] = "attention" if finding["matched"] else "not-found"
                break
    residual = _residual_risk_summary(root=repo, paths=paths)
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "policy": {
            "secrets": "report key names and locations only; never emit values",
            "migration": "no silent migration of production settings or secrets",
            "scope": "known high-signal settings and hardcode risks; not a full static analyzer",
        },
        "summary": _summary(findings),
        "residualRisks": residual,
        "findings": findings,
    }


def _residual_risk_summary(root: Path, paths: Any | None = None) -> dict[str, Any]:
    buckets = [
        _env_override_risk(paths),
        _manual_default_drift_risk(paths),
        _protected_group_risk(),
        _rag_legacy_env_risk(root),
        _shell_wrapper_risk(root),
    ]
    active = [bucket for bucket in buckets if bucket["status"] != "ok"]
    by_severity: dict[str, int] = {}
    for bucket in active:
        severity = str(bucket.get("severity") or "info")
        by_severity[severity] = by_severity.get(severity, 0) + 1
    return {
        "schemaVersion": 1,
        "status": "attention" if active else "ok",
        "attention": len(active),
        "total": len(buckets),
        "bySeverity": by_severity,
        "buckets": buckets,
    }


def _env_override_risk(paths: Any | None = None) -> dict[str, Any]:
    active: list[dict[str, str]] = []
    if paths is not None:
        inventory = settings_authority_inventory(paths, persist_defaults=False)
        for group in inventory.get("groups", []):
            for field in group.get("fields", []):
                if field.get("envOverride"):
                    active.append(
                        {
                            "group": str(group.get("group") or ""),
                            "path": str(field.get("path") or ""),
                            "env": str(field.get("env") or ""),
                            "semantics": _env_semantics(str(field.get("env") or "")),
                        }
                    )
    else:
        for group in SETTINGS_AUTHORITY_GROUPS:
            for field in group.get("fields", []):
                env_name = field.get("env")
                if env_name and os.getenv(str(env_name)) is not None:
                    active.append(
                        {
                            "group": str(group.get("group") or ""),
                            "path": str(field.get("path") or ""),
                            "env": str(env_name),
                            "semantics": _env_semantics(str(env_name)),
                        }
                    )
    by_semantics: dict[str, int] = {}
    for item in active:
        semantics = str(item.get("semantics") or "runtime-override")
        by_semantics[semantics] = by_semantics.get(semantics, 0) + 1
    runtime_attention = [
        item
        for item in active
        if item.get("semantics") not in {"bootstrap-pointer", "diagnostic-guard", "installer-transport"}
    ]
    return {
        "id": "active-env-overrides",
        "category": "multi-head-settings",
        "severity": "medium" if runtime_attention else "info",
        "status": "attention" if runtime_attention else "ok",
        "count": len(active),
        "runtimeAttention": len(runtime_attention),
        "bySemantics": by_semantics,
        "items": active,
        "recommendation": "Bootstrap, installer transport, and diagnostic guard env names are expected process-local inputs; investigate other env values before debugging persisted settings.",
    }


def _env_semantics(env_name: str) -> str:
    if env_name in BOOTSTRAP_ENV_KEYS:
        return "bootstrap-pointer"
    if env_name in DIAGNOSTIC_ENV_KEYS:
        return "diagnostic-guard"
    if any(env_name.startswith(prefix) for prefix in INSTALLER_TRANSPORT_ENV_PREFIXES):
        return "installer-transport"
    if env_name in SECRET_ENV_KEYS or env_name.endswith("_API_KEY") or env_name.endswith("_TOKEN"):
        return "secret-injection"
    return "runtime-override"


def _manual_default_drift_risk(paths: Any | None = None) -> dict[str, Any]:
    if paths is None:
        return {
            "id": "manual-default-drift",
            "category": "manual-vs-derived",
            "severity": "low",
            "status": "ok",
            "items": [],
            "recommendation": "Manual values are valid when explicit; compare against derived defaults during provider changes.",
        }
    provider = resolve_llm_provider(paths, redact_secrets=True)
    drift = bool(provider.get("pipelineGateDrift"))
    return {
        "id": "manual-default-drift",
        "category": "manual-vs-derived",
        "severity": "low",
        "status": "attention" if drift else "ok",
        "items": [
            {
                "path": "llmProvider.pipelineGateTokens",
                "mode": str(provider.get("pipelineGateMode") or ""),
                "effectiveValue": str(provider.get("pipelineGateTokens") or ""),
                "autoValue": str(provider.get("autoPipelineGateTokens") or ""),
            }
        ]
        if drift
        else [],
        "recommendation": "Manual values are valid when explicit; compare against derived defaults during provider changes.",
    }


def _protected_group_risk() -> dict[str, Any]:
    protected = sorted(OPERATOR_SETTINGS_WRITE_PROTECTED_TOP_LEVEL)
    groups = sorted({str(group.get("group")) for group in SETTINGS_AUTHORITY_GROUPS if group.get("group")})
    return {
        "id": "protected-setting-groups",
        "category": "write-policy",
        "severity": "info",
        "status": "ok",
        "protectedGroups": protected,
        "knownGroups": groups,
        "recommendation": "Protected groups must keep dedicated APIs/workflows; do not route them through generic settings writes.",
    }


def _rag_legacy_env_risk(root: Path) -> dict[str, Any]:
    legacy_mode_env = os.getenv("RAG_MODE")
    nova_mode_env = os.getenv("NOVA_RAG_MODE")
    config_text = _read_text(root / "src" / "agentic_rag" / "rag_config.py")
    mentions_legacy_mode = "RAG_MODE" in config_text
    active = bool(legacy_mode_env or nova_mode_env or mentions_legacy_mode)
    return {
        "id": "rag-legacy-env-boundary",
        "category": "rag-settings",
        "severity": "medium" if legacy_mode_env or nova_mode_env else "low",
        "status": "attention" if active else "ok",
        "activeEnv": [name for name, value in (("RAG_MODE", legacy_mode_env), ("NOVA_RAG_MODE", nova_mode_env)) if value is not None],
        "codeMentionsLegacyMode": mentions_legacy_mode,
        "recommendation": "Keep nova-RAG mode changes in the dedicated RAG control plane; legacy RAG_MODE must not become the product-level subsystem switch.",
    }


def _shell_wrapper_risk(root: Path) -> dict[str, Any]:
    path = root / "advanced" / "dashboard" / "run_dashboard_server.sh"
    content = _read_text(path)
    markers = [
        marker
        for marker in ("NOVA_DASHBOARD_PROJECT_ROOT", "NOVA_DASHBOARD_PYTHON", "NOVA_DASHBOARD_HOST", "NOVA_DASHBOARD_PORT")
        if marker in content
    ]
    return {
        "id": "shell-wrapper-env-defaults",
        "category": "launch-wrapper",
        "severity": "medium" if markers else "info",
        "status": "attention" if markers else "ok",
        "path": "advanced/dashboard/run_dashboard_server.sh",
        "markers": markers,
        "recommendation": "Shell wrappers should resolve dashboard settings instead of carrying independent dashboard env defaults.",
    }


def _evaluate_target(repo: Path, target: dict[str, Any]) -> dict[str, Any]:
    path = repo / str(target["path"])
    content = _read_text(path)
    matched_patterns = [pattern for pattern in target["patterns"] if pattern in content]
    matched = bool(matched_patterns)
    status = "attention" if matched else "not-found"
    if matched and (target.get("severity") == "info" or target.get("category") == "settings-routed-path"):
        status = "ok"
    return {
        "id": target["id"],
        "category": target["category"],
        "severity": target["severity"],
        "path": target["path"],
        "settingTarget": target["settingTarget"],
        "status": status,
        "matched": matched,
        "matchedPatterns": matched_patterns,
        "recommendation": target["recommendation"],
    }


def _dotenv_secret_presence(path: Path) -> set[str]:
    content = _read_text(path)
    present: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in SECRET_ENV_KEYS and value.strip().strip("'\""):
            present.add(key)
    return present


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    attention = [item for item in findings if item.get("status") == "attention"]
    by_severity: dict[str, int] = {}
    for item in attention:
        severity = str(item.get("severity") or "unknown")
        by_severity[severity] = by_severity.get(severity, 0) + 1
    return {
        "status": "attention" if attention else "ok",
        "attention": len(attention),
        "total": len(findings),
        "bySeverity": by_severity,
    }

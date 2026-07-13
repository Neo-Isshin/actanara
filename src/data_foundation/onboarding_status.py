"""Read-only onboarding payloads for new-user setup checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .dependency_profiles import dependency_profiles_status
from .onboarding_plan import (
    default_onboarding_profiles,
    dependency_groups_for_product_profiles,
    dependency_profiles_for_product_profiles,
    normalize_onboarding_profiles,
    packaging_plan_for_product_profiles,
    requirement_sets_for_product_profiles,
    required_onboarding_inputs,
)
from .paths import RuntimePaths, load_paths
from .scheduler_preview import preview_system_timer
from .settings_status import nova_settings_status


def nova_onboarding_status(paths: RuntimePaths | None = None, selected_profiles: list[str] | None = None) -> dict[str, Any]:
    """Return a read-only, product-facing onboarding readiness payload."""
    runtime_paths = paths or load_paths()
    status = nova_settings_status(runtime_paths)
    scheduler_preview = preview_system_timer(runtime_paths)
    selected_profile_ids = normalize_onboarding_profiles(selected_profiles or default_onboarding_profiles())
    dependencies = dependency_profiles_status(dependency_profiles_for_product_profiles(selected_profile_ids))
    required_inputs = required_onboarding_inputs(selected_profile_ids, runtime_paths)
    dependency_groups = dependency_groups_for_product_profiles(selected_profile_ids)
    requirement_sets = requirement_sets_for_product_profiles(selected_profile_ids, dependencies, required_inputs)
    packaging_plan = packaging_plan_for_product_profiles(selected_profile_ids, requirement_sets)
    readiness_checks = _readiness_checks(status, dependencies, scheduler_preview, selected_profile_ids, required_inputs)
    return {
        "schemaVersion": 2,
        "readOnly": True,
        "profileModel": "product-v2",
        "runtime": status.get("runtime"),
        "general": status.get("general"),
        "settings": {
            "settingsPath": ((status.get("runtime") or {}).get("settingsPath")),
            "summary": status.get("summary"),
            "checks": status.get("checks"),
        },
        "dependencyProfiles": dependencies,
        "selectedDependencyProfiles": selected_profile_ids,
        "requiredInputs": required_inputs,
        "dependencyGroups": dependency_groups,
        "requirementSets": requirement_sets,
        "packagingPlan": packaging_plan,
        "resourceProfile": status.get("resourceProfile"),
        "rag": _rag_payload(status),
        "scheduler": {
            "provider": scheduler_preview.get("provider"),
            "supported": scheduler_preview.get("supported"),
            "registered": scheduler_preview.get("registered"),
            "registrationImplemented": scheduler_preview.get("registrationImplemented", scheduler_preview.get("provider") == "launchd"),
            "jobs": scheduler_preview.get("jobs", []),
            "installPlan": scheduler_preview.get("installPlan", []),
            "rollbackPlan": scheduler_preview.get("rollbackPlan", []),
            "note": scheduler_preview.get("note"),
        },
        "readiness": {
            "status": _readiness_status(readiness_checks),
            "checks": readiness_checks,
        },
    }


def format_nova_onboarding_status(payload: dict[str, Any]) -> str:
    runtime = payload.get("runtime") or {}
    general = payload.get("general") or {}
    readiness = payload.get("readiness") or {}
    dependencies = payload.get("dependencyProfiles") or {}
    dependency_groups = payload.get("dependencyGroups") or []
    requirement_sets = payload.get("requirementSets") or []
    packaging_plan = payload.get("packagingPlan") or {}
    scheduler = payload.get("scheduler") or {}
    resource_profile = payload.get("resourceProfile") or {}
    rag = payload.get("rag") or {}
    lines = [
        f"Nova onboarding status: {readiness.get('status', 'unknown')}",
        f"Runtime: {runtime.get('novaHome', '-')}",
        f"Settings: {runtime.get('settingsPath', '-')}",
        f"General: {general.get('appName', '-')} / {general.get('environment', '-')} / {general.get('timezone', '-')}",
        (
            "Dependencies: "
            f"profiles={((dependencies.get('summary') or {}).get('profiles', '-'))} "
            f"missingRequired={((dependencies.get('summary') or {}).get('missingRequired', '-'))}"
        ),
        f"Dependency groups: {len([item for item in dependency_groups if item.get('selected')])}",
        f"Requirement sets: {len([item for item in requirement_sets if item.get('selected')])}",
        (
            "Packaging: "
            f"groups={((packaging_plan.get('summary') or {}).get('groups', 0))} "
            f"pendingProviderDerived={((packaging_plan.get('summary') or {}).get('pendingProviderDerivedGroups', 0))} "
            f"packageManager={packaging_plan.get('packageManager', 'undecided')}"
        ),
        (
            "Resources: "
            f"dashboard={((resource_profile.get('dashboard') or {}).get('expectedResidentProcesses', '-'))} "
            f"rag={((resource_profile.get('rag') or {}).get('expectedResidentProcesses', '-'))}"
        ),
        (
            "RAG: "
            f"enabled={rag.get('enabled', '-')} "
            f"mode={rag.get('mode', '-')} "
            f"server={rag.get('serverEnabled', '-')}"
        ),
        (
            "Scheduler: "
            f"{scheduler.get('provider', '-')} "
            f"supported={scheduler.get('supported', '-')} "
            f"registered={scheduler.get('registered', '-')}"
        ),
        "Readiness checks:",
    ]
    for check in readiness.get("checks") or []:
        lines.append(f"- {check.get('status', 'unknown')}: {check.get('id', '-')}: {check.get('message', '')}")
    return "\n".join(lines) + "\n"


def dump_nova_onboarding_status_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _rag_payload(status: dict[str, Any]) -> dict[str, Any]:
    rag_profile = ((status.get("resourceProfile") or {}).get("rag") or {})
    return {
        "enabled": rag_profile.get("enabled"),
        "mode": rag_profile.get("mode"),
        "serverEnabled": rag_profile.get("serverEnabled"),
        "expectedResidentProcesses": rag_profile.get("expectedResidentProcesses"),
        "status": rag_profile.get("status"),
        "host": rag_profile.get("host"),
        "port": rag_profile.get("port"),
    }


def _readiness_checks(
    status: dict[str, Any],
    dependencies: dict[str, Any],
    scheduler_preview: dict[str, Any],
    selected_profiles: list[str],
    required_inputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    runtime = status.get("runtime") or {}
    dependency_summary = ((dependencies or {}).get("summary") or {})
    resource_profile = status.get("resourceProfile") or {}
    rag = resource_profile.get("rag") or {}
    settings_path = runtime.get("settingsPath")
    dashboard_selected = "dashboard" in selected_profiles
    rag_selected = "nova-rag" in selected_profiles
    pending_required_inputs = [item for item in required_inputs if item.get("required") and item.get("status") == "pending"]
    checks = [
        _check("runtime-home", bool(((runtime.get("validation") or {}).get("valid"))), "error", f"runtime home {runtime.get('novaHome')} is initialized"),
        _check("settings-file", bool(settings_path and Path(str(settings_path)).exists()), "error", f"settings file {settings_path} is present"),
        _check(
            "dependencies",
            int(dependency_summary.get("missingRequired") or 0) == 0,
            "warn",
            f"{dependency_summary.get('missingRequired', 0)} required dependency item(s) missing across reported profiles",
        ),
        _check(
            "dashboard-resource-profile",
            (not dashboard_selected) or int((resource_profile.get("dashboard") or {}).get("expectedResidentProcesses") or 0) >= 1,
            "warn",
            "Dashboard is expected to run as one resident process" if dashboard_selected else "Dashboard profile is not selected",
        ),
        _check(
            "rag-product-state",
            (not rag_selected) or rag.get("status") != "unknown",
            "warn",
            f"nova-RAG mode={rag.get('mode')} enabled={rag.get('enabled')}" if rag_selected else "nova-RAG profile is not selected",
        ),
        _check(
            "scheduler-preview",
            bool(scheduler_preview.get("supported")) or scheduler_preview.get("registrationImplemented") is False,
            "warn",
            f"scheduler provider={scheduler_preview.get('provider')} supported={scheduler_preview.get('supported')}",
        ),
        _check(
            "selected-profiles",
            bool(selected_profiles),
            "error",
            f"default selected dependency profiles: {', '.join(selected_profiles) or '-'}",
        ),
        _check(
            "required-inputs",
            not pending_required_inputs,
            "warn",
            f"{len(pending_required_inputs)} required installer input(s) pending",
        ),
    ]
    return checks


def _readiness_status(checks: list[dict[str, Any]]) -> str:
    if any(check.get("status") == "error" for check in checks):
        return "error"
    if any(check.get("status") == "warn" for check in checks):
        return "warn"
    return "ok"


def _check(check_id: str, ok: bool, severity: str, message: str) -> dict[str, Any]:
    return {"id": check_id, "status": "ok" if ok else severity, "severity": severity, "message": message}

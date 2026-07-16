"""Read-only onboarding payloads for new-user setup checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cli_output import render_cli, status_item, status_label
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


_MODEL_KEY_STEP = "open-nova model key --value-stdin"


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
    scheduler = payload.get("scheduler") or {}
    rag = payload.get("rag") or {}
    selected = [_profile_label(value) for value in payload.get("selectedDependencyProfiles") or []]
    pending = [
        item
        for item in payload.get("requiredInputs") or []
        if item.get("required") and item.get("status") == "pending"
    ]
    return render_cli(
        "Setup status",
        fields=(
            ("Status", status_label(readiness.get("status"))),
            ("Data folder", runtime.get("novaHome", "—")),
            ("Features", ", ".join(selected) or "Open Nova"),
            ("Timezone", general.get("timezone", "—")),
            ("Memory search", "Included" if rag.get("enabled") else "Off"),
            ("Automatic runs", "Enabled" if scheduler.get("registered") else "Not enabled"),
        ),
        sections=(("Checks", [_friendly_onboarding_check(check) for check in readiness.get("checks") or []]),),
        next_steps=[_pending_input_step(item.get("id")) for item in pending],
    )


def _profile_label(value: object) -> str:
    return {
        "open-nova": "Daily diary",
        "dashboard": "Dashboard",
        "nova-rag": "Memory search",
        "nova-task": "Tasks",
        "dev-test": "Developer tools",
    }.get(str(value or ""), str(value or "Open Nova"))


def _friendly_onboarding_check(check: dict[str, Any]) -> str:
    check_id = str(check.get("id") or "")
    status = check.get("status", "unknown")
    labels = {
        "runtime-home": ("Data folder is ready", "Choose a data folder"),
        "settings-file": ("Settings are ready", "Settings need setup"),
        "dependencies": ("Required software is ready", "Some required software is missing"),
        "dashboard-resource-profile": ("Dashboard is ready", "Dashboard needs attention"),
        "rag-product-state": ("Memory search choice is ready", "Memory search needs a choice"),
        "scheduler-preview": ("Automatic daily runs are available", "Automatic daily runs are unavailable"),
        "selected-profiles": ("Selected features are ready", "Choose at least one feature"),
        "required-inputs": ("Required choices are complete", "Some required choices are missing"),
    }
    ready, attention = labels.get(check_id, ("Setup check passed", "Setup check needs attention"))
    return status_item(status, ready, attention)


def _pending_input_step(value: object) -> str:
    return {
        "output-path": "Choose where Open Nova stores its data",
        "llm-provider": "open-nova model set --help",
        "llm-api-key": _MODEL_KEY_STEP,
        "rag-provider": "Choose local or cloud memory search in Dashboard settings",
        "rag-embedding-model": "Choose a model for memory search in Dashboard settings",
    }.get(str(value or ""), "Finish the remaining setup choice")


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

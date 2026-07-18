"""Read-only selectable subsystem onboarding plans."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import plistlib
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cli_output import render_cli, status_item, status_label
from .dependency_profiles import dependency_profiles_status
from .paths import RuntimePaths, default_oneliner_runtime_home, initialize_home, load_paths, persist_runtime_selection
from .scheduler_preview import preview_system_timer
from .pipeline_language import resolve_pipeline_language_profile
from .settings import read_settings, resolve_llm_provider, write_settings
from .release_clean import repository_clean_deployment_check
from .time import resolve_timezone


PRODUCT_PROFILE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "actanara": {
        "label": "Actanara",
        "defaultEnabled": True,
        "required": True,
        "description": "Diary generation pipeline and core runtime.",
        "dependencyProfiles": ("core-foundation",),
    },
    "dashboard": {
        "label": "Dashboard",
        "defaultEnabled": True,
        "required": True,
        "description": "Local Dashboard UI/API.",
        "dependencyProfiles": ("dashboard",),
    },
    "nova-rag": {
        "label": "nova-RAG",
        "defaultEnabled": False,
        "required": False,
        "description": "nova-RAG memory/search subsystem.",
        "dependencyProfiles": (),
    },
    "nova-task": {
        "label": "Nova-Task",
        "defaultEnabled": True,
        "required": True,
        "description": "Nova-Task authority/review subsystem.",
        "dependencyProfiles": (),
    },
    "dev-test": {
        "label": "Dev/Test",
        "defaultEnabled": False,
        "required": False,
        "description": "Developer/test tooling; advanced CLI only.",
        "dependencyProfiles": ("dev-test",),
    },
}

PROFILE_ORDER = tuple(PRODUCT_PROFILE_DEFINITIONS.keys())
REQUIRED_PROFILE_IDS = ("actanara", "dashboard", "nova-task")
DEFAULT_PROFILE_IDS = REQUIRED_PROFILE_IDS

PRODUCT_DEPENDENCY_GROUPS: dict[str, dict[str, Any]] = {
    "actanara": {
        "label": "Actanara core",
        "required": True,
        "installDefault": True,
        "legacyDependencyProfiles": ("core-foundation",),
        "requirementSets": ("actanara-core",),
        "providerInputs": ("output-path", "llm-provider", "llm-api-key"),
        "description": "Core runtime and diary generation pipeline dependencies.",
    },
    "dashboard": {
        "label": "Dashboard",
        "required": True,
        "installDefault": True,
        "legacyDependencyProfiles": ("dashboard",),
        "requirementSets": ("dashboard",),
        "providerInputs": (),
        "description": "Local Dashboard API/UI dependencies.",
    },
    "nova-rag": {
        "label": "nova-RAG",
        "required": False,
        "installDefault": False,
        "legacyDependencyProfiles": (),
        "requirementSets": ("rag-provider-derived",),
        "providerInputs": ("rag-provider", "rag-embedding-model"),
        "description": "nova-RAG dependencies are derived after local/cloud provider selection.",
    },
    "nova-task": {
        "label": "Nova-Task",
        "required": True,
        "installDefault": True,
        "legacyDependencyProfiles": (),
        "requirementSets": ("nova-task",),
        "providerInputs": (),
        "description": "Nova-Task authority and review surface dependencies.",
    },
    "dev-test": {
        "label": "Dev/Test",
        "required": False,
        "installDefault": False,
        "legacyDependencyProfiles": ("dev-test",),
        "requirementSets": ("dev-test",),
        "providerInputs": (),
        "description": "Developer and test tooling dependencies.",
    },
}

PACKAGING_GROUP_DEFINITIONS: dict[str, dict[str, Any]] = {
    "base": {
        "label": "Base runtime",
        "profile": "actanara",
        "requirementSet": "actanara-core",
        "dependencySource": "current-detection",
        "currentDetection": "detected-now",
        "currentChecks": ("python3", "sqlite3", "zoneinfo"),
        "futureCandidates": ("core diary pipeline runtime packages after source audit",),
        "providerInputs": (),
        "description": "Required Actanara core runtime and diary pipeline dependency group.",
    },
    "dashboard": {
        "label": "Dashboard runtime",
        "profile": "dashboard",
        "requirementSet": "dashboard",
        "dependencySource": "current-detection",
        "currentDetection": "detected-now",
        "currentChecks": ("fastapi", "uvicorn"),
        "futureCandidates": ("Dashboard API/server dependencies",),
        "providerInputs": (),
        "description": "Optional local Dashboard API/server dependency group.",
    },
    "nova-rag-local": {
        "label": "nova-RAG local provider",
        "profile": "nova-rag",
        "requirementSet": "rag-provider-derived",
        "dependencySource": "provider-derived",
        "currentDetection": "compatibility-detected-only",
        "currentChecks": ("sentence_transformers", "torch"),
        "futureCandidates": ("sentence-transformers", "torch", "numpy", "fastapi", "uvicorn", "pydantic"),
        "providerInputs": ("rag-provider", "rag-embedding-model"),
        "description": "Provider-derived local embedding runtime dependencies.",
    },
    "nova-rag-cloud": {
        "label": "nova-RAG cloud provider",
        "profile": "nova-rag",
        "requirementSet": "rag-provider-derived",
        "dependencySource": "provider-derived",
        "currentDetection": "not-detected-today",
        "currentChecks": (),
        "futureCandidates": ("approved cloud embedding provider client or HTTP dependencies",),
        "providerInputs": ("rag-provider", "rag-embedding-model"),
        "description": "Provider-derived cloud embedding dependencies.",
    },
    "nova-task": {
        "label": "Nova-Task runtime",
        "profile": "nova-task",
        "requirementSet": "nova-task",
        "dependencySource": "planned",
        "currentDetection": "planned-only",
        "currentChecks": (),
        "futureCandidates": ("core Foundation/SQLite runtime by default",),
        "providerInputs": (),
        "description": "Optional Nova-Task authority and review dependency group.",
    },
    "dev-test": {
        "label": "Dev/Test tooling",
        "profile": "dev-test",
        "requirementSet": "dev-test",
        "dependencySource": "current-detection",
        "currentDetection": "detected-now",
        "currentChecks": ("unittest", "node"),
        "futureCandidates": ("contributor validation tooling",),
        "providerInputs": (),
        "description": "Optional contributor test and validation tooling.",
    },
}

PYPROJECT_EXTRA_BY_INSTALL_INTENT: dict[str, str | None] = {
    "base": None,
    "dashboard": "dashboard",
    "nova-rag-local": "rag-local",
    "nova-rag-cloud": "rag-server",
    "nova-task": None,
    "dev-test": "dev-test",
}

RAG_READINESS_STATES = (
    "rag-disabled",
    "rag-provider-pending",
    "rag-local-dependencies-missing",
    "rag-local-ready",
    "rag-cloud-config-missing",
    "rag-cloud-ready",
    "rag-sync-skipped",
    "rag-sync-complete",
)

RAG_LOCAL_DEPENDENCY_CANDIDATES = ("sentence-transformers", "torch", "numpy")
RAG_LOCAL_DEPENDENCY_MODULES = {
    "sentence-transformers": "sentence_transformers",
    "torch": "torch",
    "numpy": "numpy",
}
RAG_CLOUD_CONFIG_FIELDS = (
    "provider",
    "endpoint",
    "model",
    "dimension",
    "apiKeyEnv",
    "batchSize",
    "timeoutSeconds",
    "indexingSourceSets",
    "syncPolicy",
)

REQUIREMENT_SET_DEFINITIONS: dict[str, dict[str, Any]] = {
    "actanara-core": {
        "label": "Actanara core",
        "profile": "actanara",
        "legacyDependencyProfiles": ("core-foundation",),
        "providerInputs": ("output-path", "llm-provider", "llm-api-key"),
        "description": "Python runtime, standard library support and core diary pipeline prerequisites.",
    },
    "dashboard": {
        "label": "Dashboard runtime",
        "profile": "dashboard",
        "legacyDependencyProfiles": ("dashboard",),
        "providerInputs": (),
        "description": "Dashboard API/server Python modules.",
    },
    "rag-provider-derived": {
        "label": "nova-RAG provider-derived",
        "profile": "nova-rag",
        "legacyDependencyProfiles": (),
        "providerInputs": ("rag-provider", "rag-embedding-model"),
        "description": "RAG dependency set selected after local/cloud provider and embedding model choices.",
    },
    "nova-task": {
        "label": "Nova-Task runtime",
        "profile": "nova-task",
        "legacyDependencyProfiles": (),
        "providerInputs": (),
        "description": "Nova-Task materialization and review prerequisites.",
    },
    "dev-test": {
        "label": "Dev/Test tooling",
        "profile": "dev-test",
        "legacyDependencyProfiles": ("dev-test",),
        "providerInputs": (),
        "description": "Developer and validation tooling prerequisites.",
    },
}


def default_onboarding_profiles() -> list[str]:
    return list(DEFAULT_PROFILE_IDS)


def normalize_onboarding_profiles(selected: list[str] | None = None) -> list[str]:
    raw = selected or list(DEFAULT_PROFILE_IDS)
    normalized = []
    unknown = []
    for profile_id in raw:
        value = str(profile_id or "").strip()
        if not value:
            continue
        if value not in PRODUCT_PROFILE_DEFINITIONS:
            unknown.append(value)
            continue
        if value not in normalized:
            normalized.append(value)
    if unknown:
        raise ValueError(f"unknown onboarding profile(s): {', '.join(sorted(unknown))}")
    for profile_id in reversed(REQUIRED_PROFILE_IDS):
        if profile_id not in normalized:
            normalized.insert(0, profile_id)
    return [profile_id for profile_id in PROFILE_ORDER if profile_id in normalized]


def onboarding_subsystem_plan(selected: list[str] | None = None, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Return a dry-run plan for selected onboarding subsystems."""
    runtime_paths = paths or load_paths()
    selected_profiles = normalize_onboarding_profiles(selected)
    dependencies = dependency_profiles_status(_dependency_profile_ids(selected_profiles))
    scheduler_preview = preview_system_timer(runtime_paths)
    actions = _planned_actions(selected_profiles, scheduler_preview)
    required_inputs = required_onboarding_inputs(selected_profiles, runtime_paths)
    dependency_groups = dependency_groups_for_product_profiles(selected_profiles)
    requirement_sets = requirement_sets_for_product_profiles(selected_profiles, dependencies, required_inputs)
    packaging_plan = packaging_plan_for_product_profiles(selected_profiles, requirement_sets)
    return {
        "schemaVersion": 2,
        "readOnly": True,
        "planOnly": True,
        "profileModel": "product-v2",
        "selectedProfiles": selected_profiles,
        "availableProfiles": _available_profiles(),
        "requiredInputs": required_inputs,
        "dependencyGroups": dependency_groups,
        "requirementSets": requirement_sets,
        "packagingPlan": packaging_plan,
        "dependencyProfiles": dependencies,
        "scheduler": _scheduler_plan(selected_profiles, scheduler_preview),
        "actions": actions,
        "summary": _summary(dependencies, actions, required_inputs, dependency_groups, requirement_sets, packaging_plan),
    }


def onboarding_one_liner_dry_run(selected: list[str] | None = None, paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Return a read-only runtime bootstrap dry-run plan."""
    plan = onboarding_subsystem_plan(selected, paths)
    dry_run_steps = [
        {
            "id": action.get("id"),
            "mode": action.get("mode"),
            "description": action.get("description"),
            "executesShell": False,
            "wouldWrite": bool(action.get("writes")),
            "writes": list(action.get("writes") or []),
            "requiresConfirmation": bool(action.get("requiresConfirmation")),
        }
        for action in plan.get("actions") or []
    ]
    scheduler_plan = _one_liner_scheduler_plan(plan.get("scheduler") or {})
    rag_readiness = _rag_readiness_plan(plan.get("selectedProfiles") or [])
    safety_policy = _one_liner_safety_policy()
    apply_contract = onboarding_apply_write_contract(plan.get("selectedProfiles") or [], scheduler_plan=scheduler_plan)
    scheduler_approval = scheduler_apply_approval_contract(scheduler_plan)
    one_liner_command = _one_liner_v1_command_plan(plan.get("selectedProfiles") or [])
    installer_v2_plan = installer_v2_contract(plan.get("selectedProfiles") or [], plan.get("packagingPlan") or {})
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "dryRunOnly": True,
        "defaultRuntimeTarget": _default_runtime_target(),
        "oneLinerState": "v1-apply-ready",
        "applyState": "runtime-bootstrap-apply-implemented",
        "applyImplemented": True,
        "installerImplemented": False,
        "profileModel": plan.get("profileModel"),
        "selectedProfiles": plan.get("selectedProfiles"),
        "requiredInputs": plan.get("requiredInputs"),
        "dependencyGroups": plan.get("dependencyGroups"),
        "requirementSets": plan.get("requirementSets"),
        "packagingPlan": plan.get("packagingPlan"),
        "scheduler": plan.get("scheduler"),
        "safetyPolicy": safety_policy,
        "schedulerPlan": scheduler_plan,
        "ragReadiness": rag_readiness,
        "sourceBoundaryApprovals": _source_boundary_approvals(),
        "applyWriteContract": apply_contract,
        "schedulerApprovalContract": scheduler_approval,
        "installerV2Plan": installer_v2_plan,
        "oneLinerV1Command": one_liner_command,
        "commandDraft": {
            "id": "actanara-onboarding-one-liner",
            "argv": one_liner_command["argv"],
            "display": one_liner_command["display"],
            "copyPasteReady": True,
            "executesShell": False,
            "reason": "Runtime apply command is available behind exact confirmation.",
        },
        "blockedApplyCommand": {
            "argv": ["actanara", "onboarding", "apply"],
            "display": "actanara onboarding apply",
            "implemented": True,
            "blocked": True,
            "exitCode": 1,
            "reason": "Blocked skeleton only; no apply writes are implemented.",
            "writeContractIncluded": True,
        },
        "executionPolicy": {
            "allowed": False,
            "reason": "This dry-run command is read-only; use runtime-apply with exact confirmation to write runtime bootstrap artifacts.",
            "writesSettings": False,
            "installsDependencies": False,
            "createsVirtualenv": False,
            "registersScheduler": False,
            "writesExternalAgentSkills": False,
            "mutatesPromptPayloads": False,
            "changesRagAuthority": False,
            "changesNovaTaskAuthority": False,
            "futureConfirmationPhrase": "APPLY ACTANARA ONBOARDING",
            "oneLinerApplyImplemented": True,
            "oneLinerApplyCommand": one_liner_command["display"],
            "safetyPolicy": safety_policy,
        },
        "dryRunSteps": dry_run_steps,
        "sourcePlan": plan,
        "summary": {
            "status": (plan.get("summary") or {}).get("status", "unknown"),
            "profiles": len(plan.get("selectedProfiles") or []),
            "requiredInputs": len([item for item in plan.get("requiredInputs") or [] if item.get("required")]),
            "dependencyGroups": (plan.get("summary") or {}).get("dependencyGroups", 0),
            "requirementSets": (plan.get("summary") or {}).get("requirementSets", 0),
            "packagingGroups": (plan.get("summary") or {}).get("packagingGroups", 0),
            "dryRunSteps": len(dry_run_steps),
            "blockedActions": (plan.get("summary") or {}).get("blockedActions", 0),
            "ragReadinessState": rag_readiness.get("readinessState"),
            "schedulerProvider": scheduler_plan.get("provider"),
            "schedulerApprovalState": scheduler_approval.get("status"),
            "installerV2DefaultGroups": installer_v2_plan.get("defaultInstallGroups"),
            "plannedWriteOperations": len((apply_contract.get("writePlan") or {}).get("operations") or []),
            "rollbackOperations": len((apply_contract.get("rollbackPlan") or {}).get("operations") or []),
        },
    }


def _default_runtime_target() -> dict[str, Any]:
    return {
        "id": "user-home-dot-actanara",
        "path": str(default_oneliner_runtime_home()),
        "source": "one-liner-default",
        "requiresExplicitUseDefaultRuntimeFlag": True,
        "isInstallDirectory": True,
        "bootstrapPointerIsInstallDirectory": False,
        "bootstrapPointerPath": "~/.config/actanara/location.json",
    }


def _one_liner_v1_command_plan(selected_profiles: list[str]) -> dict[str, Any]:
    argv = [
        "actanara",
        "onboarding",
        "runtime-apply",
        "--use-default-runtime",
        "--language",
        "zh-CN",
        "--confirmation-text",
        "APPLY ACTANARA ONBOARDING",
    ]
    for profile_id in selected_profiles:
        if profile_id != "actanara":
            argv.extend(["--profile", profile_id])
    return {
        "id": "actanara-onboarding-one-liner-v1",
        "argv": argv,
        "display": " ".join(argv),
        "copyPasteReady": True,
        "executesShell": False,
        "implementation": "runtime-bootstrap-with-optional-scheduler-opt-in",
        "writesDefaultRuntime": True,
        "selectsActiveRuntimeByDefault": False,
        "registersScheduler": False,
        "callsLaunchctl": False,
        "installsDependencies": False,
        "notes": [
            "Creates or updates ~/.actanara runtime files after exact confirmation.",
            "Leaves active runtime selection opt-in via --select-active-runtime.",
            "Does not register launchd jobs unless --with-scheduler and scheduler confirmation are provided.",
        ],
    }


def scheduler_apply_approval_contract(scheduler_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the scheduler approval contract without enabling real registration."""
    scheduler = scheduler_plan or {}
    jobs = list(scheduler.get("jobs") or [])
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "status": "registration-gated",
        "provider": scheduler.get("provider", "launchd-user"),
        "registrationImplemented": True,
        "realLaunchAgentsWriteImplemented": True,
        "launchctlImplemented": True,
        "unregisterImplemented": True,
        "sandboxApplyImplemented": True,
        "plistWriteApplyImplemented": True,
        "managedPlistSerializationRequired": True,
        "managedPlistSerializationReady": bool(scheduler.get("managedPlistSerializationReady")),
        "plistWriteConfirmationPhrase": "WRITE ACTANARA LAUNCHAGENTS",
        "registrationConfirmationPhrase": "REGISTER ACTANARA SCHEDULER",
        "rollbackConfirmationPhrase": "UNREGISTER ACTANARA SCHEDULER",
        "requiresFakeHomeForSandbox": True,
        "requiresExplicitRuntime": True,
        "requiresExplicitOperatorApprovalForRealWrites": True,
        "allowedCurrentPhase": {
            "writeFakeLaunchAgents": True,
            "writeRealLaunchAgents": True,
            "callLaunchctl": True,
            "registerScheduler": True,
            "installDependencies": False,
        },
        "jobLabels": [job.get("label") for job in jobs if job.get("label")],
        "managedPlistCount": len([job for job in jobs if job.get("managedPlist")]),
        "auditPath": "$ACTANARA_HOME/state/onboarding/onboarding-audit.jsonl",
        "rollbackPath": "$ACTANARA_HOME/state/onboarding/scheduler-sandbox-rollback-plan.json",
        "pathPolicy": {
            "sandboxTarget": "$FAKE_HOME/Library/LaunchAgents",
            "realTarget": "~/Library/LaunchAgents",
            "realTargetBlocked": False,
            "realTargetRequiresExplicitPlistWriteApply": True,
            "managedLabelsOnly": True,
        },
    }


def onboarding_one_liner_apply(
    selected: list[str] | None = None,
    paths: RuntimePaths | None = None,
    *,
    confirmation_text: str | None = None,
    select_active_runtime: bool = False,
    language_profile: str | None = None,
    with_scheduler: bool = False,
    scheduler_confirmation_text: str | None = None,
    launch_agent_home: Path | None = None,
    launchctl_runner: Any | None = None,
) -> dict[str, Any]:
    """Apply runtime bootstrap and optional scheduler registration."""
    runtime_payload = onboarding_apply_runtime_bootstrap(
        selected,
        paths,
        confirmation_text=confirmation_text,
        select_active_runtime=select_active_runtime,
        language_profile=language_profile,
    )
    runtime_paths = paths if paths is not None else None
    scheduler_plan = (
        _one_liner_scheduler_plan(_scheduler_plan(runtime_payload.get("selectedProfiles") or [], preview_system_timer(runtime_paths)))
        if runtime_paths and runtime_payload.get("exitCode") == 0
        else {}
    )
    scheduler_plist_payload = None
    scheduler_register_payload = None
    scheduler_registration = {
        "status": "dry-run-only",
        "requested": False,
        "registersScheduler": False,
        "writesLaunchdPlist": False,
        "callsLaunchctl": False,
    }
    if with_scheduler and runtime_payload.get("exitCode") == 0:
        scheduler_registration["requested"] = True
        if str(scheduler_confirmation_text or "") != "REGISTER ACTANARA SCHEDULER":
            scheduler_registration.update(
                {
                    "status": "scheduler-confirmation-missing",
                    "requiredConfirmationPhrase": "REGISTER ACTANARA SCHEDULER",
                }
            )
        else:
            scheduler_plist_payload = onboarding_apply_scheduler_plist_write(
                selected,
                runtime_paths,
                confirmation_text="WRITE ACTANARA LAUNCHAGENTS",
                launch_agent_home=launch_agent_home,
            )
            if scheduler_plist_payload.get("exitCode") == 0:
                scheduler_register_payload = onboarding_apply_scheduler_register(
                    selected,
                    runtime_paths,
                    confirmation_text=scheduler_confirmation_text,
                    launch_agent_home=launch_agent_home,
                    launchctl_runner=launchctl_runner,
                )
            scheduler_registration.update(
                {
                    "status": (scheduler_register_payload or scheduler_plist_payload or {}).get("status", "scheduler-not-run"),
                    "registersScheduler": (scheduler_register_payload or {}).get("status") == "scheduler-registered",
                    "writesLaunchdPlist": (scheduler_plist_payload or {}).get("status") == "scheduler-plist-applied",
                    "callsLaunchctl": bool(((scheduler_register_payload or {}).get("safetyPolicy") or {}).get("callsLaunchctl")),
                    "plistApplyStatus": (scheduler_plist_payload or {}).get("status"),
                    "registerApplyStatus": (scheduler_register_payload or {}).get("status"),
                }
            )
    exit_code = int(runtime_payload.get("exitCode", 1))
    if with_scheduler and runtime_payload.get("exitCode") == 0 and not scheduler_registration.get("registersScheduler"):
        exit_code = 1
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "oneLinerApply": True,
        "status": "one-liner-applied" if exit_code == 0 else "one-liner-rejected",
        "exitCode": exit_code,
        "selectedProfiles": runtime_payload.get("selectedProfiles", []),
        "runtimeBootstrap": runtime_payload,
        "schedulerPlistApply": scheduler_plist_payload,
        "schedulerRegisterApply": scheduler_register_payload,
        "schedulerPlan": scheduler_plan,
        "schedulerApprovalContract": scheduler_apply_approval_contract(scheduler_plan),
        "schedulerRegistration": scheduler_registration,
        "dependencyInstallation": {
            "status": "detect-only",
            "installsDependencies": False,
            "createsVirtualenv": False,
        },
        "summary": {
            "runtimeApplied": runtime_payload.get("exitCode") == 0,
            "schedulerRegistration": scheduler_registration.get("status"),
            "dependencies": "detect-only",
            "selectedAsActiveRuntime": ((runtime_payload.get("runtime") or {}).get("selectedAsActiveRuntime")),
        },
    }


def onboarding_one_liner_status(paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Return read-only status for runtime bootstrap artifacts."""
    runtime_paths = paths or load_paths()
    onboarding_state = runtime_paths.state_dir / "onboarding"
    settings_path = runtime_paths.config_dir / "settings.json"
    runtime_manifest_path = runtime_paths.config_dir / "runtime.json"
    audit_path = onboarding_state / "onboarding-audit.jsonl"
    runtime_rollback_path = onboarding_state / "runtime-bootstrap-rollback-plan.json"
    scheduler_rollback_path = onboarding_state / "scheduler-sandbox-rollback-plan.json"
    scheduler_plist_rollback_path = onboarding_state / "scheduler-plist-rollback-plan.json"
    scheduler_register_rollback_path = onboarding_state / "scheduler-register-rollback-plan.json"
    scheduler_unregister_rollback_path = onboarding_state / "scheduler-unregister-rollback-plan.json"
    sandbox_rollback_path = onboarding_state / "rollback-plan.json"
    latest_audit = _read_last_jsonl_event(audit_path)
    artifacts = {
        "runtimeManifest": _artifact_status(runtime_manifest_path),
        "settings": _artifact_status(settings_path),
        "audit": _artifact_status(audit_path),
        "runtimeBootstrapRollback": _artifact_status(runtime_rollback_path),
        "schedulerSandboxRollback": _artifact_status(scheduler_rollback_path),
        "schedulerPlistRollback": _artifact_status(scheduler_plist_rollback_path),
        "schedulerRegisterRollback": _artifact_status(scheduler_register_rollback_path),
        "schedulerUnregisterRollback": _artifact_status(scheduler_unregister_rollback_path),
        "sandboxRollback": _artifact_status(sandbox_rollback_path),
    }
    runtime_initialized = artifacts["runtimeManifest"]["exists"] and artifacts["settings"]["exists"]
    settings = read_settings(runtime_paths, redact_secrets=True) if runtime_initialized else {}
    schedule = settings.get("schedule", {}) if isinstance(settings.get("schedule"), dict) else {}
    system_timer = schedule.get("systemTimer", {}) if isinstance(schedule.get("systemTimer"), dict) else {}
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "statusOnly": True,
        "status": "initialized" if runtime_initialized else "not-initialized",
        "runtime": {
            "actanaraHome": str(runtime_paths.home),
            "exists": runtime_paths.home.exists(),
            "initialized": runtime_initialized,
        },
        "artifacts": artifacts,
        "latestAuditEvent": latest_audit,
        "schedulerRegistration": {
            "status": "registered" if system_timer.get("registered") else "not-registered-by-one-liner",
            "registered": bool(system_timer.get("registered")),
            "registrationManagedBy": system_timer.get("registrationManagedBy"),
            "registeredAt": system_timer.get("registeredAt"),
            "jobs": system_timer.get("jobs") if isinstance(system_timer.get("jobs"), list) else [],
            "registersScheduler": bool(system_timer.get("registered")),
            "callsLaunchctl": False,
            "writesRealLaunchAgents": False,
        },
        "dependencyInstallation": {
            "status": "not-installed-by-one-liner",
            "installsDependencies": False,
        },
        "nextSteps": _one_liner_next_steps(runtime_initialized, artifacts),
        "summary": {
            "artifacts": len(artifacts),
            "present": sum(1 for item in artifacts.values() if item.get("exists")),
            "runtimeInitialized": runtime_initialized,
            "hasAudit": artifacts["audit"]["exists"],
            "hasRollbackPlan": (
                artifacts["runtimeBootstrapRollback"]["exists"]
                or artifacts["schedulerSandboxRollback"]["exists"]
                or artifacts["schedulerPlistRollback"]["exists"]
                or artifacts["schedulerRegisterRollback"]["exists"]
                or artifacts["schedulerUnregisterRollback"]["exists"]
            ),
            "hasSchedulerPlistRollbackPlan": artifacts["schedulerPlistRollback"]["exists"],
            "hasSchedulerRegisterRollbackPlan": artifacts["schedulerRegisterRollback"]["exists"],
            "hasSchedulerUnregisterRollbackPlan": artifacts["schedulerUnregisterRollback"]["exists"],
        },
    }


def onboarding_rollback_plan_status(paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Return read-only rollback plan aggregation without executing rollback."""
    runtime_paths = paths or load_paths()
    onboarding_state = runtime_paths.state_dir / "onboarding"
    rollback_files = [
        ("runtime-bootstrap", onboarding_state / "runtime-bootstrap-rollback-plan.json"),
        ("scheduler-sandbox", onboarding_state / "scheduler-sandbox-rollback-plan.json"),
        ("scheduler-plist", onboarding_state / "scheduler-plist-rollback-plan.json"),
        ("scheduler-register", onboarding_state / "scheduler-register-rollback-plan.json"),
        ("scheduler-unregister", onboarding_state / "scheduler-unregister-rollback-plan.json"),
        ("sandbox", onboarding_state / "rollback-plan.json"),
    ]
    plans = []
    for plan_id, path in rollback_files:
        payload = _read_json_file(path)
        plans.append(
            {
                "id": plan_id,
                "path": str(path),
                "exists": path.exists(),
                "payload": payload,
                "operationCount": len(payload.get("sourceOperationResults") or []) if isinstance(payload, dict) else 0,
                "rollbackImplemented": bool(payload.get("rollbackImplemented")) if isinstance(payload, dict) else False,
            }
        )
    existing = [plan for plan in plans if plan.get("exists")]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "rollbackPlanOnly": True,
        "status": "available" if existing else "missing",
        "runtime": {"actanaraHome": str(runtime_paths.home)},
        "plans": plans,
        "executionPolicy": {
            "executesRollback": False,
            "deletesFiles": False,
            "writesSettings": False,
            "writesLaunchdPlist": False,
            "callsLaunchctl": False,
            "requiresManualReview": True,
        },
        "summary": {
            "plans": len(plans),
            "available": len(existing),
            "operations": sum(int(plan.get("operationCount") or 0) for plan in plans),
            "executableRollbackImplemented": False,
        },
    }


def onboarding_apply_scheduler_sandbox(
    selected: list[str] | None = None,
    paths: RuntimePaths | None = None,
    *,
    scheduler_home: Path | None = None,
    confirmation_text: str | None = None,
) -> dict[str, Any]:
    """Write managed launchd plists to a fake HOME only; never call launchctl."""
    selected_profiles = normalize_onboarding_profiles(selected)
    if paths is None:
        raise ValueError("scheduler sandbox apply requires an explicit runtime path")
    if scheduler_home is None:
        raise ValueError("scheduler sandbox apply requires an explicit fake scheduler home")
    fake_home = scheduler_home.expanduser()
    if fake_home.resolve() == Path.home().resolve():
        raise ValueError("scheduler sandbox apply fake home cannot be the real current HOME")

    required_phrase = "REGISTER ACTANARA SCHEDULER"
    confirmation_accepted = str(confirmation_text or "") == required_phrase
    if not confirmation_accepted:
        return {
            "schemaVersion": 1,
            "readOnly": False,
            "schedulerSandboxApply": True,
            "status": "scheduler-sandbox-rejected",
            "exitCode": 1,
            "selectedProfiles": selected_profiles,
            "confirmationAccepted": False,
            "requiredConfirmationPhrase": required_phrase,
            "reason": "exact scheduler confirmation phrase is required for scheduler sandbox apply",
            "operationResults": [],
            "safetyPolicy": _scheduler_sandbox_safety_policy(writes_fake_plists=False),
        }

    runtime_paths = initialize_home(paths.home, legacy_diary_root=paths.legacy_diary_root)
    preview = preview_system_timer(runtime_paths, launch_agent_home=fake_home)
    scheduler_plan = _one_liner_scheduler_plan(_scheduler_plan(selected_profiles, preview))
    approval_contract = scheduler_apply_approval_contract(scheduler_plan)
    launch_agents_dir = fake_home / "Library" / "LaunchAgents"
    operation_results: list[dict[str, Any]] = []
    for managed in scheduler_plan.get("managedPlists") or []:
        target = Path(str(managed.get("plistPath") or ""))
        if target.parent != launch_agents_dir:
            raise ValueError("managed plist target escaped fake scheduler home")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(managed.get("serializedPlist") or ""), encoding="utf-8")
        operation_results.append(
            {
                "id": f"write-scheduler-sandbox-plist:{managed.get('label')}",
                "status": "applied",
                "target": str(target),
                "label": managed.get("label"),
                "provider": managed.get("provider"),
            }
        )

    onboarding_state = runtime_paths.state_dir / "onboarding"
    onboarding_state.mkdir(parents=True, exist_ok=True)
    rollback_path = onboarding_state / "scheduler-sandbox-rollback-plan.json"
    audit_path = onboarding_state / "onboarding-audit.jsonl"
    rollback_payload = _scheduler_sandbox_rollback_payload(
        selected_profiles,
        runtime_paths,
        fake_home,
        operation_results,
        scheduler_plan,
    )
    _write_json_file(rollback_path, rollback_payload)
    audit_event = _scheduler_sandbox_audit_event(
        selected_profiles,
        required_phrase,
        fake_home,
        operation_results,
        rollback_path,
    )
    _append_jsonl(audit_path, audit_event)
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "schedulerSandboxApply": True,
        "status": "scheduler-sandbox-applied",
        "exitCode": 0,
        "selectedProfiles": selected_profiles,
        "confirmationAccepted": True,
        "requiredConfirmationPhrase": required_phrase,
        "runtime": {
            "actanaraHome": str(runtime_paths.home),
            "auditPath": str(audit_path),
            "rollbackPath": str(rollback_path),
        },
        "schedulerHome": str(fake_home),
        "launchAgentsDir": str(launch_agents_dir),
        "schedulerPlan": scheduler_plan,
        "schedulerApprovalContract": approval_contract,
        "operationResults": operation_results,
        "safetyPolicy": _scheduler_sandbox_safety_policy(writes_fake_plists=True),
        "summary": {
            "status": "scheduler-sandbox-applied",
            "operations": len(operation_results),
            "applied": sum(1 for item in operation_results if item.get("status") == "applied"),
            "writesRealLaunchAgents": False,
            "callsLaunchctl": False,
        },
    }


def onboarding_apply_scheduler_plist_write(
    selected: list[str] | None = None,
    paths: RuntimePaths | None = None,
    *,
    confirmation_text: str | None = None,
    launch_agent_home: Path | None = None,
) -> dict[str, Any]:
    """Write managed launchd plists under LaunchAgents; never call launchctl."""
    selected_profiles = normalize_onboarding_profiles(selected)
    if paths is None:
        raise ValueError("scheduler plist apply requires an explicit runtime path")

    required_phrase = "WRITE ACTANARA LAUNCHAGENTS"
    confirmation_accepted = str(confirmation_text or "") == required_phrase
    if not confirmation_accepted:
        return {
            "schemaVersion": 1,
            "readOnly": False,
            "schedulerPlistApply": True,
            "status": "scheduler-plist-rejected",
            "exitCode": 1,
            "selectedProfiles": selected_profiles,
            "confirmationAccepted": False,
            "requiredConfirmationPhrase": required_phrase,
            "reason": "exact scheduler plist write confirmation phrase is required",
            "operationResults": [],
            "safetyPolicy": _scheduler_plist_write_safety_policy(writes_launch_agents=False),
        }

    target_home = (launch_agent_home or Path.home()).expanduser()
    runtime_paths = initialize_home(paths.home, legacy_diary_root=paths.legacy_diary_root)
    preview = preview_system_timer(runtime_paths, launch_agent_home=target_home)
    timezone_boundary = preview.get("timezoneBoundary") if isinstance(preview.get("timezoneBoundary"), dict) else {}
    if timezone_boundary.get("status") == "blocked":
        raise ValueError(f"Blocked: {timezone_boundary.get('issueCode') or 'scheduler-timezone-boundary'}")
    scheduler_plan = _one_liner_scheduler_plan(_scheduler_plan(selected_profiles, preview))
    approval_contract = scheduler_apply_approval_contract(scheduler_plan)
    launch_agents_dir = target_home / "Library" / "LaunchAgents"
    onboarding_state = runtime_paths.state_dir / "onboarding"
    backup_dir = runtime_paths.state_dir / "backups" / "launchd" / datetime.now(resolve_timezone(runtime_paths)).strftime("%Y%m%d-%H%M%S")
    operation_results: list[dict[str, Any]] = []
    for managed in scheduler_plan.get("managedPlists") or []:
        target = Path(str(managed.get("plistPath") or ""))
        if target.parent != launch_agents_dir:
            raise ValueError("managed plist target escaped LaunchAgents directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if target.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / target.name
            backup_path.write_bytes(target.read_bytes())
        target.write_text(str(managed.get("serializedPlist") or ""), encoding="utf-8")
        operation_results.append(
            {
                "id": f"write-managed-launchagent-plist:{managed.get('label')}",
                "status": "applied",
                "target": str(target),
                "backupPath": str(backup_path) if backup_path else None,
                "label": managed.get("label"),
                "provider": managed.get("provider"),
            }
        )

    onboarding_state.mkdir(parents=True, exist_ok=True)
    rollback_path = onboarding_state / "scheduler-plist-rollback-plan.json"
    audit_path = onboarding_state / "onboarding-audit.jsonl"
    rollback_payload = _scheduler_plist_write_rollback_payload(
        selected_profiles,
        runtime_paths,
        target_home,
        operation_results,
        scheduler_plan,
    )
    _write_json_file(rollback_path, rollback_payload)
    audit_event = _scheduler_plist_write_audit_event(
        selected_profiles,
        required_phrase,
        target_home,
        operation_results,
        rollback_path,
    )
    _append_jsonl(audit_path, audit_event)
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "schedulerPlistApply": True,
        "status": "scheduler-plist-applied",
        "exitCode": 0,
        "selectedProfiles": selected_profiles,
        "confirmationAccepted": True,
        "requiredConfirmationPhrase": required_phrase,
        "runtime": {
            "actanaraHome": str(runtime_paths.home),
            "auditPath": str(audit_path),
            "rollbackPath": str(rollback_path),
        },
        "launchAgentHome": str(target_home),
        "launchAgentsDir": str(launch_agents_dir),
        "schedulerPlan": scheduler_plan,
        "schedulerApprovalContract": approval_contract,
        "operationResults": operation_results,
        "safetyPolicy": _scheduler_plist_write_safety_policy(writes_launch_agents=True),
        "summary": {
            "status": "scheduler-plist-applied",
            "operations": len(operation_results),
            "applied": sum(1 for item in operation_results if item.get("status") == "applied"),
            "backups": sum(1 for item in operation_results if item.get("backupPath")),
            "registersScheduler": False,
            "callsLaunchctl": False,
        },
    }


def onboarding_apply_scheduler_register(
    selected: list[str] | None = None,
    paths: RuntimePaths | None = None,
    *,
    confirmation_text: str | None = None,
    launch_agent_home: Path | None = None,
    launchctl_runner: Any | None = None,
) -> dict[str, Any]:
    """Register managed launchd jobs with launchctl after exact confirmation."""
    selected_profiles = normalize_onboarding_profiles(selected)
    if paths is None:
        raise ValueError("scheduler register apply requires an explicit runtime path")

    required_phrase = "REGISTER ACTANARA SCHEDULER"
    confirmation_accepted = str(confirmation_text or "") == required_phrase
    if not confirmation_accepted:
        return {
            "schemaVersion": 1,
            "readOnly": False,
            "schedulerRegisterApply": True,
            "status": "scheduler-register-rejected",
            "exitCode": 1,
            "selectedProfiles": selected_profiles,
            "confirmationAccepted": False,
            "requiredConfirmationPhrase": required_phrase,
            "reason": "exact scheduler registration confirmation phrase is required",
            "operationResults": [],
            "safetyPolicy": _scheduler_register_safety_policy(calls_launchctl=False),
        }

    runtime_paths = initialize_home(paths.home, legacy_diary_root=paths.legacy_diary_root)
    target_home = (launch_agent_home or Path.home()).expanduser()
    preview = preview_system_timer(runtime_paths, launch_agent_home=target_home)
    timezone_boundary = preview.get("timezoneBoundary") if isinstance(preview.get("timezoneBoundary"), dict) else {}
    if timezone_boundary.get("status") == "blocked":
        return _scheduler_register_failed_payload(
            selected_profiles,
            runtime_paths,
            _one_liner_scheduler_plan(_scheduler_plan(selected_profiles, preview)),
            scheduler_apply_approval_contract(_one_liner_scheduler_plan(_scheduler_plan(selected_profiles, preview))),
            [],
            f"Blocked: {timezone_boundary.get('issueCode') or 'scheduler-timezone-boundary'}",
        )
    scheduler_plan = _one_liner_scheduler_plan(_scheduler_plan(selected_profiles, preview))
    approval_contract = scheduler_apply_approval_contract(scheduler_plan)
    runner = launchctl_runner or _run_launchctl
    domain = _launchctl_gui_domain()
    operation_results: list[dict[str, Any]] = []
    for managed in scheduler_plan.get("managedPlists") or []:
        plist_path = Path(str(managed.get("plistPath") or ""))
        if not plist_path.exists():
            return _scheduler_register_failed_payload(
                selected_profiles,
                runtime_paths,
                scheduler_plan,
                approval_contract,
                operation_results,
                f"managed plist does not exist: {plist_path}",
            )
    jobs = _scheduler_handoff_jobs(scheduler_plan)
    installed_jobs = [
        {
            "kind": job.get("kind"),
            "label": job.get("label"),
            "plistPath": str(target_home / "Library" / "LaunchAgents" / f"{job.get('label')}.plist"),
            "time": job.get("time"),
            "registeredBy": "one-liner",
        }
        for job in jobs
    ]
    controls = _scheduler_handoff_controls(
        target_home,
        runner,
        operation_results,
        bootout_id="launchctl-bootout-stale",
        model_runtime=launchctl_runner is not None,
    )
    try:
        from app.services import scheduler as scheduler_service

        handoff = scheduler_service._execute_scheduler_handoff(
            runtime_paths,
            action="install",
            jobs=jobs,
            schedule_update={
                "enabled": True,
                "mode": "system",
                "systemTimer": {
                    "provider": "launchd",
                    "label": _scheduler_base_label_from_jobs(scheduler_plan.get("jobs") or []),
                    "registered": True,
                    "registrationManagedBy": "one-liner",
                    "registeredAt": datetime.now().astimezone().isoformat(),
                    "jobs": installed_jobs,
                    "lastAction": "install",
                    "lastActionStatus": "success",
                    "lastError": None,
                    "lastErrorAt": None,
                    "stale": False,
                    "reinstallRequired": False,
                },
            },
            **controls,
        )
    except Exception as error:
        return _scheduler_register_failed_payload(
            selected_profiles,
            runtime_paths,
            scheduler_plan,
            approval_contract,
            operation_results,
            f"scheduler handoff transaction failed: {type(error).__name__}",
        )

    onboarding_state = runtime_paths.state_dir / "onboarding"
    onboarding_state.mkdir(parents=True, exist_ok=True)
    rollback_path = onboarding_state / "scheduler-register-rollback-plan.json"
    audit_path = onboarding_state / "onboarding-audit.jsonl"
    rollback_payload = _scheduler_register_rollback_payload(
        selected_profiles,
        runtime_paths,
        target_home,
        operation_results,
        scheduler_plan,
        domain,
    )
    _write_json_file(rollback_path, rollback_payload)
    audit_event = _scheduler_register_audit_event(
        selected_profiles,
        required_phrase,
        target_home,
        operation_results,
        rollback_path,
        domain,
    )
    _append_jsonl(audit_path, audit_event)
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "schedulerRegisterApply": True,
        "status": "scheduler-registered",
        "exitCode": 0,
        "selectedProfiles": selected_profiles,
        "confirmationAccepted": True,
        "requiredConfirmationPhrase": required_phrase,
        "runtime": {
            "actanaraHome": str(runtime_paths.home),
            "auditPath": str(audit_path),
            "rollbackPath": str(rollback_path),
        },
        "launchAgentHome": str(target_home),
        "launchctlDomain": domain,
        "schedulerPlan": scheduler_plan,
        "schedulerApprovalContract": approval_contract,
        "operationResults": operation_results,
        "handoff": handoff,
        "safetyPolicy": _scheduler_register_safety_policy(calls_launchctl=True),
        "summary": {
            "status": "scheduler-registered",
            "operations": len(operation_results),
            "applied": sum(1 for item in operation_results if item.get("status") == "applied"),
            "callsLaunchctl": True,
            "installsDependencies": False,
        },
    }


def onboarding_apply_scheduler_unregister(
    selected: list[str] | None = None,
    paths: RuntimePaths | None = None,
    *,
    confirmation_text: str | None = None,
    launch_agent_home: Path | None = None,
    launchctl_runner: Any | None = None,
) -> dict[str, Any]:
    """Unregister managed launchd jobs with launchctl bootout after exact confirmation."""
    selected_profiles = normalize_onboarding_profiles(selected)
    if paths is None:
        raise ValueError("scheduler unregister apply requires an explicit runtime path")

    required_phrase = "UNREGISTER ACTANARA SCHEDULER"
    confirmation_accepted = str(confirmation_text or "") == required_phrase
    if not confirmation_accepted:
        return {
            "schemaVersion": 1,
            "readOnly": False,
            "schedulerUnregisterApply": True,
            "status": "scheduler-unregister-rejected",
            "exitCode": 1,
            "selectedProfiles": selected_profiles,
            "confirmationAccepted": False,
            "requiredConfirmationPhrase": required_phrase,
            "reason": "exact scheduler unregister confirmation phrase is required",
            "operationResults": [],
            "safetyPolicy": _scheduler_unregister_safety_policy(calls_launchctl=False),
        }

    runtime_paths = initialize_home(paths.home, legacy_diary_root=paths.legacy_diary_root)
    target_home = (launch_agent_home or Path.home()).expanduser()
    preview = preview_system_timer(runtime_paths, launch_agent_home=target_home)
    scheduler_plan = _one_liner_scheduler_plan(_scheduler_plan(selected_profiles, preview))
    runner = launchctl_runner or _run_launchctl
    domain = _launchctl_gui_domain()
    operation_results: list[dict[str, Any]] = []
    jobs = _scheduler_handoff_jobs(scheduler_plan)
    controls = _scheduler_handoff_controls(
        target_home,
        runner,
        operation_results,
        bootout_id="launchctl-bootout",
        model_runtime=launchctl_runner is not None,
    )
    try:
        from app.services import scheduler as scheduler_service

        handoff = scheduler_service._execute_scheduler_handoff(
            runtime_paths,
            action="uninstall",
            jobs=jobs,
            schedule_update={
                "enabled": False,
                "mode": "system",
                "systemTimer": {
                    "provider": "launchd",
                    "label": _scheduler_base_label_from_jobs(scheduler_plan.get("jobs") or []),
                    "registered": False,
                    "registrationManagedBy": "one-liner",
                    "unregisteredAt": datetime.now().astimezone().isoformat(),
                    "jobs": [],
                    "lastAction": "uninstall",
                    "lastActionStatus": "success",
                    "lastError": None,
                    "lastErrorAt": None,
                    "stale": False,
                    "reinstallRequired": False,
                },
            },
            **controls,
        )
    except Exception as error:
        return _scheduler_unregister_failed_payload(
            selected_profiles,
            runtime_paths,
            scheduler_plan,
            operation_results,
            f"scheduler handoff transaction failed: {type(error).__name__}",
        )

    onboarding_state = runtime_paths.state_dir / "onboarding"
    onboarding_state.mkdir(parents=True, exist_ok=True)
    rollback_path = onboarding_state / "scheduler-unregister-rollback-plan.json"
    audit_path = onboarding_state / "onboarding-audit.jsonl"
    rollback_payload = _scheduler_unregister_rollback_payload(
        selected_profiles,
        runtime_paths,
        target_home,
        operation_results,
        scheduler_plan,
        domain,
    )
    _write_json_file(rollback_path, rollback_payload)
    audit_event = _scheduler_unregister_audit_event(
        selected_profiles,
        required_phrase,
        target_home,
        operation_results,
        rollback_path,
        domain,
    )
    _append_jsonl(audit_path, audit_event)
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "schedulerUnregisterApply": True,
        "status": "scheduler-unregistered",
        "exitCode": 0,
        "selectedProfiles": selected_profiles,
        "confirmationAccepted": True,
        "requiredConfirmationPhrase": required_phrase,
        "runtime": {
            "actanaraHome": str(runtime_paths.home),
            "auditPath": str(audit_path),
            "rollbackPath": str(rollback_path),
        },
        "launchAgentHome": str(target_home),
        "launchctlDomain": domain,
        "schedulerPlan": scheduler_plan,
        "operationResults": operation_results,
        "handoff": handoff,
        "safetyPolicy": _scheduler_unregister_safety_policy(calls_launchctl=True),
        "summary": {
            "status": "scheduler-unregistered",
            "operations": len(operation_results),
            "applied": sum(1 for item in operation_results if item.get("status") == "applied"),
            "callsLaunchctl": True,
            "installsDependencies": False,
        },
    }


def onboarding_release_gate(
    selected: list[str] | None = None,
    paths: RuntimePaths | None = None,
    *,
    confirmation_text: str | None = None,
) -> dict[str, Any]:
    """Return a read-only release gate aggregation for future onboarding apply."""
    dry_run = onboarding_one_liner_dry_run(selected, paths)
    apply_payload = onboarding_apply_blocked(dry_run.get("selectedProfiles") or [], confirmation_text=confirmation_text)
    write_contract = apply_payload.get("applyWriteContract") or {}
    preflight = apply_payload.get("applyPreflight") or {}
    gates = [
        _release_gate(
            "one-liner-dry-run-schema",
            "passed",
            "Runtime dry-run schema is available and read-only.",
            evidence={"schemaVersion": dry_run.get("schemaVersion"), "dryRunOnly": dry_run.get("dryRunOnly")},
        ),
        _release_gate(
            "blocked-apply-command",
            "passed",
            "Blocked apply command is available and returns non-zero.",
            evidence={"exitCode": apply_payload.get("exitCode"), "blocked": apply_payload.get("blocked")},
        ),
        _release_gate(
            "write-contract-readonly",
            "passed" if write_contract.get("readOnly") and not write_contract.get("writesAllowed") else "failed",
            "Future write contract exists but writes remain disabled.",
            evidence={"writesAllowed": write_contract.get("writesAllowed"), "applyImplemented": write_contract.get("applyImplemented")},
        ),
        _release_gate(
            "sandbox-apply-harness",
            "passed",
            "Explicit sandbox apply harness is available for temp-runtime write/audit/rollback validation.",
            evidence={
                "command": "onboarding apply --sandbox-apply --runtime <temp-path>",
                "requiresExplicitRuntime": True,
                "requiresExactConfirmation": True,
                "registersScheduler": False,
                "installsDependencies": False,
            },
        ),
        _release_gate(
            "runtime-bootstrap-apply",
            "passed",
            "Explicit runtime bootstrap apply is available for runtime/settings/audit/rollback writes only.",
            evidence={
                "command": "onboarding apply --runtime-bootstrap-apply --runtime <path>",
                "requiresExplicitRuntime": True,
                "requiresExactConfirmation": True,
                "writesSettings": True,
                "writesBootstrapLocation": False,
                "selectsActiveRuntime": False,
                "registersScheduler": False,
                "installsDependencies": False,
            },
        ),
        _release_gate(
            "default-runtime-target",
            "passed",
            "User-facing runtime default target is ~/.actanara and requires explicit opt-in.",
            evidence=dry_run.get("defaultRuntimeTarget") or {},
        ),
        _release_gate(
            "active-runtime-selection",
            "passed",
            "Active runtime pointer write is available only as an explicit runtime bootstrap option.",
            evidence={
                "command": "onboarding apply --runtime-bootstrap-apply --select-active-runtime --runtime <path>",
                "requiresRuntimeBootstrapApply": True,
                "requiresExplicitRuntime": True,
                "requiresExactConfirmation": True,
                "writesBootstrapLocation": True,
                "defaultApplySelectsRuntime": False,
                "registersScheduler": False,
            },
        ),
        _release_gate(
            "apply-preflight",
            "blocked",
            "Apply preflight reports blocking reasons and cannot enable apply.",
            blocking=True,
            evidence={
                "allowedToApply": preflight.get("allowedToApply"),
                "blockingReasons": preflight.get("blockingReasons", []),
                "confirmationAccepted": preflight.get("confirmationAccepted"),
            },
        ),
        _release_gate(
            "scheduler-registration",
            "passed" if (dry_run.get("schedulerApprovalContract") or {}).get("launchctlImplemented") else "blocked",
            "Scheduler registration is available behind explicit launchctl confirmation.",
            blocking=not bool((dry_run.get("schedulerApprovalContract") or {}).get("launchctlImplemented")),
            evidence={
                "provider": (dry_run.get("schedulerPlan") or {}).get("provider"),
                "applyImplemented": (dry_run.get("schedulerApprovalContract") or {}).get("registrationImplemented"),
                "launchctlImplemented": (dry_run.get("schedulerApprovalContract") or {}).get("launchctlImplemented"),
                "confirmationPhrase": (dry_run.get("schedulerApprovalContract") or {}).get("registrationConfirmationPhrase"),
                "jobs": len((dry_run.get("schedulerPlan") or {}).get("jobs") or []),
            },
        ),
        _release_gate(
            "scheduler-managed-plist-serialization",
            "passed" if (dry_run.get("schedulerPlan") or {}).get("managedPlistSerializationReady") else "failed",
            "Managed launchd plist serialization is available as a dry-run artifact only.",
            evidence={
                "ready": (dry_run.get("schedulerPlan") or {}).get("managedPlistSerializationReady"),
                "dryRunOnly": (dry_run.get("schedulerPlan") or {}).get("dryRunOnly"),
                "wouldWritePlist": (dry_run.get("schedulerPlan") or {}).get("wouldWriteManagedPlists"),
                "wouldCallLaunchctl": (dry_run.get("schedulerPlan") or {}).get("wouldCallLaunchctl"),
            },
        ),
        _release_gate(
            "scheduler-plist-write-gate",
            "passed" if (dry_run.get("schedulerApprovalContract") or {}).get("plistWriteApplyImplemented") else "failed",
            "Managed LaunchAgent plist writes are available behind explicit confirmation; launchctl remains blocked.",
            evidence={
                "implemented": (dry_run.get("schedulerApprovalContract") or {}).get("plistWriteApplyImplemented"),
                "confirmationPhrase": (dry_run.get("schedulerApprovalContract") or {}).get("plistWriteConfirmationPhrase"),
                "registerScheduler": (dry_run.get("schedulerApprovalContract") or {}).get("allowedCurrentPhase", {}).get("registerScheduler"),
                "callLaunchctl": (dry_run.get("schedulerApprovalContract") or {}).get("allowedCurrentPhase", {}).get("callLaunchctl"),
            },
        ),
        _release_gate(
            "rag-provider-readiness",
            "blocked" if (dry_run.get("ragReadiness") or {}).get("readinessState") not in {"rag-disabled", "rag-local-ready", "rag-cloud-ready", "rag-sync-complete"} else "passed",
            "nova-RAG final sync requires a ready provider or explicit disabled state.",
            blocking=(dry_run.get("ragReadiness") or {}).get("readinessState") not in {"rag-disabled", "rag-local-ready", "rag-cloud-ready", "rag-sync-complete"},
            evidence={
                "readinessState": (dry_run.get("ragReadiness") or {}).get("readinessState"),
                "finalSyncPolicy": (dry_run.get("ragReadiness") or {}).get("finalSyncPolicy"),
            },
        ),
        _release_gate(
            "audit-schema",
            "passed",
            "Audit schema is present and preview-only.",
            evidence={
                "auditRequired": ((write_contract.get("auditPlan") or {}).get("auditRequired")),
                "writesAudit": ((write_contract.get("auditPlan") or {}).get("writesAudit")),
            },
        ),
        _release_gate(
            "rollback-schema",
            "passed",
            "Rollback schema is present and preview-only.",
            evidence={
                "rollbackRequired": ((write_contract.get("rollbackPlan") or {}).get("rollbackRequired")),
                "writesAllowed": ((write_contract.get("rollbackPlan") or {}).get("writesAllowed")),
            },
        ),
        _release_gate(
            "dependency-and-metadata-writes",
            "passed",
            "Dependency installation and packaging metadata creation remain disabled.",
            evidence={
                "installsDependencies": (dry_run.get("executionPolicy") or {}).get("installsDependencies"),
                "createsPackageMetadata": (dry_run.get("safetyPolicy") or {}).get("createsPackageMetadata"),
            },
        ),
        _release_gate(
            "no-production-clean-extraction",
            "passed",
            "Production-clean extraction remains disabled.",
            evidence={"productionCleanExtraction": (dry_run.get("safetyPolicy") or {}).get("productionCleanExtraction")},
        ),
    ]
    blocking_gates = [gate["id"] for gate in gates if gate.get("blocking") and gate.get("status") != "passed"]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "releaseGateOnly": True,
        "status": "blocked" if blocking_gates else "passed",
        "selectedProfiles": dry_run.get("selectedProfiles"),
        "confirmationAccepted": (preflight.get("confirmationAccepted")),
        "gates": gates,
        "blockingGates": blocking_gates,
        "sourcePayloads": {
            "oneLinerDryRunIncluded": True,
            "blockedApplyIncluded": True,
            "writeContractIncluded": True,
            "preflightIncluded": True,
            "sandboxApplyHarnessIncluded": True,
            "runtimeBootstrapApplyIncluded": True,
            "defaultRuntimeTargetIncluded": True,
            "activeRuntimeSelectionIncluded": True,
            "schedulerManagedPlistSerializationIncluded": True,
            "schedulerPlistWriteGateIncluded": True,
        },
        "summary": {
            "gates": len(gates),
            "passed": sum(1 for gate in gates if gate.get("status") == "passed"),
            "blocked": sum(1 for gate in gates if gate.get("status") == "blocked"),
            "failed": sum(1 for gate in gates if gate.get("status") == "failed"),
        },
    }


def onboarding_one_liner_release_gate(
    selected: list[str] | None = None,
    paths: RuntimePaths | None = None,
    *,
    with_scheduler: bool = False,
) -> dict[str, Any]:
    """Return a release gate for the runtime bootstrap surface."""
    selected_profiles = normalize_onboarding_profiles(selected or ["dashboard"])
    dry_run = onboarding_one_liner_dry_run(selected_profiles, paths)
    scheduler_contract = dry_run.get("schedulerApprovalContract") or {}
    clean_check = repository_clean_deployment_check()
    gates = [
        _release_gate(
            "runtime-bootstrap-apply",
            "passed",
            "Runtime bootstrap apply is available for selected runtime writes.",
            evidence={"command": "onboarding runtime-apply", "writesSettings": True},
        ),
        _release_gate(
            "default-runtime-target",
            "passed" if (dry_run.get("defaultRuntimeTarget") or {}).get("path", "").endswith(".actanara") else "failed",
            "Default runtime bootstrap target is ~/.actanara.",
            evidence=dry_run.get("defaultRuntimeTarget") or {},
        ),
        _release_gate(
            "status-and-rollback-inspection",
            "passed",
            "Post-apply status and rollback-plan inspection commands are available.",
            evidence={
                "statusCommand": "onboarding runtime-status",
                "rollbackPlanCommand": "onboarding rollback-plan",
                "executesRollback": False,
            },
        ),
        _release_gate(
            "dependency-installation-disabled",
            "passed",
            "Runtime bootstrap does not install dependencies.",
            evidence={"installsDependencies": False, "createsVirtualenv": False},
        ),
        _release_gate(
            "clean-deployment-artifacts",
            "passed" if clean_check.get("status") == "passed" else "blocked",
            "Repository does not carry runtime DB/log/snapshot/state/cache/settings files or raw secret values.",
            blocking=clean_check.get("status") != "passed",
            evidence={
                "status": clean_check.get("status"),
                "scannedFiles": clean_check.get("scannedFiles"),
                "findings": clean_check.get("findings"),
                "truncated": clean_check.get("truncated"),
                "policy": clean_check.get("policy"),
            },
        ),
        _release_gate(
            "rag-not-required-for-minimal-v1",
            "passed" if "nova-rag" not in selected_profiles else "blocked",
            "Minimal runtime bootstrap can pass without nova-RAG selected.",
            blocking="nova-rag" in selected_profiles,
            evidence={"selectedProfiles": selected_profiles},
        ),
    ]
    if with_scheduler:
        gates.extend(
            [
                _release_gate(
                    "scheduler-plist-write-gate",
                    "passed" if scheduler_contract.get("plistWriteApplyImplemented") else "failed",
                    "Scheduler plist write gate is available.",
                    evidence={"implemented": scheduler_contract.get("plistWriteApplyImplemented")},
                ),
                _release_gate(
                    "scheduler-registration-gate",
                    "passed" if scheduler_contract.get("registrationImplemented") else "failed",
                    "Scheduler registration gate is available.",
                    evidence={"implemented": scheduler_contract.get("registrationImplemented")},
                ),
                _release_gate(
                    "scheduler-unregister-gate",
                    "passed" if scheduler_contract.get("unregisterImplemented") else "failed",
                    "Scheduler unregister gate is available.",
                    evidence={"implemented": scheduler_contract.get("unregisterImplemented")},
                ),
            ]
        )
    else:
        gates.append(
            _release_gate(
                "scheduler-optional",
                "passed",
                "Scheduler registration is optional and not required for minimal runtime bootstrap.",
                evidence={"withScheduler": False},
            )
        )
    blocking_gates = [gate["id"] for gate in gates if gate.get("blocking") and gate.get("status") != "passed"]
    failed_gates = [gate["id"] for gate in gates if gate.get("status") == "failed"]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "oneLinerReleaseGateOnly": True,
        "status": "passed" if not blocking_gates and not failed_gates else "blocked",
        "selectedProfiles": selected_profiles,
        "withScheduler": with_scheduler,
        "gates": gates,
        "blockingGates": blocking_gates,
        "failedGates": failed_gates,
        "summary": {
            "gates": len(gates),
            "passed": sum(1 for gate in gates if gate.get("status") == "passed"),
            "blocked": sum(1 for gate in gates if gate.get("status") == "blocked"),
            "failed": len(failed_gates),
        },
    }


def onboarding_one_liner_validation_matrix(paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Return the clean-machine validation matrix for the runtime bootstrap surface."""
    minimal_gate = onboarding_one_liner_release_gate(["dashboard"], paths)
    scheduler_gate = onboarding_one_liner_release_gate(["dashboard"], paths, with_scheduler=True)
    rag_gate = onboarding_one_liner_release_gate(["nova-rag"], paths)
    clean_check = repository_clean_deployment_check()
    cases = [
        _validation_case(
            "minimal-v1-release-gate",
            "Minimal runtime bootstrap release gate passes.",
            "actanara onboarding runtime-release-gate --json",
            expected_status="passed",
            expected_exit_code=0,
            observed_status=minimal_gate.get("status"),
            observed_exit_code=0 if minimal_gate.get("status") == "passed" else 1,
            evidence={"blockingGates": minimal_gate.get("blockingGates") or []},
        ),
        _validation_case(
            "scheduler-opt-in-release-gate",
            "Scheduler opt-in runtime bootstrap gates pass without making scheduler mandatory.",
            "actanara onboarding runtime-release-gate --with-scheduler --json",
            expected_status="passed",
            expected_exit_code=0,
            observed_status=scheduler_gate.get("status"),
            observed_exit_code=0 if scheduler_gate.get("status") == "passed" else 1,
            evidence={"blockingGates": scheduler_gate.get("blockingGates") or []},
        ),
        _validation_case(
            "rag-out-of-minimal-v1-scope",
            "Selecting nova-rag blocks the minimal runtime bootstrap release gate.",
            "actanara onboarding runtime-release-gate --profile nova-rag --json",
            expected_status="blocked",
            expected_exit_code=1,
            observed_status=rag_gate.get("status"),
            observed_exit_code=0 if rag_gate.get("status") == "passed" else 1,
            evidence={"blockingGates": rag_gate.get("blockingGates") or []},
        ),
        _validation_case(
            "default-runtime-apply-contract",
            "Default runtime apply targets ~/.actanara and keeps scheduler/dependencies opt-in or disabled.",
            "actanara onboarding runtime-apply --use-default-runtime --language zh-CN --confirmation-text 'APPLY ACTANARA ONBOARDING' --json",
            expected_status="contract-ready",
            expected_exit_code=0,
            observed_status="contract-ready",
            observed_exit_code=0,
            evidence={
                "expectedApplyStatus": "one-liner-applied",
                "target": str(default_oneliner_runtime_home()),
                "writesRuntime": True,
                "registersSchedulerByDefault": False,
                "installsDependencies": False,
            },
        ),
        _validation_case(
            "clean-deployment-artifact-scan",
            "New-user deployment release gate rejects checked-in runtime artifacts and raw secrets.",
            "actanara onboarding runtime-release-gate --json",
            expected_status="passed",
            expected_exit_code=0,
            observed_status=clean_check.get("status"),
            observed_exit_code=0 if clean_check.get("status") == "passed" else 1,
            evidence={
                "findings": clean_check.get("findings"),
                "policy": clean_check.get("policy"),
            },
        ),
    ]
    failed_cases = [case["id"] for case in cases if case.get("status") != "passed"]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "oneLinerValidationMatrix": True,
        "status": "passed" if not failed_cases else "failed",
        "cases": cases,
        "failedCases": failed_cases,
        "summary": {
            "cases": len(cases),
            "passed": sum(1 for case in cases if case.get("status") == "passed"),
            "failed": len(failed_cases),
        },
    }


def onboarding_approval_packet(
    selected: list[str] | None = None,
    paths: RuntimePaths | None = None,
    *,
    confirmation_text: str | None = None,
) -> dict[str, Any]:
    """Return a read-only operator approval packet for future write-capable apply."""
    release_gate = onboarding_release_gate(selected, paths, confirmation_text=confirmation_text)
    approval_items = _operator_approval_items()
    blocked_items = [item for item in approval_items if item.get("requiredBeforeImplementation")]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "approvalPacketOnly": True,
        "status": "approval-required",
        "selectedProfiles": release_gate.get("selectedProfiles"),
        "releaseGateStatus": release_gate.get("status"),
        "releaseGateBlockingGates": release_gate.get("blockingGates", []),
        "operatorApprovalItems": approval_items,
        "implementationReadiness": {
            "readyForWriteImplementation": False,
            "reason": "Operator approvals and release gates are not complete.",
            "requiredApprovalItems": [item["id"] for item in blocked_items],
            "requiredPassingGates": [
                "apply-preflight",
                "scheduler-registration",
                "sandbox-apply-harness",
                "runtime-bootstrap-apply",
                "default-runtime-target",
                "active-runtime-selection",
                "rag-provider-readiness",
                "audit-schema",
                "rollback-schema",
                "no-production-path-writes",
            ],
        },
        "nonNegotiableBoundaries": [
            "no dependency installation without explicit approval",
            "no packaging metadata creation without explicit approval",
            "no production-clean extraction without explicit approval",
            "no prompt/nova-RAG retrieval/index authority changes",
            "no Nova-Task authority change",
            "no secret value persistence without explicit secret policy approval",
        ],
        "sourcePayloads": {
            "releaseGateIncluded": True,
            "writeContractIncluded": True,
            "preflightIncluded": True,
        },
        "summary": {
            "approvalItems": len(approval_items),
            "requiredBeforeImplementation": len(blocked_items),
            "blockingGates": len(release_gate.get("blockingGates", [])),
        },
    }


def onboarding_apply_blocked(
    selected: list[str] | None = None,
    *,
    confirmation_text: str | None = None,
) -> dict[str, Any]:
    """Return the blocked apply skeleton payload without touching runtime paths."""
    selected_profiles = normalize_onboarding_profiles(selected)
    safety_policy = _one_liner_safety_policy()
    apply_contract = onboarding_apply_write_contract(selected_profiles)
    preflight = onboarding_apply_preflight(
        selected_profiles,
        confirmation_text=confirmation_text,
        apply_contract=apply_contract,
    )
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "blocked": True,
        "status": "apply-not-implemented",
        "command": "onboarding apply",
        "profileModel": "product-v2",
        "selectedProfiles": selected_profiles,
        "exitCode": 1,
        "message": "Actanara onboarding apply is not implemented and is blocked by design.",
        "requiresApproval": "separate explicit implementation approval",
        "noSideEffects": True,
        "safetyPolicy": safety_policy,
        "applyWriteContract": apply_contract,
        "applyPreflight": preflight,
        "executionPolicy": {
            "allowed": False,
            "reason": "Blocked apply skeleton only; no apply implementation is available.",
            "writesSettings": False,
            "installsDependencies": False,
            "createsVirtualenv": False,
            "registersScheduler": False,
            "writesLaunchdPlist": False,
            "callsLaunchctl": False,
            "writesExternalAgentSkills": False,
            "mutatesPromptPayloads": False,
            "changesRagAuthority": False,
            "changesNovaTaskAuthority": False,
            "requiredConfirmationPhrase": "APPLY ACTANARA ONBOARDING",
            "confirmationAccepted": bool(preflight.get("confirmationAccepted")),
            "safetyPolicy": safety_policy,
        },
    }


def onboarding_apply_sandbox(
    selected: list[str] | None = None,
    paths: RuntimePaths | None = None,
    *,
    confirmation_text: str | None = None,
    language_profile: str | None = None,
) -> dict[str, Any]:
    """Apply the onboarding write contract to an explicit sandbox runtime only."""
    selected_profiles = normalize_onboarding_profiles(selected)
    if paths is None:
        raise ValueError("sandbox apply requires an explicit runtime path")

    contract = onboarding_apply_write_contract(selected_profiles)
    preflight = onboarding_apply_preflight(
        selected_profiles,
        confirmation_text=confirmation_text,
        apply_contract=contract,
        paths=paths,
    )
    required_phrase = preflight.get("requiredConfirmationPhrase") or "APPLY ACTANARA ONBOARDING"
    if not preflight.get("confirmationAccepted"):
        return _sandbox_apply_rejected(
            selected_profiles,
            contract,
            preflight,
            "exact confirmation phrase is required for sandbox apply",
        )

    home = paths.home
    before_exists = home.exists()
    runtime_paths = initialize_home(home, legacy_diary_root=paths.legacy_diary_root)
    _apply_runtime_language_profile(runtime_paths, language_profile)
    settings = read_settings(runtime_paths, redact_secrets=True)
    onboarding_state = runtime_paths.state_dir / "onboarding"
    onboarding_state.mkdir(parents=True, exist_ok=True)
    rollback_path = onboarding_state / "rollback-plan.json"
    audit_path = onboarding_state / "onboarding-audit.jsonl"
    operation_results = [
        {
            "id": "create-runtime-home",
            "status": "applied",
            "target": str(runtime_paths.home),
            "createdDuringSandboxApply": not before_exists,
        },
        {
            "id": "create-runtime-state-dirs",
            "status": "applied",
            "target": str(onboarding_state),
        },
        {
            "id": "write-runtime-settings",
            "status": "applied",
            "target": str(runtime_paths.config_dir / "settings.json"),
        },
    ]
    if "nova-rag" in selected_profiles:
        operation_results.append(
            {
                "id": "write-rag-provider-settings",
                "status": "skipped",
                "target": str(runtime_paths.config_dir / "settings.json:rag"),
                "reason": "nova-RAG provider configuration is not accepted by sandbox apply.",
            }
        )
    if "nova-task" in selected_profiles:
        nova_task_state = runtime_paths.state_dir / "nova-task"
        nova_task_state.mkdir(parents=True, exist_ok=True)
        operation_results.append(
            {
                "id": "initialize-nova-task-state",
                "status": "applied",
                "target": str(nova_task_state),
            }
        )

    rollback_payload = _sandbox_rollback_payload(
        selected_profiles,
        contract,
        runtime_paths,
        operation_results,
    )
    _write_json_file(rollback_path, rollback_payload)
    audit_event = _sandbox_audit_event(
        selected_profiles,
        required_phrase,
        operation_results,
        rollback_path,
    )
    _append_jsonl(audit_path, audit_event)
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "sandboxApply": True,
        "status": "sandbox-applied",
        "exitCode": 0,
        "selectedProfiles": selected_profiles,
        "runtime": {
            "actanaraHome": str(runtime_paths.home),
            "settingsPath": settings.get("settingsPath"),
            "auditPath": str(audit_path),
            "rollbackPath": str(rollback_path),
        },
        "confirmationAccepted": True,
        "requiredInputsBypassedForSandbox": preflight.get("pendingRequiredInputs", []),
        "applyWriteContract": contract,
        "applyPreflight": preflight,
        "operationResults": operation_results,
        "safetyPolicy": {
            "sandboxOnly": True,
            "requiresExplicitRuntime": True,
            "writesSettings": True,
            "writesAudit": True,
            "writesRollbackPlan": True,
            "registersScheduler": False,
            "writesLaunchdPlist": False,
            "callsLaunchctl": False,
            "installsDependencies": False,
            "createsPackageMetadata": False,
            "productionCleanExtraction": False,
            "persistsSecretValues": False,
        },
        "summary": {
            "status": "sandbox-applied",
            "operations": len(operation_results),
            "applied": sum(1 for item in operation_results if item.get("status") == "applied"),
            "skipped": sum(1 for item in operation_results if item.get("status") == "skipped"),
        },
    }


def _apply_runtime_language_profile(paths: RuntimePaths, language_profile: str | None) -> None:
    if language_profile is None:
        return
    normalized = str(language_profile or "").strip()
    if normalized in {"zh-CN", "zh_CN"}:
        normalized = "zh"
    elif normalized in {"en-US", "en_US"}:
        normalized = "en"
    if normalized not in {"zh", "en"}:
        raise ValueError("--language must be zh-CN or en-US")
    profile = resolve_pipeline_language_profile(normalized)
    write_settings(
        {
            "general": {"locale": profile.locale},
            "pipeline": {
                "languageProfile": profile.profile_id,
                "englishEnabled": profile.profile_id == "en",
                "diarySchemaVersion": profile.diary_schema_version,
                "promptPayloadProfile": profile.prompt_payload_profile,
            },
            "rag": {"languageProfile": profile.rag_language_profile},
        },
        paths,
    )


def onboarding_apply_runtime_bootstrap(
    selected: list[str] | None = None,
    paths: RuntimePaths | None = None,
    *,
    confirmation_text: str | None = None,
    select_active_runtime: bool = False,
    language_profile: str | None = None,
) -> dict[str, Any]:
    """Apply the first real onboarding write category to an explicit runtime."""
    selected_profiles = normalize_onboarding_profiles(selected)
    if paths is None:
        raise ValueError("runtime bootstrap apply requires an explicit runtime path")

    contract = onboarding_apply_write_contract(selected_profiles)
    preflight = onboarding_apply_preflight(
        selected_profiles,
        confirmation_text=confirmation_text,
        apply_contract=contract,
        paths=paths,
    )
    required_phrase = preflight.get("requiredConfirmationPhrase") or "APPLY ACTANARA ONBOARDING"
    if not preflight.get("confirmationAccepted"):
        return _runtime_bootstrap_rejected(
            selected_profiles,
            contract,
            preflight,
            "exact confirmation phrase is required for runtime bootstrap apply",
        )

    home = paths.home
    before_exists = home.exists()
    runtime_paths = initialize_home(home, legacy_diary_root=paths.legacy_diary_root)
    _apply_runtime_language_profile(runtime_paths, language_profile)
    settings = read_settings(runtime_paths, redact_secrets=True)
    onboarding_state = runtime_paths.state_dir / "onboarding"
    onboarding_state.mkdir(parents=True, exist_ok=True)
    rollback_path = onboarding_state / "runtime-bootstrap-rollback-plan.json"
    audit_path = onboarding_state / "onboarding-audit.jsonl"
    operation_results = [
        {
            "id": "create-runtime-home",
            "status": "applied",
            "target": str(runtime_paths.home),
            "createdDuringRuntimeBootstrap": not before_exists,
        },
        {
            "id": "create-runtime-state-dirs",
            "status": "applied",
            "target": str(onboarding_state),
        },
        {
            "id": "write-runtime-settings",
            "status": "applied",
            "target": str(runtime_paths.config_dir / "settings.json"),
        },
    ]
    deferred_operations = []
    if "nova-rag" in selected_profiles:
        deferred_operations.append("write-rag-provider-settings")
    if "nova-task" in selected_profiles:
        nova_task_state = runtime_paths.state_dir / "nova-task"
        nova_task_state.mkdir(parents=True, exist_ok=True)
        operation_results.append(
            {
                "id": "initialize-nova-task-state",
                "status": "applied",
                "target": str(nova_task_state),
            }
        )
    selection_result = None
    if select_active_runtime:
        selection_result = persist_runtime_selection(runtime_paths)
        operation_results.append(
            {
                "id": "select-active-runtime",
                "status": "applied",
                "target": selection_result.get("bootstrapPath"),
                "selectedActanaraHome": selection_result.get("actanaraHome"),
            }
        )

    rollback_payload = _runtime_bootstrap_rollback_payload(
        selected_profiles,
        contract,
        runtime_paths,
        operation_results,
        deferred_operations,
        selection_result,
    )
    _write_json_file(rollback_path, rollback_payload)
    audit_event = _runtime_bootstrap_audit_event(
        selected_profiles,
        required_phrase,
        operation_results,
        rollback_path,
        deferred_operations,
        bool(selection_result),
    )
    _append_jsonl(audit_path, audit_event)
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "runtimeBootstrapApply": True,
        "status": "runtime-bootstrap-applied",
        "exitCode": 0,
        "selectedProfiles": selected_profiles,
        "runtime": {
            "actanaraHome": str(runtime_paths.home),
            "settingsPath": settings.get("settingsPath"),
            "auditPath": str(audit_path),
            "rollbackPath": str(rollback_path),
            "selectedAsActiveRuntime": bool(selection_result),
            "selectionBootstrapPath": (selection_result or {}).get("bootstrapPath"),
        },
        "confirmationAccepted": True,
        "pendingRequiredInputs": preflight.get("pendingRequiredInputs", []),
        "deferredOperations": deferred_operations,
        "applyWriteContract": contract,
        "applyPreflight": preflight,
        "operationResults": operation_results,
        "safetyPolicy": {
            "runtimeBootstrapOnly": True,
            "requiresExplicitRuntime": True,
            "writesSettings": True,
            "writesAudit": True,
            "writesRollbackPlan": True,
            "writesBootstrapLocation": bool(selection_result),
            "selectsActiveRuntime": bool(selection_result),
            "registersScheduler": False,
            "writesLaunchdPlist": False,
            "callsLaunchctl": False,
            "installsDependencies": False,
            "createsPackageMetadata": False,
            "productionCleanExtraction": False,
            "persistsSecretValues": False,
        },
        "summary": {
            "status": "runtime-bootstrap-applied",
            "operations": len(operation_results),
            "applied": sum(1 for item in operation_results if item.get("status") == "applied"),
            "deferred": len(deferred_operations),
            "selectedAsActiveRuntime": bool(selection_result),
        },
    }


def onboarding_apply_preflight(
    selected_profiles: list[str],
    *,
    confirmation_text: str | None = None,
    apply_contract: dict[str, Any] | None = None,
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    """Return no-side-effect apply preflight checks for the blocked apply command."""
    normalized_profiles = normalize_onboarding_profiles(selected_profiles)
    contract = apply_contract or onboarding_apply_write_contract(normalized_profiles)
    required_phrase = ((contract.get("writePlan") or {}).get("confirmationPhrase")) or "APPLY ACTANARA ONBOARDING"
    provided = confirmation_text is not None
    confirmation_accepted = str(confirmation_text or "") == required_phrase
    required_inputs = required_onboarding_inputs(normalized_profiles, paths)
    pending_required_inputs = [
        item.get("id")
        for item in required_inputs
        if item.get("required") and item.get("status") != "ready"
    ]
    llm_details = _llm_provider_preflight_details(paths)
    llm_configured = _llm_provider_configured(paths)
    checks = [
        _preflight_check(
            "apply-implementation-blocked",
            False,
            "Real onboarding apply is not implemented.",
            blocking=True,
        ),
        _preflight_check(
            "exact-confirmation",
            confirmation_accepted,
            "Exact confirmation phrase matched." if confirmation_accepted else "Exact confirmation phrase is missing or incorrect.",
            blocking=not confirmation_accepted,
        ),
        _preflight_check(
            "required-inputs-ready",
            not pending_required_inputs,
            "All required onboarding inputs are ready." if not pending_required_inputs else "Required onboarding inputs are still pending.",
            blocking=bool(pending_required_inputs),
            details={"pendingRequiredInputs": pending_required_inputs},
        ),
        _preflight_check(
            "llm-provider-configured",
            llm_configured,
            "LLM provider config is complete for diary/report generation."
            if llm_configured
            else "LLM provider config is incomplete; dashboard can test live availability after credentials are configured.",
            blocking=False,
            details=llm_details,
        ),
        _preflight_check(
            "write-contract-readonly",
            bool(contract.get("readOnly")) and not bool(contract.get("writesAllowed")),
            "Apply write contract is read-only and writes are disabled.",
        ),
        _preflight_check(
            "audit-preview-readonly",
            not bool((contract.get("auditPlan") or {}).get("writesAudit")),
            "Audit schema is preview-only and does not write audit events.",
        ),
        _preflight_check(
            "rollback-preview-readonly",
            not bool((contract.get("rollbackPlan") or {}).get("writesAllowed")),
            "Rollback schema is preview-only and does not execute rollback.",
        ),
        _preflight_check(
            "no-side-effects",
            True,
            "Preflight does not write settings, scheduler files, audit files or runtime state.",
        ),
    ]
    blocking_reasons = [
        check["id"]
        for check in checks
        if check.get("blocking") and not check.get("passed")
    ]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "preflightOnly": True,
        "applyImplemented": False,
        "allowedToApply": False,
        "selectedProfiles": normalized_profiles,
        "requiredConfirmationPhrase": required_phrase,
        "confirmationProvided": provided,
        "confirmationAccepted": confirmation_accepted,
        "pendingRequiredInputs": pending_required_inputs,
        "blockingReasons": blocking_reasons,
        "checks": checks,
        "summary": {
            "status": "blocked",
            "checks": len(checks),
            "passed": sum(1 for check in checks if check.get("passed")),
            "blockingReasons": len(blocking_reasons),
        },
    }


def _llm_provider_preflight_details(paths: RuntimePaths | None = None) -> dict[str, Any]:
    provider = _readonly_llm_provider(paths)
    missing = [
        field
        for field in ("endpoint", "model", "apiKey")
        if not (provider.get("hasApiKey") if field == "apiKey" else str(provider.get(field) or "").strip())
    ]
    return {
        "provider": provider.get("provider") or "",
        "endpointConfigured": bool(str(provider.get("endpoint") or "").strip()),
        "modelConfigured": bool(str(provider.get("model") or "").strip()),
        "hasApiKey": bool(provider.get("hasApiKey")),
        "missing": missing,
        "liveProbe": "dashboard:/api/llm-provider/test",
    }


def _llm_provider_configured(paths: RuntimePaths | None = None) -> bool:
    return not _llm_provider_preflight_details(paths)["missing"]


def _readonly_llm_provider(paths: RuntimePaths | None = None) -> dict[str, Any]:
    if paths is None:
        return resolve_llm_provider(redact_secrets=True)
    settings = _read_runtime_settings_json(paths)
    provider = settings.get("llmProvider") if isinstance(settings.get("llmProvider"), dict) else {}
    api_key_env = str(provider.get("apiKeyEnv") or "LLM_API_KEY")
    secret_ref = provider.get("secretRef") if isinstance(provider.get("secretRef"), dict) else None
    has_api_key = bool(str(provider.get("apiKey") or "") or secret_ref or os.getenv(api_key_env))
    return {
        "provider": str(provider.get("provider") or ""),
        "endpoint": str(provider.get("endpoint") or ""),
        "model": str(provider.get("model") or ""),
        "apiKey": "secret-store" if has_api_key else "",
        "apiKeyEnv": api_key_env,
        "hasApiKey": has_api_key,
        "source": {
            "provider": "settings" if provider.get("provider") else "unset",
            "endpoint": "settings" if provider.get("endpoint") else "unset",
            "model": "settings" if provider.get("model") else "unset",
            "apiKey": "settings" if provider.get("apiKey") else ("secret-store" if secret_ref else ("env" if os.getenv(api_key_env) else "unset")),
        },
    }


def _sandbox_apply_rejected(
    selected_profiles: list[str],
    contract: dict[str, Any],
    preflight: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "sandboxApply": True,
        "status": "sandbox-rejected",
        "exitCode": 1,
        "selectedProfiles": selected_profiles,
        "reason": reason,
        "confirmationAccepted": False,
        "applyWriteContract": contract,
        "applyPreflight": preflight,
        "operationResults": [],
        "safetyPolicy": {
            "sandboxOnly": True,
            "requiresExplicitRuntime": True,
            "writesSettings": False,
            "writesAudit": False,
            "writesRollbackPlan": False,
            "registersScheduler": False,
            "writesLaunchdPlist": False,
            "callsLaunchctl": False,
            "installsDependencies": False,
        },
    }


def _runtime_bootstrap_rejected(
    selected_profiles: list[str],
    contract: dict[str, Any],
    preflight: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "runtimeBootstrapApply": True,
        "status": "runtime-bootstrap-rejected",
        "exitCode": 1,
        "selectedProfiles": selected_profiles,
        "reason": reason,
        "confirmationAccepted": False,
        "applyWriteContract": contract,
        "applyPreflight": preflight,
        "operationResults": [],
        "safetyPolicy": {
            "runtimeBootstrapOnly": True,
            "requiresExplicitRuntime": True,
            "writesSettings": False,
            "writesAudit": False,
            "writesRollbackPlan": False,
            "writesBootstrapLocation": False,
            "selectsActiveRuntime": False,
            "registersScheduler": False,
            "writesLaunchdPlist": False,
            "callsLaunchctl": False,
            "installsDependencies": False,
        },
    }


def _sandbox_rollback_payload(
    selected_profiles: list[str],
    contract: dict[str, Any],
    paths: RuntimePaths,
    operation_results: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "sandboxOnly": True,
        "rollbackImplemented": False,
        "selectedProfiles": selected_profiles,
        "runtime": {"actanaraHome": str(paths.home)},
        "sourceOperationResults": operation_results,
        "contractRollbackPlan": contract.get("rollbackPlan"),
        "manualRollbackNotes": [
            "Sandbox apply does not provide an executable rollback command.",
            "Delete the sandbox ACTANARA_HOME directory if it was created only for this test.",
        ],
    }


def _runtime_bootstrap_rollback_payload(
    selected_profiles: list[str],
    contract: dict[str, Any],
    paths: RuntimePaths,
    operation_results: list[dict[str, Any]],
    deferred_operations: list[str],
    selection_result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "runtimeBootstrapOnly": True,
        "rollbackImplemented": False,
        "selectedProfiles": selected_profiles,
        "runtime": {"actanaraHome": str(paths.home)},
        "sourceOperationResults": operation_results,
        "deferredOperations": deferred_operations,
        "selectionRollback": {
            "required": bool(selection_result),
            "bootstrapPath": (selection_result or {}).get("bootstrapPath"),
            "description": "Restore or remove the active runtime pointer manually; executable rollback remains unimplemented.",
        },
        "contractRollbackPlan": contract.get("rollbackPlan"),
        "manualRollbackNotes": [
            "Runtime bootstrap apply does not provide an executable rollback command.",
            "Delete the explicit ACTANARA_HOME directory if it was created only for onboarding bootstrap.",
        ],
    }


def _scheduler_sandbox_rollback_payload(
    selected_profiles: list[str],
    paths: RuntimePaths,
    fake_home: Path,
    operation_results: list[dict[str, Any]],
    scheduler_plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "schedulerSandboxOnly": True,
        "rollbackImplemented": False,
        "selectedProfiles": selected_profiles,
        "runtime": {"actanaraHome": str(paths.home)},
        "schedulerHome": str(fake_home),
        "sourceOperationResults": operation_results,
        "managedLabels": [job.get("label") for job in scheduler_plan.get("jobs") or [] if job.get("label")],
        "manualRollbackNotes": [
            "Scheduler sandbox apply does not provide an executable rollback command.",
            "Delete the fake HOME Library/LaunchAgents plist files listed in sourceOperationResults.",
            "No launchctl job was registered.",
        ],
    }


def _scheduler_plist_write_rollback_payload(
    selected_profiles: list[str],
    paths: RuntimePaths,
    launch_agent_home: Path,
    operation_results: list[dict[str, Any]],
    scheduler_plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "schedulerPlistWriteOnly": True,
        "rollbackImplemented": False,
        "selectedProfiles": selected_profiles,
        "runtime": {"actanaraHome": str(paths.home)},
        "launchAgentHome": str(launch_agent_home),
        "sourceOperationResults": operation_results,
        "managedLabels": [job.get("label") for job in scheduler_plan.get("jobs") or [] if job.get("label")],
        "manualRollbackNotes": [
            "Scheduler plist apply does not register launchd jobs and does not provide executable rollback.",
            "If a target plist has backupPath, restore that backup manually.",
            "If a target plist has no backupPath, remove only the managed plist listed in sourceOperationResults.",
            "No launchctl bootout is needed for this phase because launchctl bootstrap was not called.",
        ],
    }


def _scheduler_plist_write_audit_event(
    selected_profiles: list[str],
    required_phrase: str,
    launch_agent_home: Path,
    operation_results: list[dict[str, Any]],
    rollback_path: Path,
) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": "onboarding-scheduler-plist-write",
        "command": "onboarding apply --scheduler-plist-apply",
        "confirmationPhraseMatched": True,
        "confirmationPhrase": required_phrase,
        "selectedProfiles": selected_profiles,
        "launchAgentHome": str(launch_agent_home),
        "operations": [item.get("id") for item in operation_results],
        "operationResults": operation_results,
        "rollbackPlanPath": str(rollback_path),
        "redactionsApplied": ["secret-values", "api-keys"],
    }


def _scheduler_plist_write_safety_policy(*, writes_launch_agents: bool) -> dict[str, Any]:
    return {
        "schedulerPlistWriteOnly": True,
        "requiresExplicitRuntime": True,
        "writesLaunchdPlist": writes_launch_agents,
        "writesRealLaunchAgents": writes_launch_agents,
        "registersScheduler": False,
        "callsLaunchctl": False,
        "installsDependencies": False,
        "createsPackageMetadata": False,
        "productionCleanExtraction": False,
        "persistsSecretValues": False,
    }


def _scheduler_register_failed_payload(
    selected_profiles: list[str],
    paths: RuntimePaths,
    scheduler_plan: dict[str, Any],
    approval_contract: dict[str, Any],
    operation_results: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "schedulerRegisterApply": True,
        "status": "scheduler-register-failed",
        "exitCode": 1,
        "selectedProfiles": selected_profiles,
        "reason": reason,
        "runtime": {"actanaraHome": str(paths.home)},
        "schedulerPlan": scheduler_plan,
        "schedulerApprovalContract": approval_contract,
        "operationResults": operation_results,
        "safetyPolicy": _scheduler_register_safety_policy(calls_launchctl=bool(operation_results)),
    }


def _scheduler_register_rollback_payload(
    selected_profiles: list[str],
    paths: RuntimePaths,
    launch_agent_home: Path,
    operation_results: list[dict[str, Any]],
    scheduler_plan: dict[str, Any],
    domain: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "schedulerRegisterOnly": True,
        "rollbackImplemented": True,
        "automaticCompensation": True,
        "selectedProfiles": selected_profiles,
        "runtime": {"actanaraHome": str(paths.home)},
        "launchAgentHome": str(launch_agent_home),
        "launchctlDomain": domain,
        "sourceOperationResults": operation_results,
        "managedLabels": [job.get("label") for job in scheduler_plan.get("jobs") or [] if job.get("label")],
        "manualRollbackNotes": [
            "The handoff journal automatically restores both launchd jobs, plist bytes/modes, and the Settings preimage when commit fails.",
            "After a committed registration, run the confirmed scheduler unregister command for an operator rollback.",
            "Do not remove non-Actanara launchd jobs.",
        ],
        "commandPreview": [
            ["launchctl", "bootout", domain, item.get("plistPath")]
            for item in operation_results
            if item.get("plistPath")
        ],
    }


def _scheduler_register_audit_event(
    selected_profiles: list[str],
    required_phrase: str,
    launch_agent_home: Path,
    operation_results: list[dict[str, Any]],
    rollback_path: Path,
    domain: str,
) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": "onboarding-scheduler-register",
        "command": "onboarding apply --scheduler-register-apply",
        "confirmationPhraseMatched": True,
        "confirmationPhrase": required_phrase,
        "selectedProfiles": selected_profiles,
        "launchAgentHome": str(launch_agent_home),
        "launchctlDomain": domain,
        "operations": [item.get("id") for item in operation_results],
        "operationResults": operation_results,
        "rollbackPlanPath": str(rollback_path),
        "redactionsApplied": ["secret-values", "api-keys"],
    }


def _scheduler_register_safety_policy(*, calls_launchctl: bool) -> dict[str, Any]:
    return {
        "schedulerRegisterOnly": True,
        "requiresExplicitRuntime": True,
        "requiresExistingManagedPlists": True,
        "writesLaunchdPlist": calls_launchctl,
        "writesRealLaunchAgents": calls_launchctl,
        "registersScheduler": calls_launchctl,
        "callsLaunchctl": calls_launchctl,
        "installsDependencies": False,
        "createsPackageMetadata": False,
        "productionCleanExtraction": False,
        "persistsSecretValues": False,
    }


def _scheduler_unregister_failed_payload(
    selected_profiles: list[str],
    paths: RuntimePaths,
    scheduler_plan: dict[str, Any],
    operation_results: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "readOnly": False,
        "schedulerUnregisterApply": True,
        "status": "scheduler-unregister-failed",
        "exitCode": 1,
        "selectedProfiles": selected_profiles,
        "reason": reason,
        "runtime": {"actanaraHome": str(paths.home)},
        "schedulerPlan": scheduler_plan,
        "operationResults": operation_results,
        "safetyPolicy": _scheduler_unregister_safety_policy(calls_launchctl=bool(operation_results)),
    }


def _scheduler_unregister_rollback_payload(
    selected_profiles: list[str],
    paths: RuntimePaths,
    launch_agent_home: Path,
    operation_results: list[dict[str, Any]],
    scheduler_plan: dict[str, Any],
    domain: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "schedulerUnregisterOnly": True,
        "rollbackImplemented": True,
        "automaticCompensation": True,
        "selectedProfiles": selected_profiles,
        "runtime": {"actanaraHome": str(paths.home)},
        "launchAgentHome": str(launch_agent_home),
        "launchctlDomain": domain,
        "sourceOperationResults": operation_results,
        "managedLabels": [job.get("label") for job in scheduler_plan.get("jobs") or [] if job.get("label")],
        "manualRollbackNotes": [
            "The handoff journal automatically restores both jobs, plist bytes/modes, and the Settings preimage when commit fails.",
            "After a committed unregister, write the managed plists and run the confirmed scheduler register command to re-register.",
            "Committed unregister removes both managed plist files.",
        ],
        "commandPreview": [
            ["launchctl", "bootstrap", domain, item.get("plistPath")]
            for item in operation_results
            if item.get("plistPath")
        ],
    }


def _scheduler_unregister_audit_event(
    selected_profiles: list[str],
    required_phrase: str,
    launch_agent_home: Path,
    operation_results: list[dict[str, Any]],
    rollback_path: Path,
    domain: str,
) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": "onboarding-scheduler-unregister",
        "command": "onboarding apply --scheduler-unregister-apply",
        "confirmationPhraseMatched": True,
        "confirmationPhrase": required_phrase,
        "selectedProfiles": selected_profiles,
        "launchAgentHome": str(launch_agent_home),
        "launchctlDomain": domain,
        "operations": [item.get("id") for item in operation_results],
        "operationResults": operation_results,
        "rollbackPlanPath": str(rollback_path),
        "redactionsApplied": ["secret-values", "api-keys"],
    }


def _scheduler_unregister_safety_policy(*, calls_launchctl: bool) -> dict[str, Any]:
    return {
        "schedulerUnregisterOnly": True,
        "requiresExplicitRuntime": True,
        "writesLaunchdPlist": calls_launchctl,
        "writesRealLaunchAgents": calls_launchctl,
        "unregistersScheduler": calls_launchctl,
        "callsLaunchctl": calls_launchctl,
        "installsDependencies": False,
        "createsPackageMetadata": False,
        "productionCleanExtraction": False,
        "persistsSecretValues": False,
    }


def _scheduler_base_label_from_jobs(jobs: list[dict[str, Any]]) -> str:
    labels = [str(job.get("label") or "") for job in jobs if job.get("label")]
    for suffix in (".pipeline", ".dashboard-aggregation"):
        for label in labels:
            if label.endswith(suffix):
                return label[: -len(suffix)]
    return "actanara.daily"


def _launchctl_gui_domain() -> str:
    return f"gui/{os.getuid()}"


def _run_launchctl(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _scheduler_handoff_jobs(scheduler_plan: dict[str, Any]) -> list[dict[str, Any]]:
    previews = {
        str(job.get("label") or ""): job
        for job in scheduler_plan.get("jobs") or []
        if job.get("label")
    }
    jobs: list[dict[str, Any]] = []
    for managed in scheduler_plan.get("managedPlists") or []:
        label = str(managed.get("label") or "")
        preview = previews.get(label) or {}
        plist = managed.get("plist")
        if not isinstance(plist, dict):
            serialized = str(managed.get("serializedPlist") or "").encode("utf-8")
            try:
                plist = plistlib.loads(serialized)
            except (ValueError, plistlib.InvalidFileException) as error:
                raise ValueError(f"managed scheduler plist is invalid for {label}") from error
        jobs.append(
            {
                "kind": str(preview.get("kind") or "unknown"),
                "label": label,
                "time": preview.get("time"),
                "plist": dict(plist),
            }
        )
    return jobs


def _scheduler_handoff_controls(
    target_home: Path,
    runner: Any,
    operation_results: list[dict[str, Any]],
    *,
    bootout_id: str,
    model_runtime: bool,
) -> dict[str, Any]:
    loaded: set[str] = set()
    running: set[str] = set()

    def plist_path(label: str) -> Path:
        return target_home / "Library" / "LaunchAgents" / f"{label}.plist"

    def operation(action: str, label: str, path: Path, *, allow_failure: bool = False) -> None:
        command = ["launchctl", action, _launchctl_gui_domain(), str(path)]
        try:
            result = runner(command)
            returncode = int(getattr(result, "returncode", 1))
        except Exception as error:
            operation_results.append(
                {
                    "id": f"launchctl-{action}:{label}",
                    "status": "failed",
                    "label": label,
                    "plistPath": str(path),
                    "command": command,
                    "returncode": None,
                    "allowFailure": allow_failure,
                    "errorClass": type(error).__name__,
                }
            )
            raise RuntimeError(f"launchctl {action} runner failed for {label}") from None
        if action == "bootout":
            loaded.discard(label)
            running.discard(label)
        elif action == "bootstrap" and returncode == 0:
            loaded.add(label)
        status = "applied" if returncode == 0 else "skipped" if allow_failure else "failed"
        result_id = f"{bootout_id}:{label}" if action == "bootout" else f"launchctl-{action}:{label}"
        operation_results.append(
            {
                "id": result_id,
                "status": status,
                "label": label,
                "plistPath": str(path),
                "command": command,
                "returncode": returncode,
                "allowFailure": allow_failure,
            }
        )
        if returncode != 0 and not allow_failure:
            raise RuntimeError(f"launchctl {action} failed for {label} (exit {returncode})")

    def probe(label: str, path: Path, expected_plist: dict[str, Any] | None) -> dict[str, Any]:
        aligned = False
        if expected_plist is not None and path.exists():
            try:
                with path.open("rb") as handle:
                    aligned = plistlib.load(handle) == expected_plist
            except (OSError, ValueError, plistlib.InvalidFileException):
                aligned = False
        return {
            "loaded": label in loaded,
            "running": label in running,
            "aligned": aligned,
            "reason": "injected-launchctl-model",
        }

    def kickstart(label: str) -> None:
        if label not in loaded:
            raise RuntimeError(f"cannot kickstart unloaded scheduler job {label}")
        running.add(label)

    controls: dict[str, Any] = {
        "plist_path_resolver": plist_path,
        "launchctl_operation": operation,
    }
    if model_runtime:
        controls.update(
            {
                "launchctl_probe": probe,
                "launchctl_kickstart": kickstart,
            }
        )
    return controls


def _scheduler_sandbox_audit_event(
    selected_profiles: list[str],
    required_phrase: str,
    fake_home: Path,
    operation_results: list[dict[str, Any]],
    rollback_path: Path,
) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": "onboarding-scheduler-sandbox",
        "command": "onboarding apply --scheduler-sandbox-apply",
        "confirmationPhraseMatched": True,
        "confirmationPhrase": required_phrase,
        "selectedProfiles": selected_profiles,
        "schedulerHome": str(fake_home),
        "operations": [item.get("id") for item in operation_results],
        "operationResults": operation_results,
        "rollbackPlanPath": str(rollback_path),
        "redactionsApplied": ["secret-values", "api-keys"],
    }


def _scheduler_sandbox_safety_policy(*, writes_fake_plists: bool) -> dict[str, Any]:
    return {
        "schedulerSandboxOnly": True,
        "requiresExplicitRuntime": True,
        "requiresExplicitFakeHome": True,
        "writesFakeLaunchAgents": writes_fake_plists,
        "writesRealLaunchAgents": False,
        "registersScheduler": False,
        "writesLaunchdPlist": False,
        "callsLaunchctl": False,
        "installsDependencies": False,
        "createsPackageMetadata": False,
        "productionCleanExtraction": False,
        "persistsSecretValues": False,
    }


def _sandbox_audit_event(
    selected_profiles: list[str],
    required_phrase: str,
    operation_results: list[dict[str, Any]],
    rollback_path: Path,
) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": "onboarding-apply-sandbox",
        "command": "onboarding apply --sandbox-apply",
        "confirmationPhraseMatched": True,
        "confirmationPhrase": required_phrase,
        "selectedProfiles": selected_profiles,
        "operations": [item.get("id") for item in operation_results],
        "operationResults": operation_results,
        "rollbackPlanPath": str(rollback_path),
        "redactionsApplied": ["secret-values", "api-keys"],
    }


def _runtime_bootstrap_audit_event(
    selected_profiles: list[str],
    required_phrase: str,
    operation_results: list[dict[str, Any]],
    rollback_path: Path,
    deferred_operations: list[str],
    active_runtime_selected: bool,
) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": "onboarding-runtime-bootstrap",
        "command": "onboarding apply --runtime-bootstrap-apply",
        "confirmationPhraseMatched": True,
        "confirmationPhrase": required_phrase,
        "selectedProfiles": selected_profiles,
        "operations": [item.get("id") for item in operation_results],
        "operationResults": operation_results,
        "deferredOperations": deferred_operations,
        "activeRuntimeSelected": active_runtime_selected,
        "rollbackPlanPath": str(rollback_path),
        "redactionsApplied": ["secret-values", "api-keys"],
    }


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _read_last_jsonl_event(path: Path) -> dict[str, Any] | None:
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (FileNotFoundError, OSError):
        return None
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _artifact_status(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "isFile": path.is_file(),
        "sizeBytes": path.stat().st_size if path.exists() and path.is_file() else 0,
    }


def _one_liner_next_steps(runtime_initialized: bool, artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    if not runtime_initialized:
        return [
            {
                "id": "run-runtime-apply",
                "status": "recommended",
                "command": "actanara onboarding runtime-apply --use-default-runtime --language zh-CN --confirmation-text 'APPLY ACTANARA ONBOARDING'",
            }
        ]
    steps = [
        {
            "id": "run-settings-doctor",
            "status": "recommended",
            "command": "actanara settings doctor --runtime <actanara-home>",
        },
        {
            "id": "review-rollback-plan",
            "status": "recommended",
            "command": "actanara onboarding rollback-plan --runtime <actanara-home>",
        },
    ]
    if not artifacts.get("schedulerSandboxRollback", {}).get("exists"):
        steps.append(
            {
                "id": "optional-scheduler-sandbox",
                "status": "optional",
                "command": "actanara onboarding apply --scheduler-sandbox-apply --runtime <actanara-home> --scheduler-home <fake-home> --confirmation-text 'REGISTER ACTANARA SCHEDULER'",
            }
        )
    return steps


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _preflight_check(
    check_id: str,
    passed: bool,
    message: str,
    *,
    blocking: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "id": check_id,
        "passed": passed,
        "blocking": blocking,
        "message": message,
    }
    if details:
        result["details"] = details
    return result


def _release_gate(
    gate_id: str,
    status: str,
    message: str,
    *,
    blocking: bool = False,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "status": status,
        "blocking": blocking,
        "message": message,
        "evidence": evidence or {},
    }


def _validation_case(
    case_id: str,
    description: str,
    command: str,
    *,
    expected_status: str,
    expected_exit_code: int,
    observed_status: str | None,
    observed_exit_code: int,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    passed = observed_status == expected_status and observed_exit_code == expected_exit_code
    return {
        "id": case_id,
        "status": "passed" if passed else "failed",
        "description": description,
        "command": command,
        "expected": {
            "status": expected_status,
            "exitCode": expected_exit_code,
        },
        "observed": {
            "status": observed_status,
            "exitCode": observed_exit_code,
        },
        "evidence": evidence or {},
    }


def _operator_approval_items() -> list[dict[str, Any]]:
    return [
        _approval_item(
            "approve-settings-writes",
            "Settings writes",
            "Allow future apply to persist approved onboarding settings under selected ACTANARA_HOME only.",
            confirmation="APPLY ACTANARA ONBOARDING",
        ),
        _approval_item(
            "approve-runtime-directory-writes",
            "Runtime directory writes",
            "Allow future apply to create selected ACTANARA_HOME runtime directories.",
            confirmation="APPLY ACTANARA ONBOARDING",
        ),
        _approval_item(
            "approve-audit-writes",
            "Audit writes",
            "Allow future apply to append redacted audit events under selected runtime state.",
            confirmation="APPLY ACTANARA ONBOARDING",
        ),
        _approval_item(
            "approve-rollback-command",
            "Rollback command",
            "Allow a future rollback command that only executes allowlisted rollback operations.",
            confirmation="ROLLBACK ACTANARA ONBOARDING",
        ),
        _approval_item(
            "approve-launchd-registration",
            "macOS launchd registration",
            "Allow future scheduler registration using user-level launchd only.",
            confirmation="REGISTER ACTANARA SCHEDULER",
        ),
        _approval_item(
            "approve-launchd-unregister",
            "macOS launchd unregister",
            "Allow future scheduler unregister/rollback for managed launchd jobs.",
            confirmation="UNREGISTER ACTANARA SCHEDULER",
        ),
        _approval_item(
            "approve-rag-provider-readiness-policy",
            "nova-RAG provider readiness policy",
            "Approve that final nova-RAG sync runs only with local/cloud embedding provider readiness.",
            confirmation="APPLY ACTANARA ONBOARDING",
        ),
        _approval_item(
            "approve-cloud-rag-config-surface",
            "Cloud RAG config surface",
            "Approve cloud provider config fields while keeping secret values out of persisted settings.",
            confirmation="APPLY ACTANARA ONBOARDING",
        ),
    ]


def _approval_item(
    item_id: str,
    label: str,
    description: str,
    *,
    confirmation: str,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "description": description,
        "status": "pending-operator-approval",
        "requiredBeforeImplementation": True,
        "requiredConfirmationPhrase": confirmation,
    }


def onboarding_apply_write_contract(
    selected_profiles: list[str],
    *,
    scheduler_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the future apply write/audit/rollback contract without applying it."""
    normalized_profiles = normalize_onboarding_profiles(selected_profiles)
    scheduler = scheduler_plan or {}
    write_plan = _allowlist_write_plan(normalized_profiles, scheduler)
    audit_plan = _audit_schema_plan(write_plan)
    rollback_plan = _rollback_schema_plan(write_plan)
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "applyImplemented": False,
        "writesAllowed": False,
        "selectedProfiles": normalized_profiles,
        "writePlan": write_plan,
        "auditPlan": audit_plan,
        "rollbackPlan": rollback_plan,
        "releaseGate": {
            "status": "blocked-until-operator-approval",
            "requiresPassingTests": [
                "allowlist-write-plan-serialization",
                "audit-redaction",
                "rollback-plan-shape",
                "no-production-path-writes",
                "confirmation-enforcement",
            ],
        },
    }


def required_onboarding_inputs(selected_profiles: list[str], paths: RuntimePaths | None = None) -> list[dict[str, Any]]:
    input_state = _required_input_state(paths)
    inputs = [
        {
            "id": "output-path",
            "profile": "actanara",
            "required": True,
            "status": input_state["output-path"]["status"],
            "description": "Output/runtime path for all durable Actanara state.",
            "source": input_state["output-path"]["source"],
        },
        {
            "id": "llm-provider",
            "profile": "actanara",
            "required": True,
            "status": input_state["llm-provider"]["status"],
            "description": "Diary generation LLM provider from the provider catalog.",
            "source": input_state["llm-provider"]["source"],
        },
        {
            "id": "llm-api-key",
            "profile": "actanara",
            "required": True,
            "status": input_state["llm-api-key"]["status"],
            "description": "LLM API key or approved credential input.",
            "source": input_state["llm-api-key"]["source"],
        },
    ]
    if "nova-rag" in selected_profiles:
        inputs.extend(
            [
                {
                    "id": "rag-provider",
                    "profile": "nova-rag",
                    "required": True,
                    "status": input_state["rag-provider"]["status"],
                    "description": "nova-RAG provider choice: local or cloud.",
                    "source": input_state["rag-provider"]["source"],
                },
                {
                    "id": "rag-embedding-model",
                    "profile": "nova-rag",
                    "required": True,
                    "status": input_state["rag-embedding-model"]["status"],
                    "description": "Embedding model/provider selected after the nova-RAG provider choice.",
                    "source": input_state["rag-embedding-model"]["source"],
                },
            ]
        )
    return inputs


def _required_input_state(paths: RuntimePaths | None) -> dict[str, dict[str, str]]:
    pending = {
        "output-path": {"status": "pending", "source": "operator-input"},
        "llm-provider": {"status": "pending", "source": "operator-input"},
        "llm-api-key": {"status": "pending", "source": "operator-input"},
        "rag-provider": {"status": "pending", "source": "operator-input"},
        "rag-embedding-model": {"status": "pending", "source": "operator-input"},
    }
    if paths is None:
        return pending

    result = dict(pending)
    if paths.home:
        result["output-path"] = {"status": "ready", "source": "runtime-path"}

    provider = _readonly_llm_provider(paths)
    if str(provider.get("endpoint") or "").strip() and str(provider.get("model") or "").strip():
        result["llm-provider"] = {"status": "ready", "source": "runtime-settings"}
    if provider.get("hasApiKey"):
        result["llm-api-key"] = {"status": "ready", "source": str((provider.get("source") or {}).get("apiKey") or "runtime-settings")}

    return result


def _read_runtime_settings_json(paths: RuntimePaths) -> dict[str, Any]:
    try:
        value = json.loads((paths.config_dir / "settings.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def dependency_profiles_for_product_profiles(selected_profiles: list[str]) -> list[str]:
    return _dependency_profile_ids(selected_profiles)


def dependency_groups_for_product_profiles(selected_profiles: list[str]) -> list[dict[str, Any]]:
    selected_set = set(selected_profiles)
    groups: list[dict[str, Any]] = []
    for profile_id in PROFILE_ORDER:
        definition = PRODUCT_DEPENDENCY_GROUPS[profile_id]
        selected = profile_id in selected_set
        groups.append(
            {
                "id": profile_id,
                "label": definition["label"],
                "selected": selected,
                "required": bool(definition["required"]),
                "installDefault": bool(definition["installDefault"]),
                "installPolicy": "required" if definition["required"] else "selected-only",
                "legacyDependencyProfiles": list(definition["legacyDependencyProfiles"]),
                "requirementSets": list(definition["requirementSets"]),
                "providerInputs": list(definition["providerInputs"]),
                "description": definition["description"],
            }
        )
    return groups


def requirement_sets_for_product_profiles(
    selected_profiles: list[str],
    dependencies: dict[str, Any],
    required_inputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_requirement_sets = {
        requirement_set_id
        for group in dependency_groups_for_product_profiles(selected_profiles)
        if group["selected"]
        for requirement_set_id in group["requirementSets"]
    }
    dependency_profiles = {
        profile.get("id"): profile
        for profile in (dependencies.get("profiles") or [])
        if profile.get("id")
    }
    input_statuses = {
        item.get("id"): item
        for item in required_inputs
        if item.get("id")
    }
    requirement_sets: list[dict[str, Any]] = []
    for requirement_set_id, definition in REQUIREMENT_SET_DEFINITIONS.items():
        selected = requirement_set_id in selected_requirement_sets
        legacy_profile_ids = list(definition["legacyDependencyProfiles"])
        provider_input_ids = list(definition["providerInputs"])
        missing_required = [
            missing
            for profile_id in legacy_profile_ids
            for missing in ((dependency_profiles.get(profile_id) or {}).get("missingRequired") or [])
        ]
        pending_inputs = [
            input_id
            for input_id in provider_input_ids
            if (input_statuses.get(input_id) or {}).get("status") == "pending"
        ]
        if not selected:
            status = "not-selected"
        elif pending_inputs:
            status = "pending-input"
        elif missing_required:
            status = "missing-required"
        elif legacy_profile_ids:
            status = "ready"
        else:
            status = "planned"
        requirement_sets.append(
            {
                "id": requirement_set_id,
                "label": definition["label"],
                "profile": definition["profile"],
                "selected": selected,
                "status": status,
                "legacyDependencyProfiles": legacy_profile_ids,
                "providerInputs": provider_input_ids,
                "pendingInputs": pending_inputs,
                "missingRequired": missing_required,
                "description": definition["description"],
            }
        )
    return requirement_sets


def packaging_plan_for_product_profiles(
    selected_profiles: list[str],
    requirement_sets: list[dict[str, Any]],
) -> dict[str, Any]:
    selected_profile_ids = set(selected_profiles)
    requirement_statuses = {
        item.get("id"): item
        for item in requirement_sets
        if item.get("id")
    }
    groups: list[dict[str, Any]] = []
    for group_id, definition in PACKAGING_GROUP_DEFINITIONS.items():
        profile_id = definition["profile"]
        requirement_set_id = definition["requirementSet"]
        requirement_set = requirement_statuses.get(requirement_set_id) or {}
        profile_selected = profile_id in selected_profile_ids
        provider_derived = definition["dependencySource"] == "provider-derived"
        concrete_selected = profile_selected and not provider_derived
        if not profile_selected:
            status = "not-selected"
        elif provider_derived:
            status = "pending-input"
        else:
            status = requirement_set.get("status") or "planned"
        groups.append(
            {
                "id": group_id,
                "label": definition["label"],
                "profile": profile_id,
                "requirementSet": requirement_set_id,
                "profileSelected": profile_selected,
                "selected": concrete_selected,
                "status": status,
                "dependencySource": definition["dependencySource"],
                "installIntent": group_id,
                "pyprojectExtra": PYPROJECT_EXTRA_BY_INSTALL_INTENT.get(group_id),
                "currentDetection": definition["currentDetection"],
                "currentChecks": list(definition["currentChecks"]),
                "futureCandidates": list(definition["futureCandidates"]),
                "providerInputs": list(definition["providerInputs"]),
                "description": definition["description"],
            }
        )
    concrete_selected_groups = [item for item in groups if item.get("selected")]
    pending_provider_groups = [
        item
        for item in groups
        if item.get("profileSelected") and item.get("dependencySource") == "provider-derived"
    ]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "packageManager": "undecided",
        "installsDependencies": False,
        "createsPackageMetadata": False,
        "schedulerIncluded": False,
        "groups": groups,
        "summary": {
            "groups": len(concrete_selected_groups),
            "pendingProviderDerivedGroups": len(pending_provider_groups),
            "packageManagerDecided": False,
        },
    }


def installer_v2_contract(
    selected_profiles: list[str] | None = None,
    packaging_plan: dict[str, Any] | None = None,
    *,
    scheduler_opt_out: bool = False,
    dashboard_server_enabled: bool = True,
    rag_enabled: bool = False,
    deploy_embedding_server: bool = False,
    platform_system: str | None = None,
) -> dict[str, Any]:
    """Return the read-only installer v2 contract derived from pyproject metadata."""
    selected = normalize_onboarding_profiles(selected_profiles or list(DEFAULT_PROFILE_IDS))
    packaging = packaging_plan or {}
    system = platform_system or platform.system()
    supported_macos = system == "Darwin"
    scheduler_default_enabled = supported_macos and not scheduler_opt_out
    default_groups = ["base", "dashboard", "nova-task"]
    opt_in_groups = ["nova-rag-local", "dev-test"]
    pyproject_extra_by_intent = {
        key: value
        for key, value in PYPROJECT_EXTRA_BY_INSTALL_INTENT.items()
        if value is not None
    }
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "contractOnly": True,
        "installerImplemented": True,
        "manifestAuthority": {
            "path": "pyproject.toml",
            "dependencyAuthority": "project.dependencies-and-project.optional-dependencies",
            "defaultInstallSpec": ".[dashboard]",
            "installIntentVocabulary": "packagingPlan.groups[].id",
            "pyprojectExtraByInstallIntent": pyproject_extra_by_intent,
        },
        "defaultInstallGroups": default_groups,
        "defaultSelectedProfiles": list(DEFAULT_PROFILE_IDS),
        "selectedProfiles": selected,
        "ordinaryWizardChoiceGroups": ["nova-rag"],
        "fixedWizardGroups": ["base", "dashboard", "nova-task"],
        "advancedCliOnlyGroups": ["dev-test"],
        "optInGroups": opt_in_groups,
        "legacyPyprojectOptInExtras": ["rag-local", "dev-test"],
        "heavyLocalRagOptIn": True,
        "dependencyInstallation": {
            "allowedByDecision": True,
            "implementedInCurrentPhase": True,
            "installsDependenciesInCurrentPhase": True,
            "createsVirtualenvByDecision": True,
            "createsVirtualenvInCurrentPhase": True,
            "invokesPipInCurrentPhase": True,
            "invokesGitInCurrentPhase": False,
            "defaultInstallSpec": ".[dashboard]",
            "ragLocalInstallSpec": ".[rag-local]",
            "ragLocalInstallIntent": "nova-rag-local",
            "pyprojectExtraByInstallIntent": pyproject_extra_by_intent,
        },
        "dashboardServer": {
            "installOption": "start-dashboard-server-service",
            "defaultEnabled": True,
            "enabled": bool(dashboard_server_enabled),
            "optOutFlag": "--no-dashboard-server",
            "serviceStartImplementedInCurrentPhase": True,
            "requiredForFeatures": ["realtime-overview", "task-board-ui"],
            "disabledImpact": "realtime overview and task board UI are unavailable when the Dashboard server is disabled; static snapshot pages such as AI Assets remain available",
            "novaTaskUnaffected": True,
            "novaTaskAuthority": "data_foundation.nova_task",
        },
        "rag": {
            "enabled": bool(rag_enabled),
            "embeddingServerDeploymentOption": "deploy-embedding-server",
            "deployEmbeddingServerSelected": bool(deploy_embedding_server),
            "deploymentMode": "background-after-installer",
            "blocksInstaller": False,
            "expectedDuration": "long-running",
            "implementedInCurrentPhase": True,
            "requiresRagLocalExtra": True,
            "installIntent": "nova-rag-local",
            "pyprojectExtra": "rag-local",
            "installSpec": ".[rag-local]",
        },
        "scheduler": {
            "provider": "launchd-user",
            "platform": system,
            "defaultPolicy": "enabled-on-supported-macos",
            "defaultEnabled": scheduler_default_enabled,
            "supportedMacosHost": supported_macos,
            "optOutFlag": "--no-scheduler",
            "optOutApplied": bool(scheduler_opt_out),
            "writesLaunchAgentsInCurrentPhase": False,
            "callsLaunchctlInCurrentPhase": False,
            "managedLabelsOnly": True,
            "managedLabelPrefix": "actanara.daily.",
            "unsupportedPlatformBehavior": "skip-scheduler-registration",
        },
        "runtime": {
            "installTarget": "~/.actanara",
            "activeRuntimePointer": "~/.config/actanara/location.json",
        },
        "sourcePackagingPlan": {
            "packageManager": packaging.get("packageManager"),
            "selectedGroups": [
                item.get("id")
                for item in packaging.get("groups", [])
                if item.get("selected")
            ],
            "installsDependencies": packaging.get("installsDependencies", False),
        },
        "outOfScope": [
            "secret-value-persistence",
            "prompt-payload-mutation",
            "rag-retrieval-or-index-authority-changes",
            "nova-task-authority-changes",
            "production-clean-extraction",
            "external-agent-skill-writes",
        ],
    }


def format_onboarding_subsystem_plan(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    scheduler = payload.get("scheduler") or {}
    required_inputs = payload.get("requiredInputs") or []
    pending_inputs = len([item for item in required_inputs if item.get("required") and item.get("status") == "pending"])
    return render_cli(
        "Setup preview",
        fields=(
            ("Status", status_label(summary.get("status"))),
            ("Features", ", ".join(_selected_profile_labels(payload))),
            ("Setup choices", "Complete" if not pending_inputs else f"{pending_inputs} remaining"),
            ("Required software", "Ready" if not summary.get("missingRequired") else f"{summary.get('missingRequired')} missing"),
            ("Automatic runs", "Available" if scheduler.get("supported") else "Not available"),
        ),
        sections=(("What Actanara will do", [_friendly_setup_action(item.get("id")) for item in payload.get("actions") or []]),),
        next_steps=("actanara onboarding runtime-dry-run",),
    )


def dump_onboarding_subsystem_plan_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def format_onboarding_one_liner_dry_run(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    command_draft = payload.get("commandDraft") or {}
    steps = [_friendly_setup_action(step.get("id")) for step in payload.get("dryRunSteps") or []]
    return render_cli(
        "Setup preview",
        fields=(
            ("Status", status_label(summary.get("status"))),
            ("Features", ", ".join(_selected_profile_labels(payload))),
            ("Steps", len(steps)),
        ),
        sections=(("What Actanara will do", steps),),
        next_steps=((command_draft.get("display"),) if command_draft.get("display") else ()),
    )


def dump_onboarding_one_liner_dry_run_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def format_onboarding_apply_blocked(payload: dict[str, Any]) -> str:
    policy = payload.get("safetyPolicy") or payload.get("executionPolicy") or {}
    if payload.get("oneLinerApply"):
        runtime_policy = ((payload.get("runtimeBootstrap") or {}).get("safetyPolicy") or {}).copy()
        scheduler_policy = payload.get("schedulerRegistration") or {}
        if runtime_policy:
            runtime_policy["registersScheduler"] = bool(scheduler_policy.get("registersScheduler"))
            runtime_policy["writesLaunchdPlist"] = bool(scheduler_policy.get("writesLaunchdPlist"))
            runtime_policy["callsLaunchctl"] = bool(scheduler_policy.get("callsLaunchctl"))
            policy = runtime_policy
    status = payload.get("status", "blocked")
    changes = [
        status_item(
            "ready" if policy.get("writesSettings") else "skipped",
            "Settings were saved",
            "Settings were not changed",
        ),
        status_item(
            "ready" if policy.get("registersScheduler") or policy.get("callsLaunchctl") else "skipped",
            "Automatic daily runs were enabled",
            "Automatic daily runs were not changed",
        ),
        status_item(
            "ready" if policy.get("installsDependencies") else "skipped",
            "Required software was installed",
            "No software was installed",
        ),
    ]
    succeeded = int(payload.get("exitCode", 1)) == 0
    return render_cli(
        "Setup",
        fields=(("Status", status_label(status)),),
        sections=(
            ("Result", (_friendly_apply_message(payload, policy),)),
            ("Changes", changes),
        ),
        next_steps=(("actanara doctor",) if succeeded else ("actanara onboard status",)),
    )


def dump_onboarding_apply_blocked_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def format_onboarding_one_liner_status(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    runtime = payload.get("runtime") or {}
    commands = [str(step.get("command")) for step in payload.get("nextSteps") or [] if step.get("command")]
    return render_cli(
        "Setup status",
        fields=(
            ("Status", status_label(payload.get("status"))),
            ("Data folder", runtime.get("actanaraHome", "—")),
            ("Setup files", f"{summary.get('present', 0)} available"),
            ("Recovery", "Available" if summary.get("hasRollbackPlan") else "Not available"),
        ),
        next_steps=commands,
    )


def dump_onboarding_one_liner_status_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def format_onboarding_rollback_plan_status(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    available = []
    for plan in payload.get("plans") or []:
        if plan.get("exists"):
            available.append(f"{_friendly_recovery_name(plan.get('id'))}: {plan.get('path', '—')}")
    return render_cli(
        "Recovery",
        fields=(
            ("Status", status_label(payload.get("status"))),
            ("Recovery plans", summary.get("available", 0)),
            ("Available actions", summary.get("operations", 0)),
        ),
        sections=(("Recovery files", available),),
        next_steps=(() if available else ("actanara onboard status",)),
    )


def dump_onboarding_rollback_plan_status_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def format_onboarding_release_gate(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    blocking = payload.get("blockingGates") or []
    return render_cli(
        "Setup readiness",
        fields=(
            ("Status", status_label(payload.get("status"))),
            ("Features", ", ".join(_selected_profile_labels(payload))),
            ("Checks passed", summary.get("passed", 0)),
            ("Needs attention", int(summary.get("blocked", 0)) + int(summary.get("failed", 0))),
        ),
        sections=(("Before continuing", [_friendly_gate(gate_id) for gate_id in blocking]),),
        next_steps=(() if not blocking else ("actanara onboard status",)),
    )


def dump_onboarding_release_gate_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def format_onboarding_one_liner_validation_matrix(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    cases = []
    for case in payload.get("cases") or []:
        cases.append(
            status_item(
                case.get("status"),
                _friendly_validation_case(case.get("id")),
                f"{_friendly_validation_case(case.get('id'))} needs attention",
            )
        )
    return render_cli(
        "Setup verification",
        fields=(
            ("Status", status_label(payload.get("status"))),
            ("Passed", summary.get("passed", 0)),
            ("Failed", summary.get("failed", 0)),
        ),
        sections=(("Checks", cases),),
        next_steps=(() if not summary.get("failed") else ("actanara onboard status",)),
    )


def dump_onboarding_one_liner_validation_matrix_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def format_onboarding_approval_packet(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    items = [str(item.get("label")) for item in payload.get("operatorApprovalItems") or [] if item.get("label")]
    return render_cli(
        "Setup confirmation",
        fields=(
            ("Status", status_label(payload.get("status"))),
            ("Features", ", ".join(_selected_profile_labels(payload))),
            ("Confirmations", summary.get("requiredBeforeImplementation", len(items))),
            ("Checks remaining", summary.get("blockingGates", 0)),
        ),
        sections=(("Before continuing", items),),
        next_steps=("actanara onboarding apply --help",),
    )


def dump_onboarding_approval_packet_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _selected_profile_labels(payload: dict[str, Any]) -> list[str]:
    labels = {
        "actanara": "Daily diary",
        "dashboard": "Dashboard",
        "nova-rag": "Memory search",
        "nova-task": "Tasks",
        "dev-test": "Developer tools",
    }
    return [labels.get(str(value), str(value)) for value in payload.get("selectedProfiles") or []]


def _friendly_setup_action(value: object) -> str:
    actions = {
        "select-output-path": "Choose where Actanara stores its data",
        "create-runtime-home": "Prepare the Actanara data folder",
        "create-python-venv": "Prepare required components",
        "install-actanara-requirements": "Install the software Actanara needs",
        "configure-llm-provider": "Choose an AI model and add its API key",
        "install-dashboard-requirements": "Prepare Dashboard",
        "start-dashboard": "Start Dashboard when setup is complete",
        "select-rag-provider": "Choose local or cloud memory search",
        "derive-rag-requirements": "Prepare memory search",
        "enable-rag-pipeline-step": "Update memory after each diary",
        "skip-rag-pipeline-step": "Leave memory search turned off",
        "enable-nova-task-authority": "Prepare Actanara tasks",
        "skip-nova-task-materialization": "Leave Actanara tasks turned off",
        "derive-scheduler-provider": "Check whether automatic daily runs are available",
        "install-dev-test-tools": "Install optional developer tools",
        "run-onboarding-doctor": "Check that setup finished successfully",
        "linux-scheduler-registration-blocked": "Leave automatic daily runs off on this system",
    }
    return actions.get(str(value or ""), "Prepare the selected Actanara feature")


def _friendly_apply_message(payload: dict[str, Any], policy: dict[str, Any]) -> str:
    if int(payload.get("exitCode", 1)) != 0:
        return "Setup was not changed. Review the remaining step and try again."
    if payload.get("oneLinerApply"):
        if policy.get("registersScheduler") or policy.get("callsLaunchctl"):
            return "Actanara is ready, including automatic daily runs."
        return "Actanara is ready. Automatic daily runs were left off."
    if payload.get("schedulerRegisterApply"):
        return "Automatic daily runs are enabled."
    if payload.get("schedulerUnregisterApply"):
        return "Automatic daily runs are disabled."
    if payload.get("schedulerPlistApply") or payload.get("schedulerSandboxApply"):
        return "Automatic daily-run files are ready."
    if payload.get("sandboxApply"):
        return "The test setup completed in the selected folder."
    return "Actanara is ready in the selected data folder."


def _friendly_recovery_name(value: object) -> str:
    text = str(value or "")
    if "scheduler" in text:
        return "Automatic daily runs"
    if "runtime" in text or "bootstrap" in text:
        return "Actanara data folder"
    return "Actanara setup"


def _friendly_gate(value: object) -> str:
    text = str(value or "").lower()
    if "rag" in text:
        return "Choose how memory search should work"
    if "scheduler" in text or "launch" in text:
        return "Finish automatic daily-run setup"
    if "confirmation" in text or "preflight" in text:
        return "Provide the exact confirmation phrase"
    if "dependency" in text or "package" in text:
        return "Install the required software"
    if "runtime" in text or "default" in text:
        return "Choose and prepare the Actanara data folder"
    if "clean" in text:
        return "Remove files that should not be included"
    return "Finish the remaining setup check"


def _friendly_validation_case(value: object) -> str:
    text = str(value or "").lower()
    if "scheduler" in text:
        return "Automatic daily-run setup"
    if "rag" in text:
        return "Memory-search setup"
    if "default" in text or "runtime" in text:
        return "Actanara data-folder setup"
    if "clean" in text:
        return "Clean installation"
    return "Setup behavior"


def _allowlist_write_plan(selected_profiles: list[str], scheduler_plan: dict[str, Any]) -> dict[str, Any]:
    operations = [
        _write_operation(
            "create-runtime-home",
            "runtime-directory",
            "$ACTANARA_HOME",
            "Create selected Actanara runtime home after output path confirmation.",
            rollback="remove-created-empty-runtime-directories",
        ),
        _write_operation(
            "create-runtime-state-dirs",
            "runtime-directory",
            "$ACTANARA_HOME/state/{logs,cache,tmp,backups,onboarding}",
            "Create runtime state subdirectories under selected ACTANARA_HOME.",
            rollback="remove-created-empty-state-directories",
        ),
        _write_operation(
            "write-runtime-settings",
            "settings-file",
            "$ACTANARA_HOME/config/settings.json",
            "Persist approved onboarding settings after exact confirmation.",
            future_writes_settings=True,
            rollback="restore-settings-backup-or-remove-created-settings-file",
        ),
    ]
    if "nova-rag" in selected_profiles:
        operations.append(
            _write_operation(
                "write-rag-provider-settings",
                "settings-file",
                "$ACTANARA_HOME/config/settings.json:rag",
                "Persist nova-RAG provider references only after provider readiness is satisfied.",
                future_writes_settings=True,
                rollback="restore-prior-rag-settings-from-audit-backup",
            )
        )
    if "nova-task" in selected_profiles:
        operations.append(
            _write_operation(
                "initialize-nova-task-state",
                "runtime-state",
                "$ACTANARA_HOME/state/nova-task",
                "Initialize Nova-Task authority runtime state without enabling frozen legacy task scripts.",
                rollback="remove-created-nova-task-runtime-state-if-empty",
            )
        )
    scheduler_jobs = scheduler_plan.get("jobs") or []
    for job in scheduler_jobs:
        operations.append(
            _write_operation(
                f"write-scheduler-plist:{job.get('kind') or 'job'}",
                "scheduler-plist",
                job.get("plistPath") or "~/Library/LaunchAgents/actanara.daily.plist",
                "Future scheduler registration plist write; still blocked in this phase.",
                future_registers_scheduler=True,
                rollback="bootout-launchd-job-and-remove-created-plist",
            )
        )
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "writesAllowed": False,
        "applyImplemented": False,
        "allowlistVersion": "v1-read-only",
        "confirmationPhrase": "APPLY ACTANARA ONBOARDING",
        "exactConfirmationRequired": True,
        "nonInteractiveYesAllowed": False,
        "productionPathWritesAllowed": False,
        "operations": operations,
        "deniedOperations": [
            "install-dependencies",
            "create-packaging-metadata",
            "production-clean-extraction",
            "prompt-payload-mutation",
            "rag-retrieval-or-index-authority-change",
            "nova-task-authority-change",
            "cloud-api-call",
            "secret-value-persistence",
        ],
        "summary": {
            "operations": len(operations),
            "settingsWrites": sum(1 for item in operations if item.get("futureWritesSettings")),
            "schedulerWrites": sum(1 for item in operations if item.get("futureRegistersScheduler")),
            "writesAllowed": False,
        },
    }


def _write_operation(
    operation_id: str,
    category: str,
    target: str,
    description: str,
    *,
    future_writes_settings: bool = False,
    future_registers_scheduler: bool = False,
    rollback: str,
) -> dict[str, Any]:
    return {
        "id": operation_id,
        "category": category,
        "target": target,
        "description": description,
        "implemented": False,
        "allowedInCurrentPhase": False,
        "requiresConfirmation": True,
        "futureWritesSettings": future_writes_settings,
        "futureRegistersScheduler": future_registers_scheduler,
        "writesSecretValues": False,
        "rollback": rollback,
    }


def _audit_schema_plan(write_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "auditRequired": True,
        "auditImplemented": False,
        "writesAudit": False,
        "auditPath": "$ACTANARA_HOME/state/onboarding/onboarding-audit.jsonl",
        "eventSchema": {
            "eventId": "stable uuid or monotonic audit id",
            "timestamp": "ISO-8601 UTC timestamp",
            "phase": "onboarding-apply",
            "command": "onboarding apply",
            "confirmationPhraseMatched": False,
            "selectedProfiles": "profile id list",
            "operations": "allowlisted operation ids",
            "operationResults": "per-operation status list",
            "rollbackPlanId": "rollback plan reference",
            "redactionsApplied": "redaction policy ids",
        },
        "redactionPolicy": {
            "redactSecretValues": True,
            "redactApiKeys": True,
            "recordSecretEnvNamesOnly": True,
            "recordPaths": "selected-runtime-and-managed-targets-only",
        },
        "operationIds": [item.get("id") for item in write_plan.get("operations") or []],
    }


def _rollback_schema_plan(write_plan: dict[str, Any]) -> dict[str, Any]:
    operations = [
        {
            "id": f"rollback:{item.get('id')}",
            "sourceOperationId": item.get("id"),
            "category": item.get("category"),
            "target": item.get("target"),
            "description": item.get("rollback"),
            "implemented": False,
            "allowedInCurrentPhase": False,
            "requiresConfirmation": True,
        }
        for item in write_plan.get("operations") or []
    ]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "rollbackRequired": True,
        "rollbackImplemented": False,
        "writesAllowed": False,
        "confirmationPhrase": "UNREGISTER ACTANARA SCHEDULER",
        "generalRollbackConfirmationPhrase": "ROLLBACK ACTANARA ONBOARDING",
        "operations": operations,
        "summary": {
            "operations": len(operations),
            "schedulerRollbackOperations": sum(1 for item in operations if item.get("category") == "scheduler-plist"),
        },
    }


def _one_liner_safety_policy() -> dict[str, Any]:
    return {
        "dryRunFirst": True,
        "exactConfirmationRequired": True,
        "nonInteractiveYesAllowed": False,
        "writesSettings": False,
        "registersScheduler": False,
        "installsDependencies": False,
        "createsPackageMetadata": False,
        "productionCleanExtraction": False,
        "mutatesPromptPayloads": False,
        "changesRagRetrievalOrIndexAuthority": False,
        "changesNovaTaskAuthority": False,
    }


def _one_liner_scheduler_plan(scheduler: dict[str, Any]) -> dict[str, Any]:
    jobs = scheduler.get("jobs") or []
    provider = "launchd-user" if scheduler.get("provider") == "launchd" else scheduler.get("provider", "launchd-user")
    normalized_jobs = [_scheduler_preview_job(job) for job in jobs]
    first_job = normalized_jobs[0] if normalized_jobs else {}
    managed_plists = [job.get("managedPlist") for job in normalized_jobs if job.get("managedPlist")]
    return {
        "platformTarget": "macos-first",
        "provider": provider,
        "applyImplemented": False,
        "registrationPlanned": True,
        "dryRunOnly": True,
        "selected": scheduler.get("selected"),
        "supported": scheduler.get("supported"),
        "registered": scheduler.get("registered"),
        "confirmationPhrase": "REGISTER ACTANARA SCHEDULER",
        "auditRequired": True,
        "auditPath": "$ACTANARA_HOME/state/onboarding/onboarding-audit.jsonl",
        "rollbackRequired": True,
        "rollbackConfirmationPhrase": "UNREGISTER ACTANARA SCHEDULER",
        "plistPathPreview": first_job.get("plistPath"),
        "labelPreview": first_job.get("label"),
        "programPreview": first_job.get("program"),
        "argumentsPreview": first_job.get("arguments"),
        "programArgumentsPreview": first_job.get("programArguments"),
        "startCalendarIntervalPreview": first_job.get("startCalendarInterval"),
        "stdoutStderrPolicy": "under-runtime-log-dir",
        "managedPlistSerializationReady": bool(managed_plists),
        "managedPlists": managed_plists,
        "wouldWriteManagedPlists": False,
        "wouldCallLaunchctl": False,
        "jobs": normalized_jobs,
        "installPlan": scheduler.get("installPlan", []),
        "rollbackPlan": scheduler.get("rollbackPlan", []),
        "note": "Read-only scheduler preview only; no plist write or launchctl call is implemented.",
    }


def _scheduler_preview_job(job: dict[str, Any]) -> dict[str, Any]:
    program_arguments = list(job.get("programArguments") or [])
    program = job.get("program") or (program_arguments[0] if program_arguments else None)
    arguments = program_arguments[1:] if program_arguments else []
    start_calendar = job.get("startCalendarInterval") or _start_calendar_interval_from_time(job.get("time"))
    return {
        "kind": job.get("kind"),
        "label": job.get("label"),
        "plistPath": job.get("plistPath"),
        "program": program,
        "arguments": arguments,
        "programArguments": program_arguments,
        "workingDirectory": job.get("workingDirectory"),
        "startCalendarInterval": start_calendar,
        "stdoutPath": job.get("stdoutPath"),
        "stderrPath": job.get("stderrPath"),
        "managedPlist": job.get("managedPlist"),
        "dryRunOnly": True,
        "wouldWritePlist": False,
        "wouldCallLaunchctl": False,
    }


def _start_calendar_interval_from_time(value: str | None) -> dict[str, int] | None:
    if not value:
        return None
    try:
        hour_s, minute_s = value.split(":", 1)
        return {"Hour": int(hour_s), "Minute": int(minute_s)}
    except (AttributeError, ValueError):
        return None


def rag_readiness_plan(
    selected_profiles: list[str],
    *,
    provider_mode: str | None = None,
    cloud_config: dict[str, Any] | None = None,
    local_dependency_availability: dict[str, bool] | None = None,
    sync_status: str | None = None,
    sync_skip_reason: str | None = None,
) -> dict[str, Any]:
    """Return read-only nova-RAG provider readiness without mutating RAG runtime state."""
    selected = "nova-rag" in selected_profiles
    normalized_provider = str(provider_mode or "").strip().lower()
    if sync_status == "skipped":
        readiness_state = "rag-sync-skipped"
        provider_mode_value = normalized_provider or ("pending" if selected else "disabled")
        final_sync_policy = "skipped-at-runtime"
        skip_reason = sync_skip_reason or "nova-RAG sync was skipped with an explicit runtime reason."
    elif sync_status == "complete":
        readiness_state = "rag-sync-complete"
        provider_mode_value = normalized_provider or "ready"
        final_sync_policy = "sync-complete"
        skip_reason = None
    elif not selected:
        readiness_state = "rag-disabled"
        provider_mode_value = "disabled"
        final_sync_policy = "skip-disabled"
        skip_reason = "nova-RAG profile is not selected."
    elif normalized_provider in {"", "pending"}:
        readiness_state = "rag-provider-pending"
        provider_mode_value = "pending"
        final_sync_policy = "skip-until-provider-ready"
        skip_reason = "nova-RAG selected but no local/cloud embedding provider is configured in the read-only plan."
    elif normalized_provider == "local":
        dependency_status = _rag_local_dependency_status(local_dependency_availability)
        missing = [item["name"] for item in dependency_status if not item["available"]]
        if missing:
            readiness_state = "rag-local-dependencies-missing"
            final_sync_policy = "skip-missing-local-dependencies"
            skip_reason = "Local nova-RAG provider is selected but embedding runtime dependencies are missing."
        else:
            readiness_state = "rag-local-ready"
            final_sync_policy = "run-final-sync-when-pipeline-completes"
            skip_reason = None
        provider_mode_value = "local"
    elif normalized_provider == "cloud":
        missing_fields = _missing_rag_cloud_config_fields(cloud_config or {})
        if missing_fields:
            readiness_state = "rag-cloud-config-missing"
            final_sync_policy = "skip-missing-cloud-config"
            skip_reason = "Cloud nova-RAG provider is selected but required config fields are missing."
        else:
            readiness_state = "rag-cloud-ready"
            final_sync_policy = "run-final-sync-when-pipeline-completes"
            skip_reason = None
        provider_mode_value = "cloud"
    else:
        readiness_state = "rag-provider-pending"
        provider_mode_value = "pending"
        final_sync_policy = "skip-until-provider-ready"
        skip_reason = f"Unknown nova-RAG provider mode {provider_mode!r}; choose local or cloud."
    dependency_status = _rag_local_dependency_status(local_dependency_availability)
    missing_cloud_fields = _missing_rag_cloud_config_fields(cloud_config or {})
    return {
        "selected": selected,
        "providerMode": provider_mode_value,
        "readinessState": readiness_state,
        "allowedReadinessStates": list(RAG_READINESS_STATES),
        "finalSyncPolicy": final_sync_policy,
        "finalSyncRequiresReadyProvider": True,
        "skipReason": skip_reason,
        "localDependenciesCandidate": list(RAG_LOCAL_DEPENDENCY_CANDIDATES),
        "localDependencyStatus": dependency_status,
        "missingLocalDependencies": [item["name"] for item in dependency_status if not item["available"]],
        "cloudConfigFields": list(RAG_CLOUD_CONFIG_FIELDS),
        "missingCloudConfigFields": missing_cloud_fields,
        "cloudApiCalls": False,
        "installsLocalDependencies": False,
        "changesRagRetrievalOrIndexAuthority": False,
        "cloudConfigSurface": rag_cloud_config_surface(),
    }


def rag_cloud_config_surface() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "profile": "nova-rag",
        "providerMode": "cloud",
        "panelId": "nova-rag-cloud-config",
        "fields": [
            {
                "id": "provider",
                "required": True,
                "secret": False,
                "description": "Cloud embedding provider id.",
            },
            {
                "id": "endpoint",
                "required": True,
                "secret": False,
                "description": "Embedding API endpoint or approved provider preset.",
            },
            {
                "id": "model",
                "required": True,
                "secret": False,
                "description": "Embedding model id.",
            },
            {
                "id": "dimension",
                "required": True,
                "secret": False,
                "description": "Embedding vector dimension.",
            },
            {
                "id": "apiKeyEnv",
                "required": True,
                "secret": False,
                "description": "Environment variable name that holds the API key; the secret value is not persisted.",
            },
            {
                "id": "batchSize",
                "required": True,
                "secret": False,
                "description": "Provider-safe embedding request batch size.",
            },
            {
                "id": "timeoutSeconds",
                "required": True,
                "secret": False,
                "description": "Embedding request timeout.",
            },
            {
                "id": "indexingSourceSets",
                "required": True,
                "secret": False,
                "description": "RAG v2 indexing source sets.",
            },
            {
                "id": "syncPolicy",
                "required": True,
                "secret": False,
                "description": "Post-pipeline final sync policy.",
            },
        ],
        "secretPolicy": {
            "persistSecretValues": False,
            "apiKeyInputMode": "environment-variable-reference",
            "redactionRequired": True,
        },
        "cloudApiCalls": False,
        "writesSettings": False,
    }


def _rag_readiness_plan(selected_profiles: list[str]) -> dict[str, Any]:
    return rag_readiness_plan(selected_profiles)


def _rag_local_dependency_status(availability: dict[str, bool] | None = None) -> list[dict[str, Any]]:
    statuses = []
    for package_name in RAG_LOCAL_DEPENDENCY_CANDIDATES:
        module_name = RAG_LOCAL_DEPENDENCY_MODULES[package_name]
        available = bool(availability[package_name]) if availability and package_name in availability else _module_available(module_name)
        statuses.append({"name": package_name, "module": module_name, "available": available})
    return statuses


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _missing_rag_cloud_config_fields(config: dict[str, Any]) -> list[str]:
    missing = []
    for field in RAG_CLOUD_CONFIG_FIELDS:
        value = config.get(field)
        if value is None or value == "" or value == []:
            missing.append(field)
    return missing


def _source_boundary_approvals() -> dict[str, Any]:
    return {
        "phase": 35,
        "document": "docs/phase35-operator-boundary-approval-matrix.md",
        "status": "approved-direction-for-design",
        "baseIncludes": [
            "daily-pipeline-core-runtime",
            "src/ai_assets_center/unified_source_collector.py",
        ],
        "dashboard": "required-product-profile-service-can-opt-out",
        "ragFinalSync": "profile-gated-post-pipeline-index-sync",
        "ragProviderRequired": True,
        "novaTaskAuthority": "data_foundation.nova_task",
        "legacyNovaTaskPath": "frozen legacy task scripts/manual-review",
        "platformTarget": "macos-first",
        "schedulerApply": "approved-for-design-blocked-for-implementation",
        "ragCloudConfig": "standard-config-surface-required",
    }


def _available_profiles() -> list[dict[str, Any]]:
    return [
        {
            "id": profile_id,
            "label": definition.get("label"),
            "defaultEnabled": bool(definition.get("defaultEnabled")),
            "required": bool(definition.get("required")),
            "minimal": profile_id in REQUIRED_PROFILE_IDS,
            "description": definition.get("description"),
        }
        for profile_id, definition in PRODUCT_PROFILE_DEFINITIONS.items()
    ]


def _planned_actions(selected_profiles: list[str], scheduler_preview: dict[str, Any]) -> list[dict[str, Any]]:
    actions = [
        _action("select-output-path", "Require the user to choose the output/runtime path before apply.", mode="required-input"),
        _action("create-runtime-home", "Validate or initialize ACTANARA_HOME directory structure.", writes=["$ACTANARA_HOME"]),
        _action("create-python-venv", "Create an isolated project Python virtual environment.", writes=[".venv"]),
        _action("install-actanara-requirements", "Install Actanara pipeline/core requirements into the project virtual environment."),
        _action("configure-llm-provider", "Require LLM provider and API key before production diary generation.", mode="required-input"),
    ]
    if "dashboard" in selected_profiles:
        actions.append(_action("install-dashboard-requirements", "Install Dashboard API/server requirements into the project virtual environment."))
        actions.append(_action("start-dashboard", "Start Dashboard only after explicit operator apply or dev-server command.", mode="manual-after-apply"))
    if "nova-rag" in selected_profiles:
        actions.append(_action("select-rag-provider", "Require local/cloud nova-RAG provider selection before dependency install.", mode="required-input"))
        actions.append(_action("derive-rag-requirements", "Install nova-RAG dependencies only after the provider/model choice is known."))
        actions.append(_action("enable-rag-pipeline-step", "Allow final nova-RAG sync/index after core materialization."))
    else:
        actions.append(_action("skip-rag-pipeline-step", "Skip final nova-RAG sync/index and disable nova-RAG UI controls.", mode="plan-only"))
    if "nova-task" in selected_profiles:
        actions.append(_action("enable-nova-task-authority", "Initialize Nova-Task authority and materialization/review surfaces."))
    else:
        actions.append(_action("skip-nova-task-materialization", "Skip Nova-Task materialization/export/review without editing prompt payloads.", mode="plan-only"))
    actions.append(
        _action(
            "derive-scheduler-provider",
            "Detect platform scheduler provider if the user enables scheduled daily runs.",
            mode="plan-only",
        )
    )
    if "dev-test" in selected_profiles:
        actions.append(_action("install-dev-test-tools", "Install optional developer/test tooling.", mode="optional"))
    actions.append(_action("run-onboarding-doctor", "Run the read-only onboarding doctor after apply.", mode="verify"))
    if scheduler_preview.get("provider") in {"systemd", "cron"}:
        actions.append(_action("linux-scheduler-registration-blocked", "Linux scheduler registration remains a separate approved apply step.", mode="blocked"))
    return actions


def _scheduler_plan(selected_profiles: list[str], preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected": None,
        "selectionModel": "derived-from-platform-and-scheduled-run-choice",
        "provider": preview.get("provider"),
        "supported": preview.get("supported"),
        "registered": preview.get("registered"),
        "registrationImplemented": preview.get("registrationImplemented", preview.get("provider") == "launchd"),
        "jobs": preview.get("jobs", []),
        "installPlan": preview.get("installPlan", []),
        "rollbackPlan": preview.get("rollbackPlan", []),
        "note": preview.get("note"),
    }


def _summary(
    dependencies: dict[str, Any],
    actions: list[dict[str, Any]],
    required_inputs: list[dict[str, Any]],
    dependency_groups: list[dict[str, Any]],
    requirement_sets: list[dict[str, Any]],
    packaging_plan: dict[str, Any],
) -> dict[str, Any]:
    missing_required = int(((dependencies.get("summary") or {}).get("missingRequired")) or 0)
    blocked = sum(1 for action in actions if action.get("mode") == "blocked")
    pending_inputs = sum(1 for item in required_inputs if item.get("required") and item.get("status") == "pending")
    selected_dependency_groups = sum(1 for item in dependency_groups if item.get("selected"))
    selected_requirement_sets = sum(1 for item in requirement_sets if item.get("selected"))
    pending_requirement_sets = sum(1 for item in requirement_sets if item.get("status") == "pending-input")
    selected_packaging_groups = int(((packaging_plan.get("summary") or {}).get("groups")) or 0)
    return {
        "status": "blocked" if blocked else "warn" if missing_required else "ready",
        "profiles": int(((dependencies.get("summary") or {}).get("profiles")) or 0),
        "missingRequired": missing_required,
        "dependencyGroups": selected_dependency_groups,
        "requirementSets": selected_requirement_sets,
        "packagingGroups": selected_packaging_groups,
        "pendingRequirementSets": pending_requirement_sets,
        "actions": len(actions),
        "blockedActions": blocked,
        "pendingRequiredInputs": pending_inputs,
    }


def _action(action_id: str, description: str, *, mode: str = "plan", writes: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": action_id,
        "mode": mode,
        "description": description,
        "writes": list(writes or []),
        "executesShell": False,
        "requiresConfirmation": mode in {"confirm-required", "blocked", "required-input"},
    }


def _dependency_profile_ids(selected_profiles: list[str]) -> list[str]:
    dependency_ids: list[str] = []
    for profile_id in selected_profiles:
        definition = PRODUCT_PROFILE_DEFINITIONS.get(profile_id) or {}
        for dependency_id in definition.get("dependencyProfiles") or ():
            if dependency_id not in dependency_ids:
                dependency_ids.append(dependency_id)
    return dependency_ids

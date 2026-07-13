"""Read-only Nova settings status payloads for future CLI/onboarding surfaces."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import pwd
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .dependency_profiles import dependency_profiles_status
from .network import RAG_SERVER_NON_LOOPBACK_ISSUE_CODE, is_loopback_host
from .paths import RuntimePaths, load_paths, validate_home
from .scheduler_preview import preview_system_timer
from .settings_audit import settings_hardcode_audit
from .settings import (
    external_tool_access_summary,
    read_settings,
    resolve_dashboard_settings,
    resolve_general_settings,
    resolve_llm_provider,
    resolve_pipeline_settings,
    resolve_runtime_sources,
    runtime_authority_contract,
)

try:
    from agentic_rag.rag_server_lifecycle import REQUIRED_SERVER_MODULES, read_server_process_state
    from agentic_rag.rag_settings import resolve_rag_settings
except ImportError:  # pragma: no cover - status should degrade without RAG imports
    REQUIRED_SERVER_MODULES = ()  # type: ignore
    read_server_process_state = None  # type: ignore
    resolve_rag_settings = None  # type: ignore


DOCTOR_PROFILES = ("all", "installer", "pipeline", "scheduler", "rag")
RUNTIME_SOURCE_FINAL_FIELDS = {
    "schemaVersion",
    "product",
    "sourceLocator",
    "deployedSourceLocator",
    "releaseLocator",
    "deploymentMode",
    "copiedAt",
    "pyprojectVersion",
    "git",
    "databaseCompatibility",
    "payload",
    "cleanScan",
}


def _normalize_doctor_profile(value: str | None) -> str:
    normalized = str(value or "all").strip().lower()
    if normalized in {"runtime", "settings", "status"}:
        return "all"
    if normalized not in DOCTOR_PROFILES:
        raise ValueError(f"unsupported doctor profile: {value}")
    return normalized


def _filter_doctor_checks(checks: list[dict[str, Any]], profile: str) -> list[dict[str, Any]]:
    if profile == "all":
        return checks
    return [check for check in checks if _doctor_check_profile(str(check.get("id") or "")) in {profile, "all"}]


def _doctor_check_profile(check_id: str) -> str:
    if check_id.startswith("rag-") or check_id.endswith(":rag-server"):
        return "rag"
    if (
        check_id.startswith("launchagent-registration")
        or check_id.startswith("scheduler-")
        or check_id == "runtime-source-launchagent-alignment"
    ):
        return "scheduler"
    if check_id.startswith("external-tool") or check_id.startswith("llm-") or check_id == "settings-hardcode-audit":
        return "pipeline"
    if check_id.startswith("runtime-source") or check_id in {"runtime-home", "settings-file", "database-file"}:
        return "installer"
    return "all"


def nova_settings_status(paths: RuntimePaths | None = None, *, doctor_profile: str = "all") -> dict[str, Any]:
    """Return a read-only status payload suitable for `status` and `doctor` views."""
    paths = paths or load_paths()
    profile = _normalize_doctor_profile(doctor_profile)
    settings = read_settings(paths, persist_defaults=False)
    validation = _validation_payload(validate_home(paths.home))
    external_tools = _jsonable(external_tool_access_summary(paths))
    general = resolve_general_settings(paths)
    sources = resolve_runtime_sources(paths)
    pipeline = resolve_pipeline_settings(paths)
    dashboard = resolve_dashboard_settings(paths)
    provider = resolve_llm_provider(paths, redact_secrets=True)
    llm_secret_visibility = _llm_secret_visibility(provider)
    settings_audit = settings_hardcode_audit(paths=paths)
    resource_profile = _resource_profile(paths, dashboard, settings)
    runtime_source = _runtime_source_provenance(paths, dashboard, settings)
    dependencies = dependency_profiles_status()
    service_registration = _service_registration(settings, runtime_source)
    scheduler_registration = _scheduler_registration_status(
        paths,
        settings,
        probe_runtime=profile in {"all", "scheduler"},
    )
    all_checks = _doctor_checks(
        paths,
        settings,
        validation,
        external_tools,
        provider,
        settings_audit,
        runtime_source,
        service_registration,
        scheduler_registration,
        resource_profile,
    )
    checks = _filter_doctor_checks(all_checks, profile)
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "doctorProfile": profile,
        "runtime": {
            "novaHome": str(paths.home),
            "settingsPath": settings.get("settingsPath"),
            "database": str(paths.db_path),
            "state": str(paths.state_dir),
            "snapshots": str(paths.snapshots_dir),
            "validation": validation,
        },
        "general": general,
        "sources": sources,
        "pipeline": pipeline,
        "dashboard": dashboard,
        "llmProvider": {
            "provider": provider.get("provider"),
            "model": provider.get("model"),
            "endpoint": provider.get("endpoint"),
            "api": provider.get("api"),
            "hasApiKey": provider.get("hasApiKey"),
            "apiKey": provider.get("apiKey"),
            "pipelineGateMode": provider.get("pipelineGateMode"),
            "pipelineGateTokens": provider.get("pipelineGateTokens"),
            "autoPipelineGateTokens": provider.get("autoPipelineGateTokens"),
            "pipelineGateDrift": provider.get("pipelineGateDrift"),
            "secretVisibility": llm_secret_visibility,
        },
        "externalTools": external_tools,
        "settingsAudit": settings_audit,
        "resourceProfile": resource_profile,
        "runtimeSource": runtime_source,
        "serviceRegistration": service_registration,
        "schedulerRegistration": scheduler_registration,
        "dependencyProfiles": dependencies,
        "authority": _jsonable(runtime_authority_contract(paths, persist_defaults=False).get("settingsAuthority", {})),
        "checks": checks,
        "allChecks": all_checks if profile != "all" else checks,
        "summary": _summary(checks),
    }


def format_nova_settings_status(payload: dict[str, Any]) -> str:
    runtime = payload.get("runtime") or {}
    summary = payload.get("summary") or {}
    profile = str(payload.get("doctorProfile") or "all")
    checks = payload.get("checks") or []
    sources = payload.get("sources") or {}
    general = payload.get("general") or {}
    pipeline = payload.get("pipeline") or {}
    dashboard = payload.get("dashboard") or {}
    provider = payload.get("llmProvider") or {}
    resource_profile = payload.get("resourceProfile") or {}
    runtime_source = payload.get("runtimeSource") or {}
    dependency_profiles = payload.get("dependencyProfiles") or {}
    scheduler_registration = payload.get("schedulerRegistration") or {}
    audit_summary = ((payload.get("settingsAudit") or {}).get("summary") or {})
    residual_summary = ((payload.get("settingsAudit") or {}).get("residualRisks") or {})
    lines = [
        (
            f"Nova settings status: {summary.get('status', 'unknown')}"
            if profile == "all"
            else f"Nova doctor ({profile}): {summary.get('status', 'unknown')}"
        ),
        f"General: {general.get('appName', '-')} / {general.get('environment', '-')} / {general.get('timezone', '-')}",
        f"Runtime: {runtime.get('novaHome', '-')}",
        f"Settings: {runtime.get('settingsPath', '-')}",
        f"Database: {runtime.get('database', '-')}",
        f"Pipeline: {pipeline.get('stableCommand', '-')}",
        f"Dashboard: {dashboard.get('url', '-')}",
        (
            "Resource profile: "
            f"dashboard={((resource_profile.get('dashboard') or {}).get('expectedResidentProcesses', '-'))} "
            f"rag={((resource_profile.get('rag') or {}).get('expectedResidentProcesses', '-'))} "
            f"ragStatus={((resource_profile.get('rag') or {}).get('status', '-'))}"
        ),
        (
            "Runtime source: "
            f"{runtime_source.get('status', 'unknown')} "
            f"manifest={'present' if runtime_source.get('manifestExists') else 'missing'} "
            f"locator={((runtime_source.get('sourceLocator') or {}).get('kind') or 'unknown')}"
        ),
        (
            "Dependencies: "
            f"profiles={((dependency_profiles.get('summary') or {}).get('profiles', '-'))} "
            f"missingRequired={((dependency_profiles.get('summary') or {}).get('missingRequired', '-'))}"
        ),
        (
            "Scheduler: "
            f"{scheduler_registration.get('status', 'unknown')} "
            f"desired={scheduler_registration.get('expectedActualState', 'unknown')} "
            f"actual={scheduler_registration.get('actualState', 'unknown')}"
        ),
        f"Sources: {', '.join(f'{key}={value}' for key, value in sorted(sources.items()))}",
        (
            "LLM: "
            f"{provider.get('provider', '-')} / {provider.get('model', '-')} "
            f"apiKey={'set' if provider.get('hasApiKey') else 'missing'} "
            f"gate={provider.get('pipelineGateTokens', '-')}"
        ),
        (
            "Settings audit: "
            f"{audit_summary.get('status', 'unknown')} "
            f"attention={audit_summary.get('attention', 0)}/{audit_summary.get('total', 0)} "
            f"residual={residual_summary.get('attention', 0)}/{residual_summary.get('total', 0)}"
        ),
        f"Checks ({profile}):",
    ]
    for action in runtime_source.get("recommendedActions") or []:
        lines.append(f"Runtime source action: {action.get('label', action.get('id', '-'))}: {action.get('command', '-')}")
    for check in checks:
        lines.append(f"- {check.get('status', 'unknown')}: {check.get('id', '-')}: {check.get('message', '')}")
    return "\n".join(lines) + "\n"


def dump_nova_settings_status_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _doctor_checks(
    paths: RuntimePaths,
    settings: dict,
    validation: dict,
    external_tools: dict,
    provider: dict,
    settings_audit: dict,
    runtime_source: dict,
    service_registration: dict,
    scheduler_registration: dict,
    resource_profile: dict,
) -> list[dict[str, Any]]:
    llm_secret_visibility = _llm_secret_visibility(provider)
    checks = [
        _check(
            "runtime-home",
            bool(validation.get("valid")),
            "error",
            f"runtime home {paths.home} is valid" if validation.get("valid") else f"runtime home {paths.home} is not initialized",
        ),
        _check(
            "settings-file",
            bool(settings.get("settingsPath") and Path(str(settings["settingsPath"])).exists()),
            "error",
            f"settings file {settings.get('settingsPath')} is present",
        ),
        _check(
            "database-file",
            paths.db_path.exists(),
            "warn",
            f"database {paths.db_path} {'exists' if paths.db_path.exists() else 'is missing'}",
        ),
        _check(
            "llm-provider",
            bool(str(provider.get("endpoint") or "").strip() and str(provider.get("model") or "").strip()),
            "error",
            "LLM provider endpoint/model are configured"
            if str(provider.get("endpoint") or "").strip() and str(provider.get("model") or "").strip()
            else "LLM provider endpoint/model are not configured",
        ),
        _check(
            "llm-api-key",
            bool(provider.get("hasApiKey")),
            "warn",
            "LLM API key is configured" if provider.get("hasApiKey") else "LLM API key is not configured",
        ),
        _check(
            "llm-launchd-secret-visibility",
            bool(llm_secret_visibility.get("launchdSafe") or not provider.get("hasApiKey")),
            "warn",
            str(llm_secret_visibility.get("message") or "LLM API key launchd visibility is unknown"),
        ),
        _check(
            "settings-hardcode-audit",
            int((settings_audit.get("summary") or {}).get("attention") or 0) == 0,
            "warn",
            "settings hardcode audit has "
            f"{int((settings_audit.get('summary') or {}).get('attention') or 0)} attention item(s)",
        ),
        _check(
            "runtime-source-provenance",
            runtime_source.get("status") == "fresh",
            "warn",
            str(runtime_source.get("message") or "runtime source provenance is unavailable"),
        ),
        _check(
            "runtime-source-checkout-dirty",
            not bool(runtime_source.get("sourceCheckoutDirty")),
            "warn",
            "source checkout has uncommitted changes not represented by the deployed runtime source"
            if runtime_source.get("sourceCheckoutDirty")
            else "source checkout is clean",
        ),
        _check(
            "runtime-source-launchagent-alignment",
            not bool(runtime_source.get("launchAgentMismatches")),
            "warn",
            str(runtime_source.get("launchAgentMessage") or "runtime source LaunchAgent alignment is unavailable"),
        ),
    ]
    rag_resource = resource_profile.get("rag") if isinstance(resource_profile.get("rag"), dict) else {}
    rag_network = rag_resource.get("networkBoundary") if isinstance(rag_resource.get("networkBoundary"), dict) else {}
    checks.append(
        _check(
            "rag-server-loopback-boundary",
            rag_network.get("status") != "blocked",
            "error",
            (
                "nova-RAG server host is loopback-only"
                if rag_network.get("status") != "blocked"
                else f"Blocked: {RAG_SERVER_NON_LOOPBACK_ISSUE_CODE}"
            ),
        )
    )
    rag_internal = (
        rag_resource.get("internalAuthorization")
        if isinstance(rag_resource.get("internalAuthorization"), dict)
        else {}
    )
    internal_ready = not bool(rag_resource.get("running")) or rag_internal.get("status") == "ready"
    checks.append(
        _check(
            "rag-internal-encode-authorization",
            internal_ready,
            "error",
            (
                "managed token will be created on nova-RAG server start"
                if internal_ready and rag_internal.get("status") != "ready"
                else "nova-RAG internal encode token is ready"
                if internal_ready
                else "Blocked: rag-internal-authorization-unavailable"
            ),
        )
    )
    for service in service_registration.get("services") or []:
        if not service.get("expected"):
            continue
        checks.append(
            _check(
                f"launchagent-registration:{service.get('id')}",
                bool(service.get("registered") and service.get("plistsPresent")),
                "warn",
                str(service.get("message") or f"{service.get('label')} LaunchAgent registration state is unknown"),
            )
        )
    checks.extend(_scheduler_doctor_checks(scheduler_registration))
    external_checks = external_tools.get("checks") if isinstance(external_tools.get("checks"), dict) else {}
    for name, result in sorted(external_checks.items()):
        checks.append(
            _check(
                f"external-tool:{name}",
                bool(result.get("exists") and result.get("readable")),
                "warn",
                f"{name} path={result.get('path')} samples={result.get('sampleCount', 0)}",
            )
        )
    return checks


def _scheduler_registration_status(
    paths: RuntimePaths,
    settings: dict[str, Any],
    *,
    probe_runtime: bool,
) -> dict[str, Any]:
    schedule = settings.get("schedule") if isinstance(settings.get("schedule"), dict) else {}
    timer = schedule.get("systemTimer") if isinstance(schedule.get("systemTimer"), dict) else {}
    try:
        preview = preview_system_timer(paths, probe_runtime=probe_runtime)
    except Exception:
        return {
            "schemaVersion": 1,
            "readOnly": True,
            "provider": str(timer.get("provider") or "launchd"),
            "registrationImplemented": False,
            "schedulerEnabled": bool(schedule.get("enabled")),
            "schedulerMode": str(schedule.get("mode") or "system"),
            "desiredRegistered": bool(schedule.get("enabled") and schedule.get("mode", "system") == "system"),
            "expectedActualState": (
                "present" if schedule.get("enabled") and schedule.get("mode", "system") == "system" else "absent"
            ),
            "actualRegistered": None,
            "actualState": "unknown",
            "configuredRegistered": bool(timer.get("registered")),
            "settingsStale": bool(timer.get("stale")),
            "status": "probe-error",
            "reason": "scheduler-preview-failed",
            "jobs": [],
        }

    desired_registered = bool(preview.get("desiredRegistered"))
    actual_registered = preview.get("actualRegistered")
    runtime_probe = _scheduler_runtime_probe_status(preview.get("runtimeProbe"))
    loaded_jobs = runtime_probe.get("loadedJobs")
    expected_state = str(preview.get("expectedActualState") or ("present" if desired_registered else "absent"))
    jobs = [_scheduler_job_status(job, desired_registered=desired_registered) for job in preview.get("jobs") or []]
    provider_supported = bool(preview.get("supported"))
    registration_implemented = bool(preview.get("registrationImplemented", preview.get("provider") == "launchd"))
    configured_registered = bool(preview.get("configuredRegistered", preview.get("registered")))
    provenance_mismatch = bool(preview.get("provenanceMismatch"))
    desired_actual_mismatch = bool(
        actual_registered is not None
        and (
            bool(actual_registered) != desired_registered
            or (not desired_registered and isinstance(loaded_jobs, int) and loaded_jobs > 0)
        )
    )
    configured_desired_mismatch = configured_registered != desired_registered
    timezone_boundary = (
        preview.get("timezoneBoundary")
        if isinstance(preview.get("timezoneBoundary"), dict)
        else {}
    )
    handoff = _scheduler_handoff_journal_status(paths)

    if handoff.get("status") == "blocked":
        status = "blocked"
    elif timezone_boundary.get("status") == "blocked":
        status = "blocked"
    elif desired_registered and (not provider_supported or not registration_implemented):
        status = "unsupported"
    elif actual_registered is None:
        status = "expected-absent" if not desired_registered and not registration_implemented else "unknown"
    elif desired_actual_mismatch or configured_desired_mismatch or provenance_mismatch:
        status = "mismatch"
    elif any(job.get("status") == "mismatch" for job in jobs):
        status = "mismatch"
    else:
        status = "aligned"

    return {
        "schemaVersion": 1,
        "readOnly": True,
        "provider": str(preview.get("provider") or timer.get("provider") or "launchd"),
        "supported": provider_supported,
        "registrationImplemented": registration_implemented,
        "schedulerEnabled": bool(preview.get("schedulerEnabled", schedule.get("enabled"))),
        "schedulerMode": str(preview.get("schedulerMode") or schedule.get("mode") or "system"),
        "desiredRegistered": desired_registered,
        "expectedActualState": expected_state,
        "expectationReason": str(preview.get("expectationReason") or "unknown"),
        "configuredRegistered": configured_registered,
        "configuredDesiredMismatch": configured_desired_mismatch,
        "settingsStale": bool(preview.get("settingsStale", timer.get("stale"))),
        "timezoneBoundary": timezone_boundary,
        "handoff": handoff,
        "actualRegistered": actual_registered,
        "actualState": (
            "unknown"
            if actual_registered is None
            else "partial"
            if runtime_probe.get("status") == "partial"
            else "present"
            if actual_registered
            else "absent"
        ),
        "registrationSource": str(preview.get("registrationSource") or "settings"),
        "registrationMismatch": bool(preview.get("registrationMismatch")),
        "desiredActualMismatch": desired_actual_mismatch,
        "provenanceMismatch": provenance_mismatch,
        "runtimeProbe": runtime_probe,
        "status": status,
        "jobs": jobs,
    }


def _scheduler_job_status(job: dict[str, Any], *, desired_registered: bool) -> dict[str, Any]:
    runtime = job.get("runtimeStatus") if isinstance(job.get("runtimeStatus"), dict) else {}
    persistent = runtime.get("persistentPlist") if isinstance(runtime.get("persistentPlist"), dict) else {}
    loaded_definition = runtime.get("loadedDefinition") if isinstance(runtime.get("loadedDefinition"), dict) else {}
    actual_loaded = runtime.get("launchctlLoaded")
    issue_codes = sorted({str(item) for item in runtime.get("issueCodes") or [] if str(item)})
    if actual_loaded is None:
        status = "unknown"
    elif desired_registered:
        status = "aligned" if actual_loaded and runtime.get("provenanceAligned") else "mismatch"
    else:
        status = "mismatch" if actual_loaded else "expected-absent"
    return {
        "kind": str(job.get("kind") or "unknown"),
        "label": str(job.get("label") or "unknown"),
        "desiredLoaded": desired_registered,
        "actualLoaded": actual_loaded,
        "actualRunning": runtime.get("launchctlRunning"),
        "runtimeStatus": str(runtime.get("status") or "not-probed"),
        "plistPresent": bool(runtime.get("plistExists")),
        "persistentDefinitionStatus": str(persistent.get("status") or "not-probed"),
        "loadedDefinitionStatus": str(loaded_definition.get("status") or "not-probed"),
        "provenanceAligned": bool(runtime.get("provenanceAligned")),
        "issueCodes": issue_codes,
        "definitionHashes": {
            "expected": runtime.get("expectedDefinitionHash"),
            "persistent": persistent.get("definitionHash"),
            "loaded": loaded_definition.get("definitionHash"),
        },
        "status": status,
    }


def _scheduler_runtime_probe_status(value: Any) -> dict[str, Any]:
    runtime_probe = value if isinstance(value, dict) else {}
    return {
        key: runtime_probe.get(key)
        for key in (
            "enabled",
            "status",
            "expectedJobs",
            "loadedJobs",
            "alignedJobs",
            "mismatchedJobs",
            "plistJobs",
        )
        if key in runtime_probe
    }


def _scheduler_doctor_checks(status: dict[str, Any]) -> list[dict[str, Any]]:
    desired_registered = bool(status.get("desiredRegistered"))
    expected_state = str(status.get("expectedActualState") or "absent")
    provider = str(status.get("provider") or "unknown")
    registration_implemented = bool(status.get("registrationImplemented"))
    supported = bool(status.get("supported"))
    provider_ok = (not desired_registered) or (registration_implemented and supported)
    provider_message = (
        f"scheduler provider {provider} supports the desired system registration"
        if provider_ok and desired_registered
        else f"scheduler provider {provider} is read-only/unimplemented; system jobs are expected absent"
        if provider_ok
        else f"scheduler provider {provider} cannot implement the desired system registration"
    )
    checks = [
        _check("scheduler-provider", provider_ok, "warn", provider_message),
        _check(
            "scheduler-timezone-boundary",
            (status.get("timezoneBoundary") or {}).get("status") != "blocked",
            "error",
            (
                "scheduler timezone matches the macOS system timezone"
                if (status.get("timezoneBoundary") or {}).get("status") != "blocked"
                else f"Blocked: {(status.get('timezoneBoundary') or {}).get('issueCode') or 'scheduler-timezone-boundary'}"
            ),
        ),
        _check(
            "scheduler-handoff-transaction",
            (status.get("handoff") or {}).get("status") != "blocked",
            "error",
            (
                "scheduler handoff journal has no unresolved transaction"
                if (status.get("handoff") or {}).get("status") != "blocked"
                else "Blocked: scheduler-handoff-recovery-required"
            ),
        ),
        _check(
            "scheduler-settings-registration",
            not bool(status.get("configuredDesiredMismatch")) and not bool(status.get("settingsStale")),
            "warn",
            (
                "scheduler settings registration audit matches desired state"
                if not status.get("configuredDesiredMismatch") and not status.get("settingsStale")
                else "scheduler settings registration audit is stale or differs from desired state"
            ),
        ),
    ]

    actual_registered = status.get("actualRegistered")
    actual_known = actual_registered is not None
    actual_ok = (
        not bool(status.get("desiredActualMismatch"))
        if actual_known
        else (not desired_registered and not registration_implemented)
    )
    actual_state = str(status.get("actualState") or "unknown")
    checks.append(
        _check(
            "scheduler-desired-actual",
            actual_ok and not bool(status.get("provenanceMismatch")),
            "warn",
            (
                f"scheduler desired={expected_state} actual={actual_state} provenance=aligned"
                if actual_ok and not status.get("provenanceMismatch")
                else f"scheduler desired={expected_state} actual={actual_state} provenance=mismatch-or-unknown"
            ),
        )
    )

    for job in status.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        actual_loaded = job.get("actualLoaded")
        if actual_loaded is None:
            job_ok = not desired_registered and not registration_implemented
        elif desired_registered:
            job_ok = bool(actual_loaded and job.get("provenanceAligned"))
        else:
            job_ok = not bool(actual_loaded)
        issue_codes = [str(item) for item in job.get("issueCodes") or []]
        actual = "unknown" if actual_loaded is None else "loaded" if actual_loaded else "absent"
        expectation = "loaded/aligned" if desired_registered else "absent"
        details = f" issues={','.join(issue_codes)}" if issue_codes and not job_ok else ""
        checks.append(
            _check(
                f"scheduler-job:{job.get('kind')}",
                job_ok,
                "warn",
                f"scheduler {job.get('kind')} expected={expectation} actual={actual}{details}",
            )
        )
    return checks


def _scheduler_handoff_journal_status(paths: RuntimePaths) -> dict[str, Any]:
    root = paths.state_dir / "scheduler-handoffs"
    if not root.exists():
        return {"status": "ready", "activeCount": 0, "conflictCount": 0, "transactionIds": []}
    active: list[str] = []
    conflicts: list[str] = []
    for transaction_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            journal = json.loads((transaction_dir / "journal.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            conflicts.append(transaction_dir.name)
            continue
        status = str(journal.get("status") or "active") if isinstance(journal, dict) else "invalid"
        if status in {"conflict", "compensation-incomplete", "invalid"}:
            conflicts.append(transaction_dir.name)
        elif status not in {"committed", "compensated"}:
            active.append(transaction_dir.name)
    blocked = bool(active or conflicts)
    return {
        "status": "blocked" if blocked else "ready",
        "activeCount": len(active),
        "conflictCount": len(conflicts),
        "transactionIds": active + conflicts,
    }


def _service_registration(settings: dict[str, Any], runtime_source: dict[str, Any]) -> dict[str, Any]:
    features = settings.get("features") if isinstance(settings.get("features"), dict) else {}
    dashboard = settings.get("dashboard") if isinstance(settings.get("dashboard"), dict) else {}
    dashboard_server = dashboard.get("server") if isinstance(dashboard.get("server"), dict) else {}
    rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
    rag_server = rag.get("server") if isinstance(rag.get("server"), dict) else {}
    launch_agents = runtime_source.get("launchAgents") if isinstance(runtime_source.get("launchAgents"), list) else []
    by_label = {str(item.get("label")): item for item in launch_agents if isinstance(item, dict)}

    dashboard_labels = [
        str(dashboard.get("serviceLabel") or "com.open-nova.dashboard"),
        str(dashboard.get("watchdogLabel") or "com.open-nova.dashboard.watchdog"),
    ]
    rag_label = str(((rag_server.get("launchAgent") or {}).get("label")) or "com.open-nova.rag-server")
    return {
        "schemaVersion": 1,
        "services": [
            _launch_service_status(
                service_id="dashboard",
                label="Dashboard server",
                expected=bool(features.get("dashboard", True) and dashboard_server.get("enabled", True)),
                audit=dashboard.get("launchAgent") if isinstance(dashboard.get("launchAgent"), dict) else {},
                labels=dashboard_labels,
                plist_status_by_label=by_label,
            ),
            _launch_service_status(
                service_id="rag-server",
                label="nova-RAG server",
                expected=bool(rag.get("enabled") and rag_server.get("enabled")),
                audit=rag_server.get("launchAgent") if isinstance(rag_server.get("launchAgent"), dict) else {},
                labels=[rag_label],
                plist_status_by_label=by_label,
            ),
        ],
    }


def _launch_service_status(
    *,
    service_id: str,
    label: str,
    expected: bool,
    audit: dict[str, Any],
    labels: list[str],
    plist_status_by_label: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    audit_jobs = audit.get("jobs") if isinstance(audit.get("jobs"), list) else []
    audit_paths = [str(job.get("plistPath") or "") for job in audit_jobs if isinstance(job, dict)]
    plist_entries = [plist_status_by_label.get(item) for item in labels]
    known_plists_present = bool(plist_entries) and all(bool(item and item.get("exists")) for item in plist_entries)
    audited_plists_present = bool(audit_paths) and all(Path(item).expanduser().exists() for item in audit_paths)
    plists_present = known_plists_present or audited_plists_present
    registered = bool(audit.get("registered"))
    if not expected:
        status = "not-expected"
        message = f"{label} LaunchAgent registration is not expected for the selected installer settings"
    elif registered and plists_present:
        status = "registered"
        message = f"{label} LaunchAgent registration is recorded and managed plist files are present"
    elif registered:
        status = "missing-plist"
        message = f"{label} LaunchAgent registration is recorded, but one or more managed plist files are missing"
    elif plists_present:
        status = "plist-present-audit-missing"
        message = (
            f"{label} LaunchAgent plist files are present, but settings registration audit is missing; "
            "reinstall or update service registration to record ownership"
        )
    else:
        status = "not-registered"
        message = f"{label} LaunchAgent registration is not recorded; reinstall or rerun installer service registration"
    return {
        "id": service_id,
        "label": label,
        "expected": expected,
        "status": status,
        "registered": registered,
        "registeredAt": audit.get("registeredAt"),
        "registrationManagedBy": audit.get("registrationManagedBy"),
        "lastAction": audit.get("lastAction"),
        "backupDir": audit.get("backupDir"),
        "labels": labels,
        "auditPlistPaths": audit_paths,
        "plistsPresent": plists_present,
        "message": message,
    }


def _llm_secret_visibility(provider: dict[str, Any]) -> dict[str, Any]:
    source = (provider.get("source") or {}).get("apiKey") or "missing"
    secret_ref = provider.get("secretRef") if isinstance(provider.get("secretRef"), dict) else {}
    backend = str(secret_ref.get("backend") or "").strip()
    if not provider.get("hasApiKey"):
        return {
            "source": source,
            "backend": backend or None,
            "launchdSafe": False,
            "status": "missing",
            "message": "LLM API key is not configured",
        }
    if source == "secret-store":
        launchd_safe = backend not in {"memory", "process-env", ""}
        status = "launchd-safe" if launchd_safe else "launchd-unsafe"
        return {
            "source": source,
            "backend": backend or None,
            "launchdSafe": launchd_safe,
            "status": status,
            "message": (
                f"LLM API key secret backend '{backend}' is visible to launchd processes"
                if launchd_safe
                else f"LLM API key secret backend '{backend or 'unknown'}' may not be visible to launchd processes"
            ),
        }
    if source == "env":
        api_key_env = str(provider.get("apiKeyEnv") or "LLM_API_KEY")
        return {
            "source": source,
            "backend": None,
            "apiKeyEnv": api_key_env,
            "launchdSafe": False,
            "status": "launchd-unsafe",
            "message": f"LLM API key comes from current process env {api_key_env}; launchd jobs need an explicit env or secret-store backend",
        }
    return {
        "source": source,
        "backend": backend or None,
        "launchdSafe": source == "settings",
        "status": "launchd-safe" if source == "settings" else "launchd-unknown",
        "message": (
            "LLM API key is persisted in settings and exported to child processes"
            if source == "settings"
            else "LLM API key launchd visibility is unknown"
        ),
    }


def _login_home_path() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _valid_locator_components(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str)
        and bool(item)
        and item not in {".", ".."}
        and "/" not in item
        and "\\" not in item
        and "\0" not in item
        for item in value
    )


def _valid_v2_runtime_source_manifest(manifest: dict[str, Any]) -> bool:
    if (
        type(manifest.get("schemaVersion")) is not int
        or manifest.get("schemaVersion") != 2
        or set(manifest) != RUNTIME_SOURCE_FINAL_FIELDS
        or manifest.get("product") != "open-nova"
        or manifest.get("deploymentMode") != "release-symlink"
        or _safe_manifest_datetime(manifest.get("copiedAt")) is None
        or (
            manifest.get("pyprojectVersion") is not None
            and _safe_manifest_version(manifest.get("pyprojectVersion")) is None
        )
    ):
        return False
    source_locator = manifest.get("sourceLocator")
    if not isinstance(source_locator, dict):
        return False
    if source_locator.get("kind") == "login-home-relative":
        if set(source_locator) != {"kind", "pathComponents"} or not _valid_locator_components(
            source_locator.get("pathComponents")
        ):
            return False
    elif source_locator.get("kind") == "unavailable":
        if set(source_locator) != {"kind", "issue"} or source_locator.get("issue") not in {
            "outside-login-home",
            "invalid-relative-components",
        }:
            return False
    else:
        return False
    deployed = manifest.get("deployedSourceLocator")
    release = manifest.get("releaseLocator")
    if (
        not isinstance(deployed, dict)
        or set(deployed) != {"kind", "pathComponents"}
        or deployed.get("kind") != "runtime-relative"
        or deployed.get("pathComponents") != ["app", "source"]
        or not isinstance(release, dict)
        or set(release) != {"kind", "pathComponents"}
        or release.get("kind") != "runtime-relative"
        or not _valid_locator_components(release.get("pathComponents"))
        or len(release["pathComponents"]) != 3
        or release["pathComponents"][:2] != ["app", "releases"]
    ):
        return False
    release_id = release["pathComponents"][2]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", release_id):
        return False
    git = manifest.get("git")
    if not isinstance(git, dict) or set(git) != {"available", "commit", "branch", "remote", "dirty"}:
        return False
    if type(git.get("available")) is not bool:
        return False
    dirty = git.get("dirty")
    if dirty is not None and type(dirty) is not bool:
        return False
    commit = git.get("commit")
    if commit is not None and (
        not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,64}", commit)
    ):
        return False
    branch = git.get("branch")
    if branch is not None and (
        not isinstance(branch, str)
        or not branch
        or branch.startswith(("/", "~/", "file:"))
        or "/Users/" in branch
        or any(character in branch for character in "\0\r\n")
    ):
        return False
    remote = git.get("remote")
    if remote is not None:
        if not isinstance(remote, str):
            return False
        try:
            parsed = urlsplit(remote)
        except (TypeError, ValueError):
            return False
        if (
            parsed.scheme not in {"https", "ssh"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
        ):
            return False
    compatibility = manifest.get("databaseCompatibility")
    compatibility_fields = {
        "schemaVersion",
        "policy",
        "preCommitWriterContract",
        "minimumReadableSchema",
        "maximumReadableSchema",
        "migrationSetSha256",
        "migrations",
    }
    if not isinstance(compatibility, dict) or set(compatibility) != compatibility_fields:
        return False
    migrations = compatibility.get("migrations")
    if (
        type(compatibility.get("schemaVersion")) is not int
        or compatibility.get("schemaVersion") != 1
        or compatibility.get("policy") != "rollback-compatible-additive-only"
        or compatibility.get("preCommitWriterContract") != "prior-reader-compatible-v1"
        or compatibility.get("minimumReadableSchema") != "unversioned"
        or not isinstance(migrations, list)
        or not migrations
        or not re.fullmatch(r"[0-9a-f]{64}", str(compatibility.get("migrationSetSha256") or ""))
    ):
        return False
    migration_versions: list[str] = []
    for record in migrations:
        if not isinstance(record, dict) or set(record) != {"version", "sha256", "rollbackClass"}:
            return False
        version = record.get("version")
        if (
            not isinstance(version, str)
            or not re.fullmatch(r"[0-9]{4}_[a-z0-9_]+", version)
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256") or ""))
            or record.get("rollbackClass") not in {"rollback-compatible-additive", "breaking"}
        ):
            return False
        migration_versions.append(version)
    if (
        len(set(migration_versions)) != len(migration_versions)
        or compatibility.get("maximumReadableSchema") != migration_versions[-1]
    ):
        return False
    clean = manifest.get("cleanScan")
    payload = manifest.get("payload")
    if (
        not isinstance(clean, dict)
        or set(clean) != {"status", "scanner", "scannedFiles", "findingCount"}
        or not isinstance(payload, dict)
        or set(payload) != {"fileCount", "files", "sha256"}
    ):
        return False
    records = payload.get("files")
    if (
        clean.get("status") != "passed"
        or clean.get("scanner") != "data_foundation.release_clean.repository_clean_deployment_check"
        or type(clean.get("scannedFiles")) is not int
        or clean.get("scannedFiles") < 0
        or clean.get("findingCount") != 0
        or not isinstance(records, list)
        or not records
        or type(payload.get("fileCount")) is not int
        or payload.get("fileCount") != len(records)
        or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("sha256") or ""))
    ):
        return False
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
            return False
        relative_text = record.get("path")
        if not isinstance(relative_text, str) or not relative_text or "\0" in relative_text:
            return False
        relative = Path(relative_text)
        if (
            relative.is_absolute()
            or relative_text.startswith(("~/", "file:"))
            or ".." in relative.parts
            or relative.as_posix() in seen
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256") or ""))
            or type(record.get("size")) is not int
            or record.get("size") < 0
        ):
            return False
        seen.add(relative.as_posix())
    return True


def _runtime_source_locator(manifest: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
    schema_version = manifest.get("schemaVersion")
    if schema_version == 1:
        raw_source = manifest.get("sourceRoot")
        source_root = Path(str(raw_source or "")).expanduser()
        if not raw_source or not source_root.is_absolute():
            return None, {"kind": "legacy-absolute", "available": False, "issue": "invalid-legacy-locator"}
        return source_root, {"kind": "legacy-absolute", "available": True}
    if schema_version != 2:
        return None, {"kind": "unsupported", "available": False, "issue": "unsupported-manifest-schema"}
    if not _valid_v2_runtime_source_manifest(manifest):
        return None, {"kind": "invalid", "available": False, "issue": "invalid-v2-manifest"}
    locator = manifest.get("sourceLocator")
    if not isinstance(locator, dict):
        return None, {"kind": "invalid", "available": False, "issue": "missing-source-locator"}
    kind = str(locator.get("kind") or "")
    if kind == "unavailable":
        issue = str(locator.get("issue") or "")
        if issue not in {"outside-login-home", "invalid-relative-components"}:
            issue = "source-locator-unavailable"
        return None, {"kind": kind, "available": False, "issue": issue}
    components = locator.get("pathComponents")
    if kind != "login-home-relative" or not isinstance(components, list) or not components:
        return None, {"kind": kind or "invalid", "available": False, "issue": "invalid-source-locator"}
    if any(
        not isinstance(item, str)
        or not item
        or item in {".", ".."}
        or "/" in item
        or "\\" in item
        for item in components
    ):
        return None, {"kind": kind, "available": False, "issue": "invalid-relative-components"}
    try:
        login_home = _login_home_path().resolve()
        source_root = login_home.joinpath(*components).resolve()
        source_root.relative_to(login_home)
    except (KeyError, OSError, RuntimeError, ValueError):
        return None, {"kind": kind, "available": False, "issue": "source-locator-unresolvable"}
    return source_root, {"kind": kind, "available": True}


def _safe_manifest_datetime(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def _safe_manifest_version(value: Any) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+!-]{0,127}", value):
        return None
    return value


def _safe_manifest_commit(value: Any) -> str | None:
    if not isinstance(value, str) or not 4 <= len(value) <= 64:
        return None
    return value if all(character in "0123456789abcdef" for character in value) else None


def _public_runtime_source_manifest(manifest: dict[str, Any], locator: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {
        "schemaVersion": manifest.get("schemaVersion") if type(manifest.get("schemaVersion")) is int else None,
        "product": manifest.get("product") if manifest.get("schemaVersion") == 2 else "open-nova",
        "deploymentMode": (
            "release-symlink" if manifest.get("deploymentMode") == "release-symlink" else None
        ),
        "copiedAt": _safe_manifest_datetime(manifest.get("copiedAt")),
        "pyprojectVersion": _safe_manifest_version(manifest.get("pyprojectVersion")),
        "sourceLocator": dict(locator),
    }
    for key in ("deployedSourceLocator", "releaseLocator"):
        runtime_locator = manifest.get(key)
        components = runtime_locator.get("pathComponents") if isinstance(runtime_locator, dict) else None
        valid = (
            isinstance(runtime_locator, dict)
            and runtime_locator.get("kind") == "runtime-relative"
            and isinstance(components, list)
            and bool(components)
            and all(
                isinstance(item, str)
                and item
                and item not in {".", ".."}
                and "/" not in item
                and "\\" not in item
                for item in components
            )
        )
        public[key] = {"kind": "runtime-relative" if valid else "invalid", "available": valid}
    git = manifest.get("git")
    if isinstance(git, dict):
        public["git"] = {
            "available": git.get("available") if type(git.get("available")) is bool else None,
            "commit": _safe_manifest_commit(git.get("commit")),
            "dirty": git.get("dirty") if git.get("dirty") is None or type(git.get("dirty")) is bool else None,
            "remoteAvailable": bool(git.get("remote")),
        }
    compatibility = manifest.get("databaseCompatibility")
    if manifest.get("schemaVersion") == 2 and isinstance(compatibility, dict):
        public["databaseCompatibility"] = {
            "schemaVersion": compatibility.get("schemaVersion"),
            "policy": compatibility.get("policy"),
            "preCommitWriterContract": compatibility.get("preCommitWriterContract"),
            "minimumReadableSchema": compatibility.get("minimumReadableSchema"),
            "maximumReadableSchema": compatibility.get("maximumReadableSchema"),
            "migrationSetSha256": compatibility.get("migrationSetSha256"),
            "migrationCount": len(compatibility.get("migrations") or []),
        }
    payload = manifest.get("payload")
    if manifest.get("schemaVersion") == 2 and isinstance(payload, dict):
        public["payload"] = {
            "fileCount": payload.get("fileCount"),
            "sha256": payload.get("sha256"),
        }
    clean = manifest.get("cleanScan")
    if manifest.get("schemaVersion") == 2 and isinstance(clean, dict):
        public["cleanScan"] = {
            "status": clean.get("status"),
            "scanner": clean.get("scanner"),
            "scannedFiles": clean.get("scannedFiles"),
            "findingCount": clean.get("findingCount"),
        }
    return public


def _runtime_source_provenance(paths: RuntimePaths, dashboard: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    project_root = Path(str(dashboard.get("projectRoot") or "")).expanduser()
    manifest_path = project_root / ".open-nova-runtime-source.json"
    payload: dict[str, Any] = {
        "status": "missing",
        "manifestExists": manifest_path.exists(),
        "freshness": "unknown",
        "sourceLocator": {"kind": "missing", "available": False, "issue": "manifest-missing"},
        "stale": None,
        "message": "runtime source manifest is missing",
    }
    if not manifest_path.exists():
        return {**payload, **_launch_agent_source_alignment(dashboard, settings, project_root)}
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except (json.JSONDecodeError, OSError):
        return {
            **payload,
            "status": "invalid",
            "manifestExists": True,
            "sourceLocator": {"kind": "invalid", "available": False, "issue": "manifest-unreadable"},
            "message": "runtime source manifest is invalid",
            **_launch_agent_source_alignment(dashboard, settings, project_root),
        }
    if not isinstance(manifest, dict):
        return {
            **payload,
            "status": "invalid",
            "manifestExists": True,
            "sourceLocator": {"kind": "invalid", "available": False, "issue": "manifest-not-object"},
            "message": "runtime source manifest is invalid",
            **_launch_agent_source_alignment(dashboard, settings, project_root),
        }
    source_root, locator = _runtime_source_locator(manifest)
    if manifest.get("schemaVersion") == 2 and not _valid_v2_runtime_source_manifest(manifest):
        return {
            **payload,
            "status": "invalid",
            "manifestExists": True,
            "manifest": {
                "schemaVersion": 2,
                "sourceLocator": dict(locator),
                "valid": False,
            },
            "manifestSchemaVersion": 2,
            "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "sourceLocator": locator,
            "stale": None,
            "freshness": "unknown",
            "message": "runtime source manifest v2 failed exact schema validation",
            **_launch_agent_source_alignment(dashboard, settings, project_root),
        }
    payload.update(
        {
            "manifest": _public_runtime_source_manifest(manifest, locator),
            "manifestSchemaVersion": (
                manifest.get("schemaVersion") if type(manifest.get("schemaVersion")) is int else None
            ),
            "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "sourceLocator": locator,
            "copiedAt": _safe_manifest_datetime(manifest.get("copiedAt")),
            "sourceVersion": _safe_manifest_version(manifest.get("pyprojectVersion")),
        }
    )
    copied_commit = _safe_manifest_commit((manifest.get("git") or {}).get("commit")) or ""
    if source_root is None or not source_root.exists() or not copied_commit:
        return {
            **payload,
            "status": "present",
            "stale": None,
            "freshness": "unknown",
            "message": "runtime source manifest is present; source freshness is unknown",
            **_launch_agent_source_alignment(dashboard, settings, project_root),
        }
    try:
        current_commit = _git_value(source_root, "rev-parse", "HEAD")
        current_dirty = bool(_git_value(source_root, "status", "--porcelain"))
    except Exception:
        return {
            **payload,
            "status": "present",
            "stale": None,
            "freshness": "unknown",
            "message": "runtime source manifest is present; git freshness check is unavailable",
            **_launch_agent_source_alignment(dashboard, settings, project_root),
        }
    commit_matches = current_commit == copied_commit
    stale_reasons: list[str] = []
    if not commit_matches:
        stale_reasons.append("source-commit-mismatch")
    stale = bool(stale_reasons)
    if current_dirty and not stale:
        message = (
            "runtime source matches the source checkout HEAD, but the source checkout has uncommitted changes "
            "that may not be included in the deployed runtime source"
        )
    elif stale:
        message = "runtime source is stale; run the recommended source-only sync before validating live services"
    else:
        message = "runtime source manifest matches the source checkout"
    return {
        **payload,
        "status": "stale" if stale else "fresh",
        "freshness": "stale" if stale else "fresh",
        "stale": stale,
        "sourceCheckoutDirty": current_dirty,
        "staleReasons": stale_reasons,
        "commitMatches": commit_matches,
        "dirtyInclusion": "unknown" if current_dirty else "not-dirty",
        "currentGit": {"commit": current_commit, "dirty": current_dirty},
        "recommendedActions": _runtime_source_recommended_actions() if stale else [],
        "message": message,
        **_launch_agent_source_alignment(dashboard, settings, project_root),
    }


def _git_value(root: Path, *args: str) -> str:
    return subprocess.check_output(("git", "-C", str(root), *args), text=True, stderr=subprocess.DEVNULL).strip()


def _runtime_source_recommended_actions() -> list[dict[str, Any]]:
    return [
        {
            "id": "sync-runtime-source",
            "label": "Sync runtime source snapshot only",
            "command": "zsh <source-root>/install/install.sh --runtime <runtime-home> --source-root <source-root> --source-only --yes",
            "changes": ["runtime-source-snapshot"],
            "safeForServiceCodeValidation": True,
        },
        {
            "id": "upgrade-runtime",
            "label": "Run full installer upgrade",
            "command": "zsh <source-root>/install/install.sh --runtime <runtime-home> --source-root <source-root> --upgrade --yes",
            "changes": ["runtime-source-snapshot", "dependencies", "settings", "service-registration"],
            "safeForServiceCodeValidation": True,
        },
    ]


def _references_exact_project_root(value: str, expected: str) -> bool:
    candidate = value.strip()
    if "=" in candidate:
        candidate = candidate.split("=", 1)[1]
    return candidate == expected or candidate.startswith(expected + os.sep)


def _launch_program_source_references(arguments: list[str]) -> list[str]:
    references: list[str] = []
    for argument in arguments:
        try:
            tokens = shlex.split(argument)
        except ValueError:
            tokens = [argument]
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in {"cd", "--app-dir"} and index + 1 < len(tokens):
                references.append(tokens[index + 1])
                index += 2
                continue
            candidate = token.split("=", 1)[1] if "=" in token else token
            if candidate.endswith(".py"):
                references.append(candidate)
            index += 1
    return references


def _launch_agent_source_alignment(dashboard: dict[str, Any], settings: dict[str, Any], project_root: Path) -> dict[str, Any]:
    expected = str(project_root.expanduser())
    rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
    rag_server = rag.get("server") if isinstance(rag.get("server"), dict) else {}
    labels = [
        str(dashboard.get("serviceLabel") or "com.open-nova.dashboard"),
        str(dashboard.get("watchdogLabel") or "com.open-nova.dashboard.watchdog"),
        str(((rag_server.get("launchAgent") or {}).get("label")) or "com.open-nova.rag-server"),
    ]
    launch_agents = []
    mismatches = []
    for label in labels:
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        entry: dict[str, Any] = {
            "label": label,
            "exists": plist_path.exists(),
            "aligned": None,
        }
        if not plist_path.exists():
            entry["status"] = "missing"
            launch_agents.append(entry)
            continue
        try:
            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)
        except Exception:
            entry.update({"status": "invalid", "aligned": False})
            launch_agents.append(entry)
            mismatches.append(entry)
            continue
        args = [str(item) for item in plist.get("ProgramArguments") or []]
        env = plist.get("EnvironmentVariables") if isinstance(plist.get("EnvironmentVariables"), dict) else {}
        working_directory = plist.get("WorkingDirectory")
        program_references = _launch_program_source_references(args)
        program_aligned = bool(program_references) and all(
            _references_exact_project_root(item, expected) for item in program_references
        )
        working_directory_present = working_directory is not None
        working_directory_aligned = (
            not working_directory_present
            or (isinstance(working_directory, str) and working_directory == expected)
        )
        source_authority_present = bool(program_references) or working_directory_present
        declared_project_root = env.get("NOVA_DASHBOARD_PROJECT_ROOT")
        environment_aligned = declared_project_root is None or (
            isinstance(declared_project_root, str) and declared_project_root == expected
        )
        aligned = (
            source_authority_present
            and (program_aligned if program_references else True)
            and working_directory_aligned
            and environment_aligned
        )
        entry.update(
            {
                "status": "aligned" if aligned else "mismatch",
                "aligned": aligned,
                "reloadCommand": f"open-nova dashboard restart --label {label}",
            }
        )
        launch_agents.append(entry)
        if not aligned:
            mismatches.append(entry)
    return {
        "launchAgents": launch_agents,
        "launchAgentMismatches": [
            {"label": item.get("label"), "status": item.get("status")}
            for item in mismatches
        ],
        "launchAgentMessage": (
            "managed Dashboard LaunchAgent plists point at the deployed runtime source"
            if not mismatches
            else "managed Dashboard LaunchAgent plist path/source mismatch; rewrite or reinstall LaunchAgents after source sync"
        ),
        "postSyncReloadCommand": "open-nova dashboard restart",
    }


def _resource_profile(paths: RuntimePaths, dashboard: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    rag_profile: dict[str, Any]
    if resolve_rag_settings is not None and read_server_process_state is not None:
        try:
            rag_settings = resolve_rag_settings(paths, settings=settings)
            lifecycle = read_server_process_state(rag_settings, probe_health=False)
            rag_profile = {
                "enabled": rag_settings.enabled,
                "mode": rag_settings.mode,
                "serverEnabled": rag_settings.server_enabled,
                "expectedResidentProcesses": 1 if rag_settings.server_enabled and rag_settings.enabled and rag_settings.mode != "disabled" else 0,
                "status": lifecycle.get("status"),
                "running": lifecycle.get("running"),
                "pid": lifecycle.get("pid"),
                "host": rag_settings.server_host,
                "port": rag_settings.server_port,
                "statePath": lifecycle.get("statePath"),
                "logPath": lifecycle.get("logPath"),
                "requiredModules": list(REQUIRED_SERVER_MODULES),
                "resourceClass": "high-memory-local-embedding" if rag_settings.server_enabled else "disabled-or-on-demand",
                "networkBoundary": {
                    "status": "ready" if is_loopback_host(rag_settings.server_host) else "blocked",
                    "issueCode": None if is_loopback_host(rag_settings.server_host) else RAG_SERVER_NON_LOOPBACK_ISSUE_CODE,
                },
                "internalAuthorization": lifecycle.get("internalAuthorization"),
            }
        except Exception as exc:
            rag_profile = {
                "enabled": None,
                "expectedResidentProcesses": 0,
                "status": "unknown",
                "error": exc.__class__.__name__,
                "requiredModules": list(REQUIRED_SERVER_MODULES),
            }
    else:
        rag_profile = {
            "enabled": None,
            "expectedResidentProcesses": 0,
            "status": "unavailable",
            "requiredModules": [],
        }
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "dashboard": {
            "component": "dashboard-server",
            "server": "uvicorn/FastAPI",
            "url": dashboard.get("url"),
            "host": dashboard.get("host"),
            "port": dashboard.get("port"),
            "expectedResidentProcesses": 1,
            "schedulerLoop": "in-process; wakes every 60 seconds when Dashboard is running",
            "systemDaemon": "optional launchd service/watchdog on macOS; Linux provider not implemented",
            "resourceClass": "low",
        },
        "rag": rag_profile,
        "pipeline": {
            "component": "daily-pipeline",
            "processModel": "on-demand subprocess tree",
            "expectedResidentProcesses": 0,
            "timeoutPolicy": "settings-backed per-step subprocess timeout; total watchdog is metadata only",
            "resourceClass": "burst",
        },
        "externalTools": {
            "processModel": "file/database reads by Dashboard or pipeline; no Nova-managed resident process",
            "expectedResidentProcesses": 0,
        },
    }


def _check(check_id: str, ok: bool, severity: str, message: str) -> dict[str, Any]:
    return {"id": check_id, "status": "ok" if ok else severity, "severity": severity, "message": message}


def _summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    errors = sum(1 for check in checks if check.get("status") == "error")
    warnings = sum(1 for check in checks if check.get("status") == "warn")
    return {
        "status": "error" if errors else "warn" if warnings else "ok",
        "errors": errors,
        "warnings": warnings,
        "checks": len(checks),
    }


def _validation_payload(validation: Any) -> dict[str, Any]:
    return {
        "candidate": str(validation.candidate),
        "exists": validation.exists,
        "initialized": validation.initialized,
        "writable": validation.writable,
        "valid": validation.valid,
        "issues": list(validation.issues),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value

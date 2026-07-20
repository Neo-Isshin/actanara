"""Read-only system timer preview helpers."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import config

from .paths import RuntimePaths, load_paths
from .platform_support import default_timer_provider
from .settings import read_settings
from .systemd_user import (
    SystemdUserError,
    default_user_unit_dir,
    probe_user_units,
    scheduler_units,
)
from .time import (
    SCHEDULER_SYSTEM_TIMEZONE_UNKNOWN_ISSUE_CODE,
    SCHEDULER_TIMEZONE_MISMATCH_ISSUE_CODE,
    detect_system_timezone_authority,
)


MANAGED_LAUNCHD_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def preview_system_timer(
    paths: RuntimePaths | None = None,
    *,
    launch_agent_home: Path | None = None,
    probe_runtime: bool = False,
    launchctl_runner=None,
    systemctl_runner=None,
) -> dict[str, Any]:
    """Return the read-only system timer plan for the selected runtime."""
    runtime_paths = paths or load_paths()
    settings = read_settings(runtime_paths, redact_secrets=True, persist_defaults=False)
    schedule = settings.get("schedule", {})
    timer = schedule.get("systemTimer", {}) if isinstance(schedule.get("systemTimer"), dict) else {}
    provider = timer.get("provider", default_timer_provider())
    desired_state = _scheduler_desired_state(schedule, timer)
    if provider == "systemd":
        return {
            **desired_state,
            **_systemd_timer_preview(
                schedule,
                timer,
                runtime_paths,
                probe_runtime=probe_runtime,
                systemctl_runner=systemctl_runner,
            ),
        }
    if provider == "cron":
        return {**desired_state, **_cron_timer_preview(schedule, timer)}
    if provider != "launchd":
        return {
            **desired_state,
            "provider": provider,
            "supported": False,
            "registrationImplemented": False,
            "configuredRegistered": bool(timer.get("registered")),
            "actualRegistered": None,
            "registrationMismatch": False,
            "runtimeProbe": {"enabled": probe_runtime, "status": "unsupported-provider"},
            "error": "unknown system timer provider",
        }
    jobs = _launchd_jobs(schedule, timer, runtime_paths)
    timezone_boundary = scheduler_timezone_boundary(schedule)
    job_previews = [
        _launchd_job_preview(
            job,
            launch_agent_home=launch_agent_home,
            probe_runtime=probe_runtime,
            launchctl_runner=launchctl_runner,
        )
        for job in jobs
    ]
    runtime_summary = _launchd_runtime_summary(
        job_previews,
        configured_registered=bool(timer.get("registered")),
        desired_registered=bool(desired_state["desiredRegistered"]),
        probe_runtime=probe_runtime,
    )
    return {
        **desired_state,
        "provider": "launchd",
        "supported": platform.system() == "Darwin",
        "registrationImplemented": platform.system() == "Darwin",
        "registrationBlocked": timezone_boundary["status"] == "blocked",
        "timezoneBoundary": timezone_boundary,
        **runtime_summary,
        "jobs": job_previews,
        "installPlan": [
            "Write LaunchAgent plist files under ~/Library/LaunchAgents.",
            "Back up any replaced plist into $ACTANARA_HOME/state/backups/launchd.",
            "Run launchctl bootstrap for the current GUI user.",
        ],
        "rollbackPlan": [
            "Run launchctl bootout for each registered label.",
            "Move installed plist files into $ACTANARA_HOME/state/backups/launchd.",
            "Mark schedule.systemTimer.registered=false in settings.json.",
        ],
    }


def scheduler_timezone_boundary(schedule: dict[str, Any]) -> dict[str, Any]:
    configured = str(schedule.get("timezone") or "").strip()
    if platform.system() != "Darwin":
        return {
            "schemaVersion": 1,
            "policy": "macos-system-timezone-only",
            "status": "not-applicable",
            "configuredTimezone": configured or None,
            "systemTimezone": None,
            "issueCode": None,
        }
    system_timezone = detect_system_timezone_authority()
    if not system_timezone:
        return {
            "schemaVersion": 1,
            "policy": "macos-system-timezone-only",
            "status": "blocked",
            "configuredTimezone": configured or None,
            "systemTimezone": None,
            "issueCode": SCHEDULER_SYSTEM_TIMEZONE_UNKNOWN_ISSUE_CODE,
        }
    aligned = configured == system_timezone
    return {
        "schemaVersion": 1,
        "policy": "macos-system-timezone-only",
        "status": "ready" if aligned else "blocked",
        "configuredTimezone": configured or None,
        "systemTimezone": system_timezone,
        "issueCode": None if aligned else SCHEDULER_TIMEZONE_MISMATCH_ISSUE_CODE,
    }


def _launchd_job_preview(
    job: dict[str, Any],
    *,
    launch_agent_home: Path | None = None,
    probe_runtime: bool = False,
    launchctl_runner=None,
) -> dict[str, Any]:
    label = str(job["label"])
    plist_path = _launch_agent_path(label, home=launch_agent_home)
    preview = {
        "kind": job["kind"],
        "label": label,
        "plistPath": str(plist_path),
        "time": job["time"],
        "program": job["plist"]["ProgramArguments"][0] if job["plist"]["ProgramArguments"] else None,
        "programArguments": job["plist"]["ProgramArguments"],
        "workingDirectory": job["plist"]["WorkingDirectory"],
        "startCalendarInterval": job["plist"]["StartCalendarInterval"],
        "stdoutPath": job["plist"]["StandardOutPath"],
        "stderrPath": job["plist"]["StandardErrorPath"],
        "managedPlist": _managed_launchd_plist_preview(job, launch_agent_home=launch_agent_home),
    }
    if probe_runtime:
        preview["runtimeStatus"] = _launchd_runtime_status(
            label,
            plist_path,
            expected_plist=job["plist"],
            launchctl_runner=launchctl_runner,
        )
    return preview


def _launchd_runtime_summary(
    job_previews: list[dict[str, Any]],
    *,
    configured_registered: bool,
    desired_registered: bool,
    probe_runtime: bool,
) -> dict[str, Any]:
    if not probe_runtime:
        return {
            "registered": configured_registered,
            "configuredRegistered": configured_registered,
            "actualRegistered": None,
            "registrationSource": "settings",
            "registrationMismatch": False,
            "desiredActualMismatch": False,
            "provenanceMismatch": False,
            "runtimeProbe": {"enabled": False, "status": "not-probed"},
        }

    statuses = [
        job.get("runtimeStatus")
        for job in job_previews
        if isinstance(job.get("runtimeStatus"), dict)
    ]
    loaded_values = [status.get("launchctlLoaded") for status in statuses]
    if not loaded_values or any(value is None for value in loaded_values):
        return {
            "registered": configured_registered,
            "configuredRegistered": configured_registered,
            "actualRegistered": None,
            "registrationSource": "settings",
            "registrationMismatch": False,
            "desiredActualMismatch": False,
            "provenanceMismatch": any(
                status.get("persistentPlist", {}).get("status") == "mismatch"
                for status in statuses
            ),
            "runtimeProbe": {
                "enabled": True,
                "status": "unknown",
                "expectedJobs": len(job_previews),
                "loadedJobs": sum(1 for value in loaded_values if value is True),
                "plistJobs": sum(1 for status in statuses if status.get("plistExists")),
            },
        }

    loaded_jobs = sum(1 for value in loaded_values if value is True)
    actual_registered = loaded_jobs == len(job_previews) and len(job_previews) > 0
    mismatched_jobs = sum(
        1
        for status_item in statuses
        if status_item.get("launchctlLoaded") is True and not status_item.get("provenanceAligned")
    )
    persistent_mismatches = sum(
        1
        for status_item in statuses
        if (status_item.get("persistentPlist") or {}).get("status") not in {"aligned", "missing"}
    )
    missing_plists = sum(
        1
        for status_item in statuses
        if (status_item.get("persistentPlist") or {}).get("status") == "missing"
    )
    provenance_mismatch = bool(mismatched_jobs or persistent_mismatches or (loaded_jobs and missing_plists))
    if actual_registered:
        status = "loaded"
    elif loaded_jobs:
        status = "partial"
    else:
        status = "not-loaded"
    return {
        "registered": actual_registered,
        "configuredRegistered": configured_registered,
        "actualRegistered": actual_registered,
        "registrationSource": "launchd-probe",
        "registrationMismatch": configured_registered != actual_registered,
        "desiredActualMismatch": desired_registered != actual_registered,
        "provenanceMismatch": provenance_mismatch,
        "runtimeProbe": {
            "enabled": True,
            "status": status,
            "expectedJobs": len(job_previews),
            "loadedJobs": loaded_jobs,
            "alignedJobs": sum(1 for status_item in statuses if status_item.get("provenanceAligned")),
            "mismatchedJobs": mismatched_jobs,
            "plistJobs": sum(1 for status_item in statuses if status_item.get("plistExists")),
        },
    }


def _launchd_runtime_status(
    label: str,
    plist_path: Path,
    *,
    expected_plist: dict[str, Any],
    launchctl_runner=None,
) -> dict[str, Any]:
    expected_definition = _launchd_definition(expected_plist)
    persistent = _persistent_plist_status(plist_path, expected_definition)
    status: dict[str, Any] = {
        "plistExists": plist_path.exists(),
        "launchctlLoaded": None,
        "launchctlRunning": None,
        "status": "unknown",
        "expectedDefinitionHash": _definition_hash(expected_definition),
        "persistentPlist": persistent,
        "loadedDefinition": {
            "status": "not-probed",
            "aligned": None,
            "issueCodes": [],
            "definitionHash": None,
        },
        "provenanceAligned": False,
        "issueCodes": list(persistent.get("issueCodes") or []),
    }
    if platform.system() != "Darwin":
        status["status"] = "unsupported"
        status["reason"] = "launchd-runtime-probe-unsupported"
        return status
    runner = launchctl_runner or subprocess.run
    command = ["launchctl", "print", f"gui/{os.getuid()}/{label}"]
    try:
        result = runner(command, capture_output=True, text=True, timeout=2)
    except FileNotFoundError:
        status["status"] = "unavailable"
        status["reason"] = "launchctl-unavailable"
        return status
    except subprocess.TimeoutExpired:
        status["status"] = "timeout"
        status["reason"] = "launchctl-timeout"
        return status
    except Exception:
        status["status"] = "error"
        status["reason"] = "launchctl-probe-error"
        return status

    if result.returncode not in {0, 3, 113}:
        status.update(
            {
                "launchctlLoaded": None,
                "status": "error",
                "returncode": result.returncode,
                "reason": "launchctl-unexpected-returncode",
            }
        )
        return status

    loaded = result.returncode == 0
    status.update(
        {
            "launchctlLoaded": loaded,
            "launchctlRunning": False if not loaded else _launchctl_state_running(result.stdout or ""),
            "status": "loaded" if loaded else "not-loaded",
            "returncode": result.returncode,
        }
    )
    if not loaded:
        status["reason"] = "launchctl-job-not-loaded"
        return status

    loaded_definition = _parse_launchctl_definition(result.stdout or "")
    loaded_issues = _definition_mismatches(expected_definition, loaded_definition)
    loaded_issues.extend(_missing_target_issues(loaded_definition))
    loaded_issues = sorted(set(loaded_issues))
    definition_status = "aligned" if not loaded_issues else "mismatch"
    status["loadedDefinition"] = {
        "status": definition_status,
        "aligned": not loaded_issues,
        "issueCodes": loaded_issues,
        "definitionHash": _definition_hash(loaded_definition) if loaded_definition else None,
    }
    combined_issues = sorted(set([*(persistent.get("issueCodes") or []), *loaded_issues]))
    status["issueCodes"] = combined_issues
    status["provenanceAligned"] = bool(
        persistent.get("aligned") and not loaded_issues
    )
    return status


def _launchctl_state_running(output: str) -> bool:
    return any(
        re.match(r"^\s*state\s*=\s*running\s*$", line, re.IGNORECASE)
        for line in output.splitlines()
    )


def _scheduler_desired_state(schedule: dict[str, Any], timer: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(schedule.get("enabled"))
    mode = str(schedule.get("mode") or "system")
    desired_registered = enabled and mode == "system"
    if desired_registered:
        reason = "system-mode-enabled"
    elif mode == "agent":
        reason = "agent-mode-expects-system-jobs-absent"
    else:
        reason = "scheduler-disabled-expects-system-jobs-absent"
    return {
        "schedulerEnabled": enabled,
        "schedulerMode": mode,
        "desiredRegistered": desired_registered,
        "expectedActualState": "present" if desired_registered else "absent",
        "expectationReason": reason,
        "settingsStale": bool(timer.get("stale")),
    }


def _launchd_definition(plist: dict[str, Any]) -> dict[str, Any]:
    arguments = plist.get("ProgramArguments") if isinstance(plist.get("ProgramArguments"), list) else []
    environment = plist.get("EnvironmentVariables") if isinstance(plist.get("EnvironmentVariables"), dict) else {}
    return {
        "program": str(arguments[0]) if arguments else "",
        "arguments": [str(item) for item in arguments],
        "workingDirectory": str(plist.get("WorkingDirectory") or ""),
        "actanaraHome": str(environment.get("ACTANARA_HOME") or ""),
        "pythonPath": str(environment.get("PYTHONPATH") or ""),
    }


def _persistent_plist_status(plist_path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    if not plist_path.exists():
        return {
            "status": "missing",
            "aligned": False,
            "issueCodes": ["persistent-plist-missing"],
            "definitionHash": None,
        }
    try:
        with plist_path.open("rb") as handle:
            payload = plistlib.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("plist payload is not an object")
    except (OSError, plistlib.InvalidFileException, ValueError, TypeError):
        return {
            "status": "invalid",
            "aligned": False,
            "issueCodes": ["persistent-plist-invalid"],
            "definitionHash": None,
        }
    definition = _launchd_definition(payload)
    issues = _definition_mismatches(expected, definition, prefix="persistent-")
    return {
        "status": "aligned" if not issues else "mismatch",
        "aligned": not issues,
        "issueCodes": issues,
        "definitionHash": _definition_hash(definition),
    }


def _parse_launchctl_definition(output: str) -> dict[str, Any]:
    lines = output.splitlines()
    definition: dict[str, Any] = {}
    for line in lines:
        match = re.match(r"^\s*(program|working directory)\s*=\s*(.*?)\s*$", line)
        if not match:
            continue
        key = "program" if match.group(1) == "program" else "workingDirectory"
        definition[key] = _unquote_launchctl_value(match.group(2))

    arguments = _launchctl_block(lines, "arguments")
    if arguments is not None:
        definition["arguments"] = [
            _unquote_launchctl_value(re.sub(r"^\s*\d+\s*=\s*", "", line.strip()))
            for line in arguments
            if line.strip()
        ]
    environment = _launchctl_block(lines, "environment")
    if environment is not None:
        parsed_environment: dict[str, str] = {}
        for line in environment:
            if "=>" not in line:
                continue
            key, value = line.split("=>", 1)
            parsed_environment[key.strip()] = _unquote_launchctl_value(value.strip())
        definition["actanaraHome"] = parsed_environment.get("ACTANARA_HOME", "")
        definition["pythonPath"] = parsed_environment.get("PYTHONPATH", "")
    return definition


def _launchctl_block(lines: list[str], name: str) -> list[str] | None:
    start_pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*\{{\s*$")
    for index, line in enumerate(lines):
        if not start_pattern.match(line):
            continue
        values: list[str] = []
        for item in lines[index + 1 :]:
            if re.match(r"^\s*}\s*$", item):
                return values
            values.append(item)
        return values
    return None


def _unquote_launchctl_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _definition_mismatches(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    prefix: str = "",
) -> list[str]:
    issues: list[str] = []
    fields = (
        ("program", "program"),
        ("arguments", "arguments"),
        ("workingDirectory", "working-directory"),
        ("actanaraHome", "actanara-home"),
        ("pythonPath", "pythonpath"),
    )
    for field, issue_name in fields:
        value = actual.get(field)
        if field not in actual or value is None or value == "":
            issues.append(f"{prefix}{issue_name}-unknown")
        elif value != expected.get(field):
            issues.append(f"{prefix}{issue_name}-mismatch")
    return issues


def _missing_target_issues(definition: dict[str, Any]) -> list[str]:
    candidates: list[tuple[str, str]] = [
        ("program", str(definition.get("program") or "")),
        ("working-directory", str(definition.get("workingDirectory") or "")),
        ("actanara-home", str(definition.get("actanaraHome") or "")),
    ]
    arguments = definition.get("arguments") if isinstance(definition.get("arguments"), list) else []
    if len(arguments) > 1:
        candidates.append(("script", str(arguments[1] or "")))
    python_path = str(definition.get("pythonPath") or "")
    for item in python_path.split(os.pathsep):
        if item:
            candidates.append(("pythonpath", item))
    issues = []
    for name, raw_path in candidates:
        path = Path(raw_path).expanduser()
        if path.is_absolute() and not path.exists():
            issues.append(f"{name}-target-missing")
    return issues


def _definition_hash(definition: dict[str, Any]) -> str:
    serialized = json.dumps(definition, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _parse_time(value: str | None, fallback: str) -> tuple[int, int, str]:
    raw = value or fallback
    try:
        hour_s, minute_s = raw.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute, f"{hour:02d}:{minute:02d}"
    except (AttributeError, ValueError):
        pass
    hour_s, minute_s = fallback.split(":", 1)
    hour, minute = int(hour_s), int(minute_s)
    return hour, minute, fallback


def _launchd_jobs(schedule: dict[str, Any], timer: dict[str, Any], paths: RuntimePaths | None = None) -> list[dict[str, Any]]:
    runtime_paths = paths or load_paths()
    base_label = str(timer.get("label") or "actanara.daily").strip() or "actanara.daily"
    workspace = runtime_paths.home / "app" / "source"
    py = str(runtime_paths.home / ".venv" / "bin" / "python")
    env = {
        "PATH": MANAGED_LAUNCHD_PATH,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": f"{workspace}:{workspace / 'src'}:{workspace / 'src' / 'dashboard'}",
        "ACTANARA_HOME": str(runtime_paths.home),
    }
    pipeline_hour, pipeline_minute, pipeline_time = _parse_time(schedule.get("dailyPipelineTime"), "04:00")
    aggregation_hour, aggregation_minute, aggregation_time = _parse_time(schedule.get("dashboardAggregationTime"), "04:30")
    return [
        {
            "kind": "daily-pipeline",
            "label": f"{base_label}.pipeline",
            "time": pipeline_time,
            "plist": _launchd_plist(
                f"{base_label}.pipeline",
                [py, str(workspace / "advanced" / "pipeline" / "run_daily_pipeline.py")],
                pipeline_hour,
                pipeline_minute,
                env,
                runtime_paths,
            ),
        },
        {
            "kind": "dashboard-aggregation",
            "label": f"{base_label}.dashboard-aggregation",
            "time": aggregation_time,
            "plist": _launchd_plist(
                f"{base_label}.dashboard-aggregation",
                [py, str(workspace / "advanced" / "pipeline" / "run_dashboard_foundation_refresh.py")],
                aggregation_hour,
                aggregation_minute,
                env,
                runtime_paths,
            ),
        },
    ]


def _systemd_timer_preview(
    schedule: dict[str, Any],
    timer: dict[str, Any],
    paths: RuntimePaths,
    *,
    probe_runtime: bool,
    systemctl_runner=None,
) -> dict[str, Any]:
    jobs = _linux_timer_jobs(schedule, timer, paths)
    units = scheduler_units(paths, schedule, timer)
    units_by_name = {unit.name: unit for unit in units}
    unit_root = default_user_unit_dir()
    configured_registered = bool(timer.get("registered"))
    runtime_probe = {
        "status": "not-probed",
        "actualRegistered": None,
        "units": [unit.name for unit in units if unit.enable_now],
    }
    if probe_runtime:
        try:
            runtime_probe = probe_user_units(
                units,
                **({"runner": systemctl_runner} if systemctl_runner is not None else {}),
            )
        except SystemdUserError:
            runtime_probe = {
                "status": "unknown",
                "actualRegistered": None,
                "units": [unit.name for unit in units if unit.enable_now],
            }
    actual_registered = runtime_probe.get("actualRegistered")
    probe_records = {
        str(item.get("name")): item
        for item in runtime_probe.get("units") or []
        if isinstance(item, dict) and item.get("name")
    }
    job_previews = []
    for job in jobs:
        service_name = str(job["unitName"])
        timer_name = str(job["timerName"])
        service_path = unit_root / service_name
        timer_path = unit_root / timer_name
        definitions = []
        for name, target in ((service_name, service_path), (timer_name, timer_path)):
            expected = units_by_name[name].content
            if not probe_runtime:
                definitions.append(
                    {"name": name, "path": str(target), "exists": None, "aligned": None}
                )
                continue
            try:
                actual = target.read_text(encoding="utf-8")
            except FileNotFoundError:
                definitions.append(
                    {"name": name, "path": str(target), "exists": False, "aligned": False}
                )
            except OSError:
                definitions.append(
                    {"name": name, "path": str(target), "exists": True, "aligned": False}
                )
            else:
                definitions.append(
                    {
                        "name": name,
                        "path": str(target),
                        "exists": True,
                        "aligned": actual == expected,
                    }
                )
        timer_probe = probe_records.get(timer_name)
        actual_loaded = (
            bool(timer_probe.get("enabled") and timer_probe.get("active"))
            if timer_probe is not None
            else None
        )
        definitions_present = (
            all(item.get("exists") is True for item in definitions) if probe_runtime else None
        )
        definitions_aligned = (
            all(item.get("aligned") is True for item in definitions) if probe_runtime else None
        )
        issue_codes = []
        if probe_runtime and definitions_present is False:
            issue_codes.append("systemd-unit-missing")
        elif probe_runtime and definitions_aligned is False:
            issue_codes.append("systemd-unit-definition-mismatch")
        if timer_probe is not None and not timer_probe.get("enabled"):
            issue_codes.append("systemd-timer-disabled")
        if timer_probe is not None and not timer_probe.get("active"):
            issue_codes.append("systemd-timer-inactive")
        job_previews.append(
            {
                "kind": job["kind"],
                "unitName": service_name,
                "timerName": timer_name,
                "time": job["time"],
                "command": job["command"],
                "unitPath": str(service_path),
                "timerPath": str(timer_path),
                "runtimeStatus": {
                    "provider": "systemd",
                    "status": (
                        "not-probed"
                        if not probe_runtime
                        else "aligned"
                        if actual_loaded and definitions_aligned
                        else "mismatch"
                    ),
                    "actualLoaded": actual_loaded,
                    "systemdEnabled": timer_probe.get("enabled") if timer_probe else None,
                    "systemdActive": timer_probe.get("active") if timer_probe else None,
                    "definitionsPresent": definitions_present,
                    "definitionsAligned": definitions_aligned,
                    "definitions": definitions,
                    "issueCodes": issue_codes,
                },
            }
        )
    linux_supported = platform.system() == "Linux"
    return {
        "provider": "systemd",
        "supported": linux_supported,
        "registrationImplemented": linux_supported,
        "installerRegistrationImplemented": linux_supported,
        "registered": configured_registered if actual_registered is None else bool(actual_registered),
        "configuredRegistered": configured_registered,
        "actualRegistered": actual_registered,
        "registrationSource": "systemd-probe" if actual_registered is not None else "settings",
        "registrationMismatch": (
            configured_registered != bool(actual_registered)
            if actual_registered is not None
            else False
        ),
        "runtimeProbe": {"enabled": probe_runtime, **runtime_probe},
        "binary": os.environ.get("ACTANARA_INSTALL_SYSTEMCTL") or shutil.which("systemctl"),
        "jobs": job_previews,
        "installPlan": [
            "Create user-level systemd service and timer units under ~/.config/systemd/user.",
            "Run systemctl --user daemon-reload.",
            "Run systemctl --user enable --now for each timer.",
        ],
        "rollbackPlan": [
            "Run systemctl --user disable --now for each timer.",
            "Move generated unit files into $ACTANARA_HOME/state/backups/systemd.",
            "Mark schedule.systemTimer.registered=false in settings.json.",
        ],
        "note": "The Linux installer registers these units; Dashboard schedule editing remains read-only.",
    }


def _cron_timer_preview(schedule: dict[str, Any], timer: dict[str, Any]) -> dict[str, Any]:
    jobs = _linux_timer_jobs(schedule, timer)
    return {
        "provider": "cron",
        "supported": platform.system() in {"Linux", "Darwin"},
        "registrationImplemented": False,
        "registered": bool(timer.get("registered")),
        "binary": shutil.which("crontab"),
        "jobs": [
            {
                "kind": job["kind"],
                "time": job["time"],
                "cron": f"{job['minute']} {job['hour']} * * * cd {job['workingDirectory']} && {job['command']}",
            }
            for job in jobs
        ],
        "installPlan": [
            "Append managed cron entries for daily pipeline and Dashboard aggregation.",
            "Keep an operator-visible backup of the previous crontab.",
            "Mark schedule.systemTimer.registered=true only after explicit operator confirmation.",
        ],
        "rollbackPlan": [
            "Remove only managed Actanara cron entries.",
            "Restore previous crontab from backup if needed.",
            "Mark schedule.systemTimer.registered=false in settings.json.",
        ],
        "note": "Read-only preview only; Dashboard registration is not implemented for cron in this batch.",
    }


def _linux_timer_jobs(
    schedule: dict[str, Any],
    timer: dict[str, Any],
    paths: RuntimePaths | None = None,
) -> list[dict[str, Any]]:
    base_label = str(timer.get("label") or "actanara.daily").strip() or "actanara.daily"
    runtime_paths = paths or load_paths()
    py = str(runtime_paths.home / ".venv" / "bin" / "python")
    workspace_path = runtime_paths.home / "app" / "source"
    workspace = str(workspace_path)
    pipeline_hour, pipeline_minute, pipeline_time = _parse_time(schedule.get("dailyPipelineTime"), "04:00")
    aggregation_hour, aggregation_minute, aggregation_time = _parse_time(schedule.get("dashboardAggregationTime"), "04:30")
    return [
        {
            "kind": "daily-pipeline",
            "unitName": f"{base_label}.pipeline.service",
            "timerName": f"{base_label}.pipeline.timer",
            "hour": pipeline_hour,
            "minute": pipeline_minute,
            "time": pipeline_time,
            "command": f"{py} {workspace_path / 'advanced' / 'pipeline' / 'run_daily_pipeline.py'}",
            "workingDirectory": workspace,
        },
        {
            "kind": "dashboard-aggregation",
            "unitName": f"{base_label}.dashboard-aggregation.service",
            "timerName": f"{base_label}.dashboard-aggregation.timer",
            "hour": aggregation_hour,
            "minute": aggregation_minute,
            "time": aggregation_time,
            "command": f"{py} {workspace_path / 'advanced' / 'pipeline' / 'run_dashboard_foundation_refresh.py'}",
            "workingDirectory": workspace,
        },
    ]


def _launchd_plist(label: str, args: list[str], hour: int, minute: int, env: dict[str, str], paths: RuntimePaths) -> dict[str, Any]:
    working_directory = str(Path(env["PYTHONPATH"].split(os.pathsep, 1)[0]).expanduser().absolute())
    return {
        "Label": label,
        "ProgramArguments": args,
        "WorkingDirectory": working_directory,
        "EnvironmentVariables": env,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(paths.home / "state" / "logs" / f"{label}.out.log"),
        "StandardErrorPath": str(paths.home / "state" / "logs" / f"{label}.err.log"),
        "RunAtLoad": False,
    }


def _launch_agent_path(label: str, *, home: Path | None = None) -> Path:
    selected_home = home or Path.home()
    return selected_home / "Library" / "LaunchAgents" / f"{label}.plist"


def _managed_launchd_plist_preview(job: dict[str, Any], *, launch_agent_home: Path | None = None) -> dict[str, Any]:
    label = str(job.get("label") or "")
    plist = dict(job.get("plist") or {})
    plist_path = _launch_agent_path(label, home=launch_agent_home)
    serialized = plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "dryRunOnly": True,
        "managedBy": "actanara-onboarding",
        "provider": "launchd-user",
        "label": label,
        "plistPath": str(plist_path),
        "plistDirectory": str(plist_path.parent),
        "plist": plist,
        "serializedPlist": serialized,
        "serializationFormat": "plist-xml-v1",
        "wouldWritePlist": False,
        "wouldCallLaunchctl": False,
        "registrationCommandPreview": ["launchctl", "bootstrap", "gui/$UID", str(plist_path)],
        "rollbackCommandPreview": ["launchctl", "bootout", "gui/$UID", str(plist_path)],
        "pathPolicy": {
            "target": "user-launch-agents-preview",
            "requiresExplicitOperatorApproval": True,
            "writesAllowedInCurrentPhase": False,
        },
    }

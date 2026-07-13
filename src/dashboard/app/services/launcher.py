"""Dashboard-managed LaunchAgent operations with preview, backup, and audit."""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from advanced.dashboard import dashboard_launch_agent, rag_server_launch_agent
from data_foundation.paths import load_paths
from data_foundation.settings import read_settings, write_settings

DASHBOARD_INSTALL_CONFIRMATION = "INSTALL OPEN NOVA DASHBOARD LAUNCHAGENT"
DASHBOARD_UNINSTALL_CONFIRMATION = "UNINSTALL OPEN NOVA DASHBOARD LAUNCHAGENT"
RAG_INSTALL_CONFIRMATION = "INSTALL OPEN NOVA RAG LAUNCHAGENT"
RAG_UNINSTALL_CONFIRMATION = "UNINSTALL OPEN NOVA RAG LAUNCHAGENT"


def preview_dashboard_launch_agent(*, probe_runtime: bool = True, launchctl_runner=None) -> dict[str, Any]:
    return _preview("dashboard", action="install", probe_runtime=probe_runtime, launchctl_runner=launchctl_runner)


def preview_rag_launch_agent(*, probe_runtime: bool = True, launchctl_runner=None) -> dict[str, Any]:
    return _preview("rag", action="install", probe_runtime=probe_runtime, launchctl_runner=launchctl_runner)


def install_dashboard_launch_agent(payload: dict | None = None) -> dict[str, Any]:
    return _apply("dashboard", "install", payload)


def uninstall_dashboard_launch_agent(payload: dict | None = None) -> dict[str, Any]:
    return _apply("dashboard", "uninstall", payload)


def install_rag_launch_agent(payload: dict | None = None) -> dict[str, Any]:
    return _apply("rag", "install", payload)


def uninstall_rag_launch_agent(payload: dict | None = None) -> dict[str, Any]:
    return _apply("rag", "uninstall", payload)


def _preview(
    kind: str,
    *,
    action: str,
    probe_runtime: bool = False,
    launchctl_runner=None,
) -> dict[str, Any]:
    jobs = _jobs(kind)
    confirmation = _confirmation(kind, action)
    job_previews = [
        _job_preview(job, probe_runtime=probe_runtime, launchctl_runner=launchctl_runner)
        for job in jobs
    ]
    runtime_summary = _runtime_summary(
        job_previews,
        configured_registered=_configured_registered(kind),
        probe_runtime=probe_runtime,
    )
    return {
        "kind": kind,
        "action": action,
        "provider": "launchd",
        "dryRun": True,
        "confirmationTextRequired": confirmation,
        "installConfirmationTextRequired": _confirmation(kind, "install"),
        "uninstallConfirmationTextRequired": _confirmation(kind, "uninstall"),
        **runtime_summary,
        "jobs": job_previews,
        "mutationPolicy": {
            "writesLaunchAgents": False,
            "callsLaunchctl": False,
            "settingsMutated": False,
        },
    }


def _apply(kind: str, action: str, payload: dict | None = None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    if payload.get("dryRun") is True:
        return _preview(kind, action=action, probe_runtime=False)
    required = _confirmation(kind, action)
    if str(payload.get("confirmationText") or "") != required:
        raise ValueError(f"confirmationText must be exactly: {required}")
    jobs = _jobs(kind)
    paths = load_paths()
    now = datetime.now().astimezone()
    backup_dir = paths.state_dir / "backups" / "launchd" / f"{now.strftime('%Y%m%d-%H%M%S')}-{kind}-{action}"
    changed: list[dict[str, Any]] = []
    operation_results: list[dict[str, Any]] = []
    try:
        if action == "install":
            for job in jobs:
                plist_path = Path(job["plistPath"])
                plist_path.parent.mkdir(parents=True, exist_ok=True)
                if plist_path.exists():
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    plist_path.replace(backup_dir / plist_path.name)
                _write_plist(plist_path, job["plist"])
                operation_results.append({"id": f"write-plist:{job['label']}", "status": "success", "plistPath": str(plist_path)})
                _record_launchctl(operation_results, "bootout", job["label"], plist_path, allow_failure=True)
                _record_launchctl(operation_results, "bootstrap", job["label"], plist_path)
                if job.get("kickstart", True):
                    _record_launchctl(operation_results, "kickstart", job["label"], plist_path, allow_failure=True)
                changed.append(_job_result(job))
            registered = True
        elif action == "uninstall":
            for job in jobs:
                plist_path = Path(job["plistPath"])
                _record_launchctl(operation_results, "bootout", job["label"], plist_path, allow_failure=True)
                if plist_path.exists():
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    plist_path.replace(backup_dir / plist_path.name)
                    operation_results.append({"id": f"backup-plist:{job['label']}", "status": "success", "plistPath": str(plist_path)})
                changed.append(_job_result(job))
            registered = False
        else:
            raise ValueError("unknown LaunchAgent action")
    except Exception as exc:
        _write_launch_agent_audit(
            kind,
            registered=False if action == "install" else True,
            action=action,
            jobs=changed,
            backup_dir=backup_dir,
            now=now,
            status="failed",
            error=str(exc),
            operation_results=operation_results,
        )
        raise
    _write_launch_agent_audit(
        kind,
        registered=registered,
        action=action,
        jobs=changed,
        backup_dir=backup_dir,
        now=now,
        status="success",
        error=None,
        operation_results=operation_results,
    )
    return {
        "kind": kind,
        "action": action,
        "status": "registered" if registered else "unregistered",
        "jobs": changed,
        "backupDir": str(backup_dir) if backup_dir.exists() else None,
    }


def _jobs(kind: str) -> list[dict[str, Any]]:
    if kind == "dashboard":
        defaults = dashboard_launch_agent.dashboard_launch_defaults()
        service_plist = dashboard_launch_agent.build_service_plist(
            label=defaults["label"],
            python=defaults["python"],
            project_root=defaults["project_root"],
            nova_home=defaults["nova_home"],
            host=defaults["host"],
            port=defaults["port"],
            foundation=True,
            logs_dir=defaults["logs_dir"],
        )
        watchdog_plist = dashboard_launch_agent.build_watchdog_plist(
            label=defaults["watchdog_label"],
            service_label=defaults["label"],
            python=defaults["python"],
            script=defaults["project_root"] / "advanced" / "dashboard" / "dashboard_launch_agent.py",
            url=defaults["url"],
            interval=60,
            nova_home=defaults["nova_home"],
            logs_dir=defaults["logs_dir"],
        )
        return [
            {
                "kind": "dashboard-service",
                "label": defaults["label"],
                "plistPath": str(dashboard_launch_agent.service_plist_path(defaults["label"])),
                "plist": service_plist,
            },
            {
                "kind": "dashboard-watchdog",
                "label": defaults["watchdog_label"],
                "plistPath": str(dashboard_launch_agent.watchdog_plist_path(defaults["watchdog_label"])),
                "plist": watchdog_plist,
            },
        ]
    if kind == "rag":
        defaults = rag_server_launch_agent.rag_launch_defaults()
        service_plist = rag_server_launch_agent.build_service_plist(
            label=defaults["label"],
            python=defaults["python"],
            project_root=defaults["project_root"],
            nova_home=defaults["nova_home"],
            script=defaults["project_root"] / "advanced" / "dashboard" / "rag_server_launch_agent.py",
            logs_dir=defaults["logs_dir"],
        )
        return [
            {
                "kind": "rag-server",
                "label": defaults["label"],
                "plistPath": str(rag_server_launch_agent.service_plist_path(defaults["label"])),
                "plist": service_plist,
                "kickstart": False,
            }
        ]
    raise ValueError("unknown LaunchAgent kind")


def _confirmation(kind: str, action: str) -> str:
    values = {
        ("dashboard", "install"): DASHBOARD_INSTALL_CONFIRMATION,
        ("dashboard", "uninstall"): DASHBOARD_UNINSTALL_CONFIRMATION,
        ("rag", "install"): RAG_INSTALL_CONFIRMATION,
        ("rag", "uninstall"): RAG_UNINSTALL_CONFIRMATION,
    }
    try:
        return values[(kind, action)]
    except KeyError as exc:
        raise ValueError("unknown LaunchAgent action") from exc


def _job_preview(
    job: dict[str, Any],
    *,
    probe_runtime: bool = False,
    launchctl_runner=None,
) -> dict[str, Any]:
    plist = job["plist"]
    preview = {
        **_job_result(job),
        "programArguments": plist.get("ProgramArguments") or [],
        "stdoutPath": plist.get("StandardOutPath"),
        "stderrPath": plist.get("StandardErrorPath"),
        "managedPlist": {
            "plistPath": job["plistPath"],
            "payload": plistlib.dumps(plist, sort_keys=False).decode("utf-8"),
        },
    }
    if probe_runtime:
        preview["runtimeStatus"] = _launchd_runtime_status(
            str(job["label"]),
            Path(str(job["plistPath"])),
            launchctl_runner=launchctl_runner,
        )
    return preview


def _job_result(job: dict[str, Any]) -> dict[str, Any]:
    return {"kind": job["kind"], "label": job["label"], "plistPath": job["plistPath"]}


def _configured_registered(kind: str) -> bool:
    try:
        settings = read_settings(load_paths(), redact_secrets=True)
        if kind == "dashboard":
            dashboard = settings.get("dashboard") if isinstance(settings.get("dashboard"), dict) else {}
            launch_agent = dashboard.get("launchAgent") if isinstance(dashboard.get("launchAgent"), dict) else {}
        else:
            rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
            server = rag.get("server") if isinstance(rag.get("server"), dict) else {}
            launch_agent = server.get("launchAgent") if isinstance(server.get("launchAgent"), dict) else {}
        return bool(launch_agent.get("registered"))
    except Exception:
        return False


def _runtime_summary(
    job_previews: list[dict[str, Any]],
    *,
    configured_registered: bool,
    probe_runtime: bool,
) -> dict[str, Any]:
    if not probe_runtime:
        return {
            "registered": configured_registered,
            "configuredRegistered": configured_registered,
            "actualRegistered": None,
            "registrationSource": "settings",
            "registrationMismatch": False,
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
        "runtimeProbe": {
            "enabled": True,
            "status": status,
            "expectedJobs": len(job_previews),
            "loadedJobs": loaded_jobs,
            "plistJobs": sum(1 for status_item in statuses if status_item.get("plistExists")),
        },
    }


def _launchd_runtime_status(label: str, plist_path: Path, *, launchctl_runner=None) -> dict[str, Any]:
    status: dict[str, Any] = {
        "plistExists": plist_path.exists(),
        "launchctlLoaded": None,
        "status": "unknown",
    }
    if platform.system() != "Darwin":
        status["status"] = "unsupported"
        status["error"] = "launchd runtime probing is only supported on macOS"
        return status
    runner = launchctl_runner or subprocess.run
    command = ["launchctl", "print", f"gui/{os.getuid()}/{label}"]
    try:
        result = runner(command, capture_output=True, text=True, timeout=2)
    except FileNotFoundError:
        status["status"] = "unavailable"
        status["error"] = "launchctl not found"
        return status
    except subprocess.TimeoutExpired:
        status["status"] = "timeout"
        status["error"] = "launchctl print timed out"
        return status
    except Exception as exc:
        status["status"] = "error"
        status["error"] = str(exc)
        return status
    loaded = result.returncode == 0
    status.update(
        {
            "launchctlLoaded": loaded,
            "status": "loaded" if loaded else "not-loaded",
            "returncode": result.returncode,
        }
    )
    if not loaded:
        message = (result.stderr or result.stdout or "").strip()
        if message:
            status["message"] = message[:500]
    return status


def _write_launch_agent_audit(
    kind: str,
    *,
    registered: bool,
    action: str,
    jobs: list[dict[str, Any]],
    backup_dir: Path,
    now: datetime,
    status: str = "success",
    error: str | None = None,
    operation_results: list[dict[str, Any]] | None = None,
) -> None:
    key = "dashboard" if kind == "dashboard" else "rag"
    launch_agent = {
        "registered": registered,
        "registrationManagedBy": "dashboard",
        "registeredAt" if registered else "unregisteredAt": now.isoformat(),
        "jobs": jobs if registered else [],
        "partialJobs": jobs,
        "backupDir": str(backup_dir) if backup_dir.exists() else None,
        "lastAction": action,
        "lastActionStatus": status,
        "lastError": error,
        "lastErrorAt": now.isoformat() if error else None,
        "operationResults": operation_results or [],
        "rollbackHint": "Use the matching uninstall action to unload partial jobs, or restore plist files from backupDir if needed.",
    }
    payload: dict[str, Any]
    if key == "dashboard":
        payload = {
            "dashboard": {
                "launchAgent": launch_agent
            }
        }
    else:
        settings = read_settings(load_paths(), redact_secrets=False)
        rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
        server = rag.get("server") if isinstance(rag.get("server"), dict) else {}
        payload = {
            "rag": {
                "server": {
                    **server,
                    "launchAgent": launch_agent,
                }
            }
        }
    write_settings(payload, load_paths())


def _write_plist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)


def _launchctl(action: str, label: str, plist_path: Path, *, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    binary = os.environ.get("NOVA_INSTALL_LAUNCHCTL") or shutil.which("launchctl") or "/bin/launchctl"
    domain = f"gui/{os.getuid()}"
    command = [binary]
    if action == "bootout":
        command.extend(["bootout", domain, str(plist_path)])
    elif action == "bootstrap":
        command.extend(["bootstrap", domain, str(plist_path)])
    elif action == "kickstart":
        command.extend(["kickstart", "-k", f"{domain}/{label}"])
    else:
        raise ValueError("unknown launchctl action")
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0 and not allow_failure:
        raise RuntimeError(result.stderr or result.stdout or f"launchctl {action} failed for {label}")
    return result


def _record_launchctl(
    operation_results: list[dict[str, Any]],
    action: str,
    label: str,
    plist_path: Path,
    *,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        result = _launchctl(action, label, plist_path, allow_failure=allow_failure)
    except Exception as exc:
        operation_results.append(
            {
                "id": f"launchctl-{action}:{label}",
                "status": "failed",
                "allowFailure": allow_failure,
                "plistPath": str(plist_path),
                "error": str(exc),
            }
        )
        raise
    returncode = result.returncode if isinstance(result.returncode, int) else 0
    operation_results.append(
        {
            "id": f"launchctl-{action}:{label}",
            "status": "success" if returncode == 0 else "skipped",
            "allowFailure": allow_failure,
            "plistPath": str(plist_path),
            "returncode": returncode,
        }
    )
    return result

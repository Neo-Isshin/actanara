"""Linux systemd user-unit rendering, registration, and read-only probes."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .paths import RuntimePaths


UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,126}$")
MANAGED_UNIT_HEADER = "# Managed by Actanara. Do not edit by hand."
Runner = Callable[..., subprocess.CompletedProcess[str]]


class SystemdUserError(RuntimeError):
    pass


@dataclass(frozen=True)
class UserUnit:
    name: str
    content: str
    enable_now: bool = True


def _quote(value: str) -> str:
    text = str(value)
    if any(character in text for character in "\0\r\n"):
        raise SystemdUserError("systemd unit value contains a control character")
    return '"' + text.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _path_value(value: Path) -> str:
    text = str(value)
    if not value.is_absolute():
        raise SystemdUserError("systemd working directory must be absolute")
    if any(character in text for character in "\0\r\n") or text.endswith("\\"):
        raise SystemdUserError("systemd working directory contains an unsafe character")
    # WorkingDirectory= is a scalar path directive, not an ExecStart-style
    # argument list. Quoting the value makes the quotes part of the path on
    # systemd 257, so preserve the scalar and escape only specifier markers.
    return text.replace("%", "%%")


def _unit_name(value: str, suffix: str) -> str:
    base = str(value or "actanara").strip()
    if not UNIT_NAME_RE.fullmatch(base):
        raise SystemdUserError("systemd unit label is unsafe")
    name = f"{base}.{suffix}"
    if not UNIT_NAME_RE.fullmatch(name):
        raise SystemdUserError("systemd unit name is unsafe")
    return name


def _environment_lines(environment: dict[str, str]) -> list[str]:
    return [f"Environment={_quote(f'{key}={value}')}" for key, value in sorted(environment.items())]


def _service_unit(
    *,
    description: str,
    command: Iterable[str],
    working_directory: Path,
    environment: dict[str, str],
    restart: bool,
) -> str:
    command_line = " ".join(_quote(item) for item in command)
    lines = [
        MANAGED_UNIT_HEADER,
        "[Unit]",
        f"Description={description}",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple" if restart else "Type=oneshot",
        f"WorkingDirectory={_path_value(working_directory)}",
        *_environment_lines(environment),
        f"ExecStart={command_line}",
    ]
    if restart:
        lines.extend(("Restart=on-failure", "RestartSec=10"))
    lines.extend(("", "[Install]", "WantedBy=default.target", ""))
    return "\n".join(lines)


def _timer_unit(*, description: str, service_name: str, time_of_day: str, timezone: str) -> str:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(time_of_day)):
        raise SystemdUserError("systemd timer time must use HH:MM")
    if not re.fullmatch(r"[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*", str(timezone)):
        raise SystemdUserError("systemd timer timezone is unsafe")
    return "\n".join(
        (
            MANAGED_UNIT_HEADER,
            "[Unit]",
            f"Description={description}",
            "",
            "[Timer]",
            f"OnCalendar=*-*-* {time_of_day}:00 {timezone}",
            "Persistent=true",
            f"Unit={service_name}",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        )
    )


def scheduler_units(paths: RuntimePaths, schedule: dict, timer: dict) -> list[UserUnit]:
    source = paths.home / "app" / "source"
    python = paths.home / ".venv" / "bin" / "python"
    label = str(timer.get("label") or "actanara.daily")
    timezone = str(schedule.get("timezone") or "UTC")
    environment = {
        "ACTANARA_HOME": str(paths.home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join((str(source), str(source / "src"), str(source / "src" / "dashboard"))),
    }
    jobs = (
        (
            "pipeline",
            "Actanara daily pipeline",
            str(schedule.get("dailyPipelineTime") or "04:00"),
            source / "advanced" / "pipeline" / "run_daily_pipeline.py",
        ),
        (
            "dashboard-aggregation",
            "Actanara Dashboard aggregation",
            str(schedule.get("dashboardAggregationTime") or "04:30"),
            source / "advanced" / "pipeline" / "run_dashboard_foundation_refresh.py",
        ),
    )
    units: list[UserUnit] = []
    for suffix, description, time_of_day, script in jobs:
        service_name = _unit_name(label, f"{suffix}.service")
        timer_name = _unit_name(label, f"{suffix}.timer")
        units.append(
            UserUnit(
                name=service_name,
                content=_service_unit(
                    description=description,
                    command=(str(python), str(script)),
                    working_directory=source,
                    environment=environment,
                    restart=False,
                ),
                enable_now=False,
            )
        )
        units.append(
            UserUnit(
                name=timer_name,
                content=_timer_unit(
                    description=f"{description} timer",
                    service_name=service_name,
                    time_of_day=time_of_day,
                    timezone=timezone,
                ),
            )
        )
    return units


def dashboard_unit(paths: RuntimePaths, dashboard: dict) -> UserUnit:
    source = paths.home / "app" / "source"
    python = paths.home / ".venv" / "bin" / "python"
    host = str(dashboard.get("host") or "127.0.0.1")
    port = int(dashboard.get("port") or 3036)
    environment = {
        "ACTANARA_HOME": str(paths.home),
        "ACTANARA_DATA_FOUNDATION_ENABLED": "true",
        "DASHBOARD_READ_SOURCE": "foundation",
        "DIARY_MEMORY_SOURCE": "foundation",
        "DIARY_METRICS_SOURCE": "foundation",
        "DIARY_TASKS_SOURCE": "foundation",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join((str(source), str(source / "src"), str(source / "src" / "dashboard"))),
        "REPORT_READ_SOURCE": "foundation",
    }
    return UserUnit(
        name="actanara-dashboard.service",
        content=_service_unit(
            description="Actanara Dashboard",
            command=(
                str(python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--app-dir",
                str(source / "src" / "dashboard"),
                "--host",
                host,
                "--port",
                str(port),
            ),
            working_directory=source,
            environment=environment,
            restart=True,
        ),
    )


def rag_unit(paths: RuntimePaths) -> UserUnit:
    source = paths.home / "app" / "source"
    python = paths.home / ".venv" / "bin" / "python"
    environment = {
        "ACTANARA_HOME": str(paths.home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join((str(source), str(source / "src"))),
    }
    return UserUnit(
        name="actanara-rag-server.service",
        content=_service_unit(
            description="Actanara nova-RAG server",
            command=(
                str(python),
                str(source / "advanced" / "dashboard" / "rag_server_launch_agent.py"),
                "run",
                "--project-root",
                str(source),
                "--actanara-home",
                str(paths.home),
            ),
            working_directory=source,
            environment=environment,
            restart=True,
        ),
    )


def default_user_unit_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd" / "user"


def _systemctl_binary() -> str:
    return os.environ.get("ACTANARA_INSTALL_SYSTEMCTL") or shutil.which("systemctl") or ""


def _run_systemctl(
    arguments: Iterable[str],
    *,
    runner: Runner = subprocess.run,
    allow_status: set[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    binary = _systemctl_binary()
    if not binary:
        raise SystemdUserError("systemctl is unavailable")
    command = [binary, "--user", *arguments]
    result = runner(command, text=True, capture_output=True, check=False, timeout=30)
    allowed = allow_status if allow_status is not None else {0}
    if result.returncode not in allowed:
        detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")
        if len(detail) > 500:
            detail = detail[:497] + "..."
        suffix = f": {detail}" if detail else ""
        raise SystemdUserError(
            f"systemctl --user failed with status {result.returncode}{suffix}"
        )
    return result


def probe_user_units(units: Iterable[UserUnit], *, runner: Runner = subprocess.run) -> dict:
    names = [unit.name for unit in units if unit.enable_now]
    if not names:
        return {"status": "not-requested", "actualRegistered": False, "units": []}
    if platform.system() != "Linux" or not _systemctl_binary():
        return {"status": "unsupported", "actualRegistered": None, "units": names}
    records = []
    for name in names:
        enabled = _run_systemctl(("is-enabled", name), runner=runner, allow_status={0, 1, 3, 4})
        active = _run_systemctl(("is-active", name), runner=runner, allow_status={0, 1, 3, 4})
        records.append(
            {
                "name": name,
                "enabled": enabled.returncode == 0,
                "active": active.returncode == 0,
            }
        )
    return {
        "status": "registered" if all(item["enabled"] and item["active"] for item in records) else "not-registered",
        "actualRegistered": all(item["enabled"] and item["active"] for item in records),
        "units": records,
    }


def _snapshot_unit_states(names: Iterable[str], *, runner: Runner) -> dict[str, dict[str, bool]]:
    states: dict[str, dict[str, bool]] = {}
    for name in names:
        enabled = _run_systemctl(("is-enabled", name), runner=runner, allow_status={0, 1, 3, 4})
        active = _run_systemctl(("is-active", name), runner=runner, allow_status={0, 1, 3, 4})
        states[name] = {"enabled": enabled.returncode == 0, "active": active.returncode == 0}
    return states


def _restore_unit_states(states: dict[str, dict[str, bool]], *, runner: Runner) -> None:
    names = list(states)
    if names:
        try:
            _run_systemctl(("disable", "--now", *names), runner=runner, allow_status={0, 1, 3, 4, 5})
        except Exception:
            pass
    for name, state in states.items():
        try:
            if state["enabled"] and state["active"]:
                _run_systemctl(("enable", "--now", name), runner=runner)
            elif state["enabled"]:
                _run_systemctl(("enable", name), runner=runner)
            elif state["active"]:
                _run_systemctl(("start", name), runner=runner)
        except Exception:
            pass


def linger_status(*, runner: Runner = subprocess.run) -> dict:
    loginctl = shutil.which("loginctl")
    if platform.system() != "Linux" or not loginctl:
        return {"status": "unknown", "enabled": None, "changed": False}
    result = runner(
        [loginctl, "show-user", str(os.getuid()), "--property=Linger", "--value"],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    value = result.stdout.strip().lower() if result.returncode == 0 else ""
    enabled = value == "yes" if value in {"yes", "no"} else None
    return {
        "status": "enabled" if enabled is True else "disabled" if enabled is False else "unknown",
        "enabled": enabled,
        "changed": False,
        "note": "Actanara changes linger only after explicit user authorization.",
    }


def enable_linger(*, runner: Runner = subprocess.run) -> dict:
    """Enable linger for the current user without invoking sudo.

    Linger is shared user-level host state rather than an Actanara-owned
    resource.  Callers must obtain explicit operator authorization before
    crossing this boundary, and uninstall workflows must never disable it.
    """

    if platform.system() != "Linux" and os.environ.get("ACTANARA_INSTALL_TEST_MODE") != "1":
        raise SystemdUserError("systemd linger is only supported on Linux")
    loginctl = shutil.which("loginctl")
    if not loginctl:
        raise SystemdUserError("loginctl is unavailable; linger could not be enabled")
    before = linger_status(runner=runner)
    if before.get("enabled") is True:
        return {
            **before,
            "action": "already-enabled",
            "authorization": "explicit-user-choice",
        }
    command = [loginctl, "enable-linger", str(os.getuid())]
    try:
        result = runner(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemdUserError("loginctl could not request linger for the current user") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")
        if len(detail) > 500:
            detail = detail[:497] + "..."
        suffix = f": {detail}" if detail else ""
        raise SystemdUserError(
            f"loginctl enable-linger failed with status {result.returncode}{suffix}"
        )
    after = linger_status(runner=runner)
    if after.get("enabled") is not True:
        raise SystemdUserError("loginctl returned success but linger is not enabled")
    return {
        **after,
        "changed": True,
        "action": "enabled",
        "authorization": "explicit-user-choice",
    }


def install_user_units(
    paths: RuntimePaths,
    units: Iterable[UserUnit],
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
) -> dict:
    if platform.system() != "Linux" and os.environ.get("ACTANARA_INSTALL_TEST_MODE") != "1":
        raise SystemdUserError("systemd user units are only supported on Linux")
    selected_units = list(units)
    if not selected_units or len({unit.name for unit in selected_units}) != len(selected_units):
        raise SystemdUserError("systemd unit set is empty or duplicated")
    root = unit_dir or default_user_unit_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    backup_root = paths.state_dir / "backups" / "systemd" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    written: list[Path] = []
    backups: dict[Path, Path] = {}
    enabled_names = [unit.name for unit in selected_units if unit.enable_now]
    prior_states = _snapshot_unit_states(enabled_names, runner=runner)
    try:
        for unit in selected_units:
            if not UNIT_NAME_RE.fullmatch(unit.name):
                raise SystemdUserError("systemd unit name is unsafe")
            target = root / unit.name
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise SystemdUserError(f"systemd unit target is unsafe: {unit.name}")
            if target.exists():
                backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                backup = backup_root / unit.name
                shutil.copy2(target, backup, follow_symlinks=False)
                backup.chmod(0o600)
                backups[target] = backup
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{unit.name}.", dir=root)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(unit.content)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.chmod(0o600)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            written.append(target)
        _run_systemctl(("daemon-reload",), runner=runner)
        if enabled_names:
            _run_systemctl(("enable", "--now", *enabled_names), runner=runner)
        probe = probe_user_units(selected_units, runner=runner)
        if enabled_names and probe.get("actualRegistered") is not True:
            raise SystemdUserError("systemd user units did not become enabled and active")
    except Exception:
        for target in reversed(written):
            backup = backups.get(target)
            if backup is not None and backup.is_file():
                shutil.copy2(backup, target, follow_symlinks=False)
                target.chmod(0o600)
            else:
                target.unlink(missing_ok=True)
        try:
            _run_systemctl(("daemon-reload",), runner=runner)
        except Exception:
            pass
        _restore_unit_states(prior_states, runner=runner)
        raise
    return {
        "status": "installed",
        "provider": "systemd-user",
        "unitDirectory": str(root),
        "units": [unit.name for unit in selected_units],
        "enabledUnits": [unit.name for unit in selected_units if unit.enable_now],
        "backupDirectory": str(backup_root) if backup_root.exists() else None,
        "probe": probe,
        "linger": linger_status(runner=runner),
    }


def uninstall_user_units(
    paths: RuntimePaths,
    units: Iterable[UserUnit],
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
) -> dict:
    if platform.system() != "Linux" and os.environ.get("ACTANARA_INSTALL_TEST_MODE") != "1":
        raise SystemdUserError("systemd user units are only supported on Linux")
    selected_units = list(units)
    if not selected_units or len({unit.name for unit in selected_units}) != len(selected_units):
        raise SystemdUserError("systemd unit set is empty or duplicated")
    root = unit_dir or default_user_unit_dir()
    names = [unit.name for unit in selected_units]
    targets: list[Path] = []
    backup_root = paths.state_dir / "backups" / "systemd" / (
        datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-remove"
    )
    backups: dict[Path, Path] = {}
    for unit in selected_units:
        if not UNIT_NAME_RE.fullmatch(unit.name):
            raise SystemdUserError("systemd unit name is unsafe")
        target = root / unit.name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise SystemdUserError(f"systemd unit target is unsafe: {unit.name}")
        if not target.exists():
            continue
        try:
            with target.open("r", encoding="utf-8") as handle:
                first_line = handle.readline().rstrip("\r\n")
        except OSError as exc:
            raise SystemdUserError(f"systemd unit target is unreadable: {unit.name}") from exc
        if first_line != MANAGED_UNIT_HEADER:
            raise SystemdUserError(f"refusing to remove an unmanaged systemd unit: {unit.name}")
        targets.append(target)

    prior_states = _snapshot_unit_states(names, runner=runner)
    try:
        for target in targets:
            backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup = backup_root / target.name
            shutil.copy2(target, backup, follow_symlinks=False)
            backup.chmod(0o600)
            backups[target] = backup
        _run_systemctl(("disable", "--now", *names), runner=runner, allow_status={0, 1, 3, 4, 5})
        for target in targets:
            target.unlink()
        _run_systemctl(("daemon-reload",), runner=runner)
        remaining = _snapshot_unit_states(names, runner=runner)
        if any(state["enabled"] or state["active"] for state in remaining.values()):
            raise SystemdUserError("systemd user units remained enabled or active after removal")
    except Exception:
        for target, backup in backups.items():
            if backup.is_file():
                shutil.copy2(backup, target, follow_symlinks=False)
                target.chmod(0o600)
        try:
            _run_systemctl(("daemon-reload",), runner=runner)
        except Exception:
            pass
        _restore_unit_states(prior_states, runner=runner)
        raise
    return {
        "status": "uninstalled",
        "provider": "systemd-user",
        "unitDirectory": str(root),
        "units": names,
        "removedUnits": [target.name for target in targets],
        "backupDirectory": str(backup_root) if backup_root.exists() else None,
        "probe": {
            "status": "not-registered",
            "actualRegistered": False,
            "units": [
                {"name": name, **remaining[name]}
                for name in names
            ],
        },
        "linger": linger_status(runner=runner),
    }

"""Linux systemd user-unit rendering, registration, and read-only probes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .paths import RuntimePaths


UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,126}$")
MANAGED_UNIT_HEADER = "# Managed by Actanara. Do not edit by hand."
Runner = Callable[..., subprocess.CompletedProcess[str]]
SYSTEMD_STATE_SETTLE_ATTEMPTS = 10
SYSTEMD_STATE_STABLE_SAMPLES = 3
SYSTEMD_STATE_SETTLE_INTERVAL_SECONDS = 0.1


class SystemdUserError(RuntimeError):
    pass


@dataclass(frozen=True)
class UserUnit:
    name: str
    content: str
    enable_now: bool = True


def systemd_transaction_checkpoint(phase: str, transaction_id: str) -> None:
    """No-op checkpoint patched by interruption-window tests."""


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


def _wait_for_registered_user_units(
    units: Iterable[UserUnit],
    *,
    runner: Runner,
) -> dict[str, Any]:
    """Require enabled units to remain active across a bounded settle window."""

    selected_units = tuple(units)
    stable_samples = 0
    probe: dict[str, Any] = {}
    for attempt in range(SYSTEMD_STATE_SETTLE_ATTEMPTS):
        probe = probe_user_units(selected_units, runner=runner)
        if probe.get("actualRegistered") is True:
            stable_samples += 1
            if stable_samples >= SYSTEMD_STATE_STABLE_SAMPLES:
                return probe
        else:
            stable_samples = 0
        if attempt + 1 < SYSTEMD_STATE_SETTLE_ATTEMPTS:
            time.sleep(SYSTEMD_STATE_SETTLE_INTERVAL_SECONDS)
    return probe


def inspect_user_units(
    units: Iterable[UserUnit],
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Inspect runtime state and persistent definition alignment without writes."""

    selected_units = _validated_units(units)
    root = unit_dir or default_user_unit_dir()
    runtime_supported = platform.system() == "Linux" and bool(_systemctl_binary())
    records: list[dict[str, Any]] = []
    for unit in selected_units:
        target = root / unit.name
        file_state = _unit_file_state(target, expected=unit.content)
        enabled: bool | None = None
        active: bool | None = None
        if runtime_supported:
            state = _snapshot_unit_states((unit.name,), runner=runner)[unit.name]
            enabled = state["enabled"]
            active = state["active"]
        records.append(
            {
                "name": unit.name,
                "path": str(target),
                "enableNow": unit.enable_now,
                "enabled": enabled,
                "active": active,
                **file_state,
            }
        )
    managed = all(item["managed"] is True for item in records)
    definitions_aligned = all(item["aligned"] is True for item in records)
    enabled_records = [item for item in records if item["enableNow"]]
    actual_enabled = (
        all(item["enabled"] is True for item in enabled_records)
        if runtime_supported and enabled_records
        else None
    )
    actual_active = (
        all(item["active"] is True for item in enabled_records)
        if runtime_supported and enabled_records
        else None
    )
    return {
        "provider": "systemd-user",
        "supported": runtime_supported,
        "unitDirectory": str(root),
        "units": records,
        "definitionsPresent": all(item["exists"] is True for item in records),
        "definitionsManaged": managed,
        "definitionsAligned": definitions_aligned,
        "actualEnabled": actual_enabled,
        "actualActive": actual_active,
        "actualRegistered": (
            actual_enabled and actual_active
            if actual_enabled is not None and actual_active is not None
            else None
        ),
    }


def control_user_units(
    paths: RuntimePaths,
    units: Iterable[UserUnit],
    action: str,
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Start, stop, or restart installed Actanara-managed user units."""

    _require_linux()
    if action not in {"start", "stop", "restart"}:
        raise SystemdUserError("systemd user-unit action must be start, stop, or restart")
    selected_units = _validated_units(units)
    root = unit_dir or default_user_unit_dir()
    names = [unit.name for unit in selected_units if unit.enable_now]
    if not names:
        raise SystemdUserError("systemd user-unit action has no runnable units")
    recovery = recover_user_unit_transactions(paths, runner=runner)
    blocked = next((item for item in recovery if item.get("status") == "conflict"), None)
    if blocked:
        raise SystemdUserError("systemd transaction recovery is blocked by a state conflict")
    for unit in selected_units:
        target = root / unit.name
        state = _unit_file_state(target, expected=unit.content)
        if not state["exists"]:
            raise SystemdUserError(f"systemd unit is not installed: {unit.name}")
        if not state["managed"]:
            raise SystemdUserError(f"refusing to control an unmanaged systemd unit: {unit.name}")
        if action in {"start", "restart"} and not state["aligned"]:
            raise SystemdUserError(
                f"systemd unit definition must be reconciled before {action}: {unit.name}"
            )
    _run_systemctl((action, *names), runner=runner)
    expected_active = action != "stop"
    states = _wait_for_active_unit_states(
        names,
        expected_active=expected_active,
        runner=runner,
    )
    if any(state["active"] is not expected_active for state in states.values()):
        raise SystemdUserError(f"systemd user units did not reach the requested {action} state")
    return {
        "status": "stopped" if action == "stop" else "running",
        "action": action,
        "provider": "systemd-user",
        "units": names,
        "states": [{"name": name, **states[name]} for name in names],
        "recoveredTransactions": [item.get("id") for item in recovery],
        "linger": linger_status(runner=runner),
    }


def _snapshot_unit_states(names: Iterable[str], *, runner: Runner) -> dict[str, dict[str, bool]]:
    states: dict[str, dict[str, bool]] = {}
    for name in names:
        enabled = _run_systemctl(("is-enabled", name), runner=runner, allow_status={0, 1, 3, 4})
        active = _run_systemctl(("is-active", name), runner=runner, allow_status={0, 1, 3, 4})
        states[name] = {"enabled": enabled.returncode == 0, "active": active.returncode == 0}
    return states


def _wait_for_active_unit_states(
    names: Iterable[str],
    *,
    expected_active: bool,
    runner: Runner,
) -> dict[str, dict[str, bool]]:
    selected_names = tuple(names)
    stable_samples = 0
    states: dict[str, dict[str, bool]] = {}
    for attempt in range(SYSTEMD_STATE_SETTLE_ATTEMPTS):
        states = _snapshot_unit_states(selected_names, runner=runner)
        if all(state["active"] is expected_active for state in states.values()):
            stable_samples += 1
            if stable_samples >= SYSTEMD_STATE_STABLE_SAMPLES:
                return states
        else:
            stable_samples = 0
        if attempt + 1 < SYSTEMD_STATE_SETTLE_ATTEMPTS:
            time.sleep(SYSTEMD_STATE_SETTLE_INTERVAL_SECONDS)
    return states


def _restore_unit_states(states: dict[str, dict[str, bool]], *, runner: Runner) -> None:
    names = list(states)
    if names:
        try:
            _run_systemctl(("disable", "--now", *names), runner=runner, allow_status={0, 1, 3, 4, 5})
        except Exception:
            pass
    if names:
        try:
            _run_systemctl(("reset-failed", *names), runner=runner, allow_status={0, 1, 3, 4, 5})
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


def _require_linux() -> None:
    if platform.system() != "Linux" and os.environ.get("ACTANARA_INSTALL_TEST_MODE") != "1":
        raise SystemdUserError("systemd user units are only supported on Linux")


def _validated_units(units: Iterable[UserUnit]) -> list[UserUnit]:
    selected = list(units)
    if not selected or len({unit.name for unit in selected}) != len(selected):
        raise SystemdUserError("systemd unit set is empty or duplicated")
    if any(not UNIT_NAME_RE.fullmatch(unit.name) for unit in selected):
        raise SystemdUserError("systemd unit name is unsafe")
    return selected


def _unit_file_state(target: Path, *, expected: str | None = None) -> dict[str, Any]:
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise SystemdUserError(f"systemd unit target is unsafe: {target.name}")
    try:
        content = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"exists": False, "managed": False, "aligned": False, "definitionHash": None}
    except (OSError, UnicodeError) as exc:
        raise SystemdUserError(f"systemd unit target is unreadable: {target.name}") from exc
    managed = content.splitlines()[0] == MANAGED_UNIT_HEADER if content.splitlines() else False
    return {
        "exists": True,
        "managed": managed,
        "aligned": content == expected if expected is not None else None,
        "definitionHash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def _read_optional_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _bytes_hash(content: bytes | None) -> str:
    return "missing" if content is None else hashlib.sha256(content).hexdigest()


def _resource_hash(path: Path) -> str:
    return _bytes_hash(_read_optional_bytes(path))


def _systemd_transaction_root(paths: RuntimePaths) -> Path:
    return paths.state_dir / "systemd-transactions"


@contextmanager
def _systemd_transaction_lock(paths: RuntimePaths):
    root = _systemd_transaction_root(paths)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    with (root / ".lock").open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _write_systemd_journal(transaction_dir: Path, journal: dict[str, Any]) -> None:
    _atomic_write_bytes(
        transaction_dir / "journal.json",
        (json.dumps(journal, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def _read_systemd_journal(transaction_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads((transaction_dir / "journal.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _advance_systemd_transaction(
    transaction_dir: Path,
    journal: dict[str, Any],
    phase: str,
    *,
    status: str | None = None,
) -> None:
    journal["phase"] = phase
    if status is not None:
        journal["status"] = status
    _write_systemd_journal(transaction_dir, journal)


def _begin_systemd_transaction(
    paths: RuntimePaths,
    *,
    action: str,
    units: list[UserUnit],
    unit_dir: Path,
    prior_states: dict[str, dict[str, bool]],
    transaction_context: dict[str, str] | None,
) -> tuple[Path, dict[str, Any]]:
    transaction_id = uuid.uuid4().hex
    transaction_dir = _systemd_transaction_root(paths) / transaction_id
    transaction_dir.mkdir(parents=True, mode=0o700)
    transaction_dir.chmod(0o700)
    records: list[dict[str, Any]] = []
    for unit in units:
        target = unit_dir / unit.name
        before = _read_optional_bytes(target)
        if before is not None:
            _atomic_write_bytes(transaction_dir / f"{unit.name}.before", before)
        desired = unit.content.encode("utf-8") if action == "install" else None
        records.append(
            {
                "name": unit.name,
                "enableNow": unit.enable_now,
                "beforeExists": before is not None,
                "beforeHash": _bytes_hash(before),
                "afterHash": _bytes_hash(desired),
                "beforeMode": (target.stat().st_mode & 0o777) if before is not None else None,
                "priorState": prior_states[unit.name],
            }
        )
    context = transaction_context if isinstance(transaction_context, dict) else {}
    journal = {
        "schemaVersion": 1,
        "id": transaction_id,
        "status": "active",
        "phase": "prior-captured",
        "action": action,
        "provider": "systemd-user",
        "unitDirectory": str(unit_dir),
        "settingsBeforeHash": context.get("settingsBeforeHash"),
        "settingsAfterHash": context.get("settingsAfterHash"),
        "units": records,
    }
    _write_systemd_journal(transaction_dir, journal)
    systemd_transaction_checkpoint("after-prior-captured", transaction_id)
    return transaction_dir, journal


def _transaction_targets(journal: dict[str, Any]) -> tuple[Path, list[dict[str, Any]]]:
    root = Path(str(journal.get("unitDirectory") or ""))
    if not root.is_absolute():
        raise SystemdUserError("systemd transaction unit directory is unsafe")
    records = journal.get("units") if isinstance(journal.get("units"), list) else []
    if not records:
        raise SystemdUserError("systemd transaction has no units")
    for record in records:
        if not isinstance(record, dict) or not UNIT_NAME_RE.fullmatch(str(record.get("name") or "")):
            raise SystemdUserError("systemd transaction unit name is unsafe")
    return root, records


def _transaction_has_conflict(journal: dict[str, Any]) -> bool:
    root, records = _transaction_targets(journal)
    return any(
        _resource_hash(root / str(record["name"]))
        not in {record.get("beforeHash"), record.get("afterHash")}
        for record in records
    )


def _restore_systemd_transaction(
    transaction_dir: Path,
    journal: dict[str, Any],
    *,
    runner: Runner,
) -> None:
    if _transaction_has_conflict(journal):
        raise SystemdUserError("systemd transaction recovery found a definition conflict")
    root, records = _transaction_targets(journal)
    states: dict[str, dict[str, bool]] = {}
    for record in records:
        name = str(record["name"])
        target = root / name
        if bool(record.get("beforeExists")):
            snapshot = transaction_dir / f"{name}.before"
            if not snapshot.is_file():
                raise SystemdUserError("systemd transaction snapshot is missing")
            _atomic_write_bytes(target, snapshot.read_bytes())
            mode = record.get("beforeMode")
            if isinstance(mode, int):
                target.chmod(mode)
        else:
            target.unlink(missing_ok=True)
        prior = record.get("priorState") if isinstance(record.get("priorState"), dict) else {}
        states[name] = {"enabled": bool(prior.get("enabled")), "active": bool(prior.get("active"))}
    _run_systemctl(("daemon-reload",), runner=runner)
    _restore_unit_states(states, runner=runner)
    restored_states = _snapshot_unit_states(states, runner=runner)
    if restored_states != states:
        raise SystemdUserError("systemd transaction could not restore prior runtime state")


def _desired_systemd_transaction_matches(journal: dict[str, Any], *, runner: Runner) -> bool:
    root, records = _transaction_targets(journal)
    if any(_resource_hash(root / str(record["name"])) != record.get("afterHash") for record in records):
        return False
    states = _snapshot_unit_states((str(record["name"]) for record in records), runner=runner)
    if journal.get("action") == "uninstall":
        return all(not state["enabled"] and not state["active"] for state in states.values())
    return all(
        not bool(record.get("enableNow"))
        or (states[str(record["name"])]["enabled"] and states[str(record["name"])]["active"])
        for record in records
    )


def recover_user_unit_transactions(
    paths: RuntimePaths,
    *,
    runner: Runner = subprocess.run,
) -> list[dict[str, Any]]:
    """Recover interrupted systemd mutations without guessing across conflicts."""

    root = _systemd_transaction_root(paths)
    if not root.exists():
        return []
    results: list[dict[str, Any]] = []
    with _systemd_transaction_lock(paths):
        for transaction_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            journal = _read_systemd_journal(transaction_dir)
            if not journal:
                results.append({"id": transaction_dir.name, "status": "conflict", "phase": "journal-unreadable"})
                continue
            if journal.get("status") in {"committed", "compensated"}:
                continue
            transaction_id = str(journal.get("id") or transaction_dir.name)
            try:
                definition_conflict = _transaction_has_conflict(journal)
            except Exception:
                definition_conflict = True
            if definition_conflict:
                _advance_systemd_transaction(transaction_dir, journal, "recovery-conflict", status="conflict")
                results.append({"id": transaction_id, "status": "conflict", "phase": "definition-conflict"})
                continue
            settings_before = journal.get("settingsBeforeHash")
            settings_after = journal.get("settingsAfterHash")
            settings_hash = _resource_hash(paths.config_dir / "settings.json")
            if settings_after and settings_hash == settings_after:
                if _desired_systemd_transaction_matches(journal, runner=runner):
                    _advance_systemd_transaction(transaction_dir, journal, "recovered-committed", status="committed")
                    results.append({"id": transaction_id, "status": "committed", "phase": "recovered-committed"})
                else:
                    _advance_systemd_transaction(transaction_dir, journal, "desired-state-conflict", status="conflict")
                    results.append({"id": transaction_id, "status": "conflict", "phase": "desired-state-conflict"})
                continue
            if settings_before and settings_hash != settings_before:
                _advance_systemd_transaction(transaction_dir, journal, "settings-cas-conflict", status="conflict")
                results.append({"id": transaction_id, "status": "conflict", "phase": "settings-cas-conflict"})
                continue
            try:
                _restore_systemd_transaction(transaction_dir, journal, runner=runner)
            except Exception:
                _advance_systemd_transaction(transaction_dir, journal, "recovery-incomplete", status="conflict")
                results.append({"id": transaction_id, "status": "conflict", "phase": "recovery-incomplete"})
            else:
                _advance_systemd_transaction(transaction_dir, journal, "recovered-prior", status="compensated")
                results.append({"id": transaction_id, "status": "compensated", "phase": "recovered-prior"})
    return results


def finalize_user_unit_transaction(
    paths: RuntimePaths,
    transaction_id: str,
    *,
    runner: Runner = subprocess.run,
) -> None:
    transaction_dir = _systemd_transaction_root(paths) / transaction_id
    journal = _read_systemd_journal(transaction_dir)
    if not journal or str(journal.get("id")) != transaction_id:
        raise SystemdUserError("systemd transaction journal is unavailable")
    if not _desired_systemd_transaction_matches(journal, runner=runner):
        raise SystemdUserError("systemd transaction desired state is no longer aligned")
    _advance_systemd_transaction(transaction_dir, journal, "committed", status="committed")


def rollback_user_unit_transaction(
    paths: RuntimePaths,
    transaction_id: str,
    *,
    runner: Runner = subprocess.run,
) -> None:
    transaction_dir = _systemd_transaction_root(paths) / transaction_id
    journal = _read_systemd_journal(transaction_dir)
    if not journal or str(journal.get("id")) != transaction_id:
        raise SystemdUserError("systemd transaction journal is unavailable")
    _restore_systemd_transaction(transaction_dir, journal, runner=runner)
    _advance_systemd_transaction(transaction_dir, journal, "compensated", status="compensated")


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
    restart_active: bool = True,
    defer_commit: bool = False,
    transaction_context: dict[str, str] | None = None,
    recover_transactions: bool = True,
) -> dict:
    _require_linux()
    selected_units = _validated_units(units)
    root = unit_dir or default_user_unit_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    recovery = recover_user_unit_transactions(paths, runner=runner) if recover_transactions else []
    if any(item.get("status") == "conflict" for item in recovery):
        raise SystemdUserError("systemd transaction recovery is blocked by a state conflict")
    prior_content: dict[str, bytes | None] = {}
    for unit in selected_units:
        target = root / unit.name
        state = _unit_file_state(target, expected=unit.content)
        if state["exists"] and not state["managed"]:
            raise SystemdUserError(f"refusing to replace an unmanaged systemd unit: {unit.name}")
        prior_content[unit.name] = _read_optional_bytes(target)
    backup_root = paths.state_dir / "backups" / "systemd" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    enabled_names = [unit.name for unit in selected_units if unit.enable_now]
    names = [unit.name for unit in selected_units]
    prior_states = _snapshot_unit_states(names, runner=runner)
    changed_names = [
        unit.name
        for unit in selected_units
        if prior_content[unit.name] != unit.content.encode("utf-8")
    ]
    transaction_dir, journal = _begin_systemd_transaction(
        paths,
        action="install",
        units=selected_units,
        unit_dir=root,
        prior_states=prior_states,
        transaction_context=transaction_context,
    )
    try:
        for unit in selected_units:
            target = root / unit.name
            if target.exists():
                backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                backup = backup_root / unit.name
                shutil.copy2(target, backup, follow_symlinks=False)
                backup.chmod(0o600)
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
        _advance_systemd_transaction(transaction_dir, journal, "definitions-applied")
        systemd_transaction_checkpoint("after-definitions-applied", str(journal["id"]))
        _run_systemctl(("daemon-reload",), runner=runner)
        if enabled_names:
            _run_systemctl(("enable", "--now", *enabled_names), runner=runner)
        restarted_names = [
            name
            for name in enabled_names
            if restart_active and prior_states[name]["active"]
        ]
        if restarted_names:
            _run_systemctl(("restart", *restarted_names), runner=runner)
        _advance_systemd_transaction(transaction_dir, journal, "external-applied")
        systemd_transaction_checkpoint("after-external-applied", str(journal["id"]))
        probe = _wait_for_registered_user_units(selected_units, runner=runner)
        if enabled_names and probe.get("actualRegistered") is not True:
            raise SystemdUserError("systemd user units did not become enabled and active")
        alignment = inspect_user_units(selected_units, unit_dir=root, runner=runner)
        if alignment.get("definitionsAligned") is not True:
            raise SystemdUserError("systemd user-unit definitions are not aligned after install")
        _advance_systemd_transaction(transaction_dir, journal, "external-verified")
        systemd_transaction_checkpoint("after-external-verified", str(journal["id"]))
        if not defer_commit:
            _advance_systemd_transaction(transaction_dir, journal, "committed", status="committed")
    except Exception:
        try:
            _restore_systemd_transaction(transaction_dir, journal, runner=runner)
        except Exception:
            _advance_systemd_transaction(
                transaction_dir,
                journal,
                "compensation-incomplete",
                status="conflict",
            )
        else:
            _advance_systemd_transaction(transaction_dir, journal, "compensated", status="compensated")
        raise
    return {
        "status": "installed",
        "provider": "systemd-user",
        "unitDirectory": str(root),
        "units": [unit.name for unit in selected_units],
        "enabledUnits": [unit.name for unit in selected_units if unit.enable_now],
        "changedUnits": changed_names,
        "restartedUnits": restarted_names,
        "backupDirectory": str(backup_root) if backup_root.exists() else None,
        "probe": probe,
        "alignment": alignment,
        "transactionId": str(journal["id"]),
        "transactionStatus": "pending-settings" if defer_commit else "committed",
        "recoveredTransactions": [item.get("id") for item in recovery],
        "linger": linger_status(runner=runner),
    }


def uninstall_user_units(
    paths: RuntimePaths,
    units: Iterable[UserUnit],
    *,
    unit_dir: Path | None = None,
    runner: Runner = subprocess.run,
    defer_commit: bool = False,
    transaction_context: dict[str, str] | None = None,
    recover_transactions: bool = True,
) -> dict:
    _require_linux()
    selected_units = _validated_units(units)
    root = unit_dir or default_user_unit_dir()
    recovery = recover_user_unit_transactions(paths, runner=runner) if recover_transactions else []
    if any(item.get("status") == "conflict" for item in recovery):
        raise SystemdUserError("systemd transaction recovery is blocked by a state conflict")
    names = [unit.name for unit in selected_units]
    targets: list[Path] = []
    backup_root = paths.state_dir / "backups" / "systemd" / (
        datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-remove"
    )
    for unit in selected_units:
        target = root / unit.name
        state = _unit_file_state(target)
        if not state["exists"]:
            continue
        if not state["managed"]:
            raise SystemdUserError(f"refusing to remove an unmanaged systemd unit: {unit.name}")
        targets.append(target)

    prior_states = _snapshot_unit_states(names, runner=runner)
    transaction_dir, journal = _begin_systemd_transaction(
        paths,
        action="uninstall",
        units=selected_units,
        unit_dir=root,
        prior_states=prior_states,
        transaction_context=transaction_context,
    )
    try:
        for target in targets:
            backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup = backup_root / target.name
            shutil.copy2(target, backup, follow_symlinks=False)
            backup.chmod(0o600)
        _run_systemctl(("disable", "--now", *names), runner=runner, allow_status={0, 1, 3, 4, 5})
        for target in targets:
            target.unlink()
        _advance_systemd_transaction(transaction_dir, journal, "definitions-removed")
        systemd_transaction_checkpoint("after-definitions-removed", str(journal["id"]))
        _run_systemctl(("daemon-reload",), runner=runner)
        _run_systemctl(("reset-failed", *names), runner=runner, allow_status={0, 1, 3, 4, 5})
        _advance_systemd_transaction(transaction_dir, journal, "external-applied")
        systemd_transaction_checkpoint("after-external-applied", str(journal["id"]))
        remaining = _snapshot_unit_states(names, runner=runner)
        if any(state["enabled"] or state["active"] for state in remaining.values()):
            raise SystemdUserError("systemd user units remained enabled or active after removal")
        if any((root / name).exists() for name in names):
            raise SystemdUserError("systemd user-unit definitions remained after removal")
        _advance_systemd_transaction(transaction_dir, journal, "external-verified")
        systemd_transaction_checkpoint("after-external-verified", str(journal["id"]))
        if not defer_commit:
            _advance_systemd_transaction(transaction_dir, journal, "committed", status="committed")
    except Exception:
        try:
            _restore_systemd_transaction(transaction_dir, journal, runner=runner)
        except Exception:
            _advance_systemd_transaction(
                transaction_dir,
                journal,
                "compensation-incomplete",
                status="conflict",
            )
        else:
            _advance_systemd_transaction(transaction_dir, journal, "compensated", status="compensated")
        raise
    return {
        "status": "uninstalled",
        "provider": "systemd-user",
        "unitDirectory": str(root),
        "units": names,
        "removedUnits": [target.name for target in targets],
        "backupDirectory": str(backup_root) if backup_root.exists() else None,
        "transactionId": str(journal["id"]),
        "transactionStatus": "pending-settings" if defer_commit else "committed",
        "recoveredTransactions": [item.get("id") for item in recovery],
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

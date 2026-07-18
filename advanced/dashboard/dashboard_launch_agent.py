#!/usr/bin/env python3
"""Install or check launchd agents for the Actanara Dashboard."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import shlex
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path


sys.dont_write_bytecode = True


MODULE_PROJECT_ROOT = Path(__file__).absolute().parents[2]
sys.path.insert(0, str(MODULE_PROJECT_ROOT))
sys.path.insert(0, str(MODULE_PROJECT_ROOT / "src"))

DEFAULT_ACTANARA_HOME = Path.home() / ".actanara"
DEFAULT_PROJECT_ROOT = DEFAULT_ACTANARA_HOME / "app" / "source"
DEFAULT_PYTHON = DEFAULT_ACTANARA_HOME / ".venv" / "bin" / "python"
DEFAULT_SERVICE_LABEL = "com.actanara.dashboard"
DEFAULT_WATCHDOG_LABEL = "com.actanara.dashboard.watchdog"
DEFAULT_PORT = 3036
_MAX_SETTINGS_BYTES = 2 * 1024 * 1024


class ManagedRuntimeConfigurationError(RuntimeError):
    """Raised when managed service defaults cannot be tied to one Runtime."""


def _require_selected_runtime(selected) -> None:
    explicit_home = os.environ.get("ACTANARA_HOME")
    if explicit_home:
        expected = Path(explicit_home).expanduser().absolute()
        if selected.home != expected:
            raise ManagedRuntimeConfigurationError(
                "selected Runtime does not match the explicit ACTANARA_HOME"
            )

    settings_path = selected.config_dir / "settings.json"
    try:
        details = settings_path.lstat()
    except FileNotFoundError:
        details = None
    except OSError as exc:
        raise ManagedRuntimeConfigurationError("Runtime settings are unreadable") from exc
    if details is not None:
        try:
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise ManagedRuntimeConfigurationError("Runtime settings must be a regular file")
            if details.st_size > _MAX_SETTINGS_BYTES:
                raise ManagedRuntimeConfigurationError("Runtime settings exceed the safe read limit")
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
        except ManagedRuntimeConfigurationError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManagedRuntimeConfigurationError("Runtime settings are unreadable") from exc
        if (
            not isinstance(payload, dict)
            or type(payload.get("schemaVersion")) is not int
            or payload.get("schemaVersion") != 1
        ):
            raise ManagedRuntimeConfigurationError("Runtime settings have an unsupported schema")
    _require_runtime_pointers(selected.home)


def _require_runtime_pointers(actanara_home: Path) -> None:
    pointers = (
        (actanara_home / "app" / "source", actanara_home / "app" / "releases"),
        (actanara_home / ".venv", actanara_home / "app" / "venvs"),
    )
    for pointer, container in pointers:
        try:
            if not pointer.is_symlink():
                raise ManagedRuntimeConfigurationError("managed Runtime pointer is unavailable")
            target = Path(os.readlink(pointer))
            if target.is_absolute():
                raise ManagedRuntimeConfigurationError("managed Runtime pointer must be relative")
            resolved = pointer.resolve(strict=True)
            expected_container = container.resolve(strict=True)
        except ManagedRuntimeConfigurationError:
            raise
        except (OSError, RuntimeError) as exc:
            raise ManagedRuntimeConfigurationError("managed Runtime pointer is unreadable") from exc
        if resolved.parent != expected_container or not resolved.is_dir():
            raise ManagedRuntimeConfigurationError("managed Runtime pointer target is outside its store")
    if not (actanara_home / ".venv" / "bin" / "python").is_file():
        raise ManagedRuntimeConfigurationError("managed Runtime Python is unavailable")


def _require_stable_runtime_binding(*, project_root: Path, python: Path, actanara_home: Path) -> None:
    expected_source = actanara_home / "app" / "source"
    expected_python = actanara_home / ".venv" / "bin" / "python"
    if project_root != expected_source or python != expected_python:
        raise ManagedRuntimeConfigurationError(
            "managed service writes require stable Runtime source and venv paths"
        )
    _require_runtime_pointers(actanara_home)


def dashboard_launch_defaults() -> dict:
    try:
        from data_foundation.paths import load_paths
        from data_foundation.settings import resolve_dashboard_settings

        selected = load_paths()
        _require_selected_runtime(selected)
        settings = resolve_dashboard_settings(selected)
        return {
            "project_root": selected.home / "app" / "source",
            "python": selected.home / ".venv" / "bin" / "python",
            "actanara_home": selected.home,
            "host": str(settings["host"]),
            "port": int(settings["port"]),
            "url": str(settings["url"]),
            "logs_dir": Path(settings["logsDir"]),
            "label": str(settings["serviceLabel"]),
            "watchdog_label": str(settings["watchdogLabel"]),
        }
    except ManagedRuntimeConfigurationError:
        raise
    except Exception as exc:
        raise ManagedRuntimeConfigurationError(
            "managed Dashboard defaults could not be read from the selected Runtime"
        ) from exc


def launch_agents_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents"


def service_plist_path(label: str, home: Path | None = None) -> Path:
    return launch_agents_dir(home) / f"{label}.plist"


def watchdog_plist_path(label: str, home: Path | None = None) -> Path:
    return launch_agents_dir(home) / f"{label}.plist"


def build_service_plist(
    *,
    label: str,
    python: Path,
    project_root: Path,
    actanara_home: Path,
    host: str,
    port: int,
    foundation: bool,
    logs_dir: Path | None = None,
) -> dict:
    env = {
        "ACTANARA_DASHBOARD_PROJECT_ROOT": str(project_root),
        "ACTANARA_DASHBOARD_PYTHON": str(python),
        "ACTANARA_DASHBOARD_HOST": host,
        "ACTANARA_DASHBOARD_PORT": str(port),
        "ACTANARA_HOME": str(actanara_home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": f"{project_root}:{project_root / 'src'}:{project_root / 'src' / 'dashboard'}",
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    }
    if foundation:
        env.update(
            {
                "ACTANARA_DATA_FOUNDATION_ENABLED": "true",
                "DASHBOARD_READ_SOURCE": "foundation",
                "REPORT_READ_SOURCE": "foundation",
                "DIARY_METRICS_SOURCE": "foundation",
                "DIARY_MEMORY_SOURCE": "foundation",
                "DIARY_TASKS_SOURCE": "foundation",
            }
        )
    logs = logs_dir or Path.home() / "Library" / "Logs" / "Actanara"
    command = " ".join(
        [
            "cd",
            shlex.quote(str(project_root)),
            "&&",
            "exec",
            shlex.quote(str(python)),
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            shlex.quote(str(project_root / "src" / "dashboard")),
            "--host",
            shlex.quote(host),
            "--port",
            str(port),
        ]
    )
    return {
        "Label": label,
        "ProgramArguments": ["/bin/zsh", "-lc", command],
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "dashboard-server.out.log"),
        "StandardErrorPath": str(logs / "dashboard-server.err.log"),
    }


def build_watchdog_plist(
    *,
    label: str,
    service_label: str,
    python: Path,
    script: Path,
    url: str,
    interval: int,
    actanara_home: Path,
    logs_dir: Path | None = None,
) -> dict:
    logs = logs_dir or Path.home() / "Library" / "Logs" / "Actanara"
    return {
        "Label": label,
        "ProgramArguments": [
            str(python),
            str(script),
            "check",
            "--url",
            url,
            "--label",
            service_label,
            "--restart",
        ],
        "EnvironmentVariables": {
            "ACTANARA_HOME": str(actanara_home),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "RunAtLoad": True,
        "StartInterval": interval,
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "dashboard-watchdog.out.log"),
        "StandardErrorPath": str(logs / "dashboard-watchdog.err.log"),
    }


def write_plist(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump(payload, fh, sort_keys=False)


def check_health(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except Exception:
        return False


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    binary = os.environ.get("ACTANARA_INSTALL_LAUNCHCTL") or shutil.which("launchctl") or "/bin/launchctl"
    return subprocess.run([binary, *args], text=True, capture_output=True, check=False)


def restart_service(label: str) -> int:
    domain_label = f"gui/{os.getuid()}/{label}"
    result = launchctl("kickstart", "-k", domain_label)
    if result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout)
    return result.returncode


def write_agents(args: argparse.Namespace) -> tuple[Path, Path]:
    project_root = args.project_root.expanduser().absolute()
    python = args.python.expanduser().absolute()
    actanara_home = args.actanara_home.expanduser().absolute()
    logs_dir = args.logs_dir.expanduser().absolute()
    _require_stable_runtime_binding(
        project_root=project_root,
        python=python,
        actanara_home=actanara_home,
    )
    logs_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_plist_path(args.label)
    watchdog_path = watchdog_plist_path(args.watchdog_label)
    url = args.url or f"http://{args.host}:{args.port}/health"
    write_plist(
        service_path,
        build_service_plist(
            label=args.label,
            python=python,
            project_root=project_root,
            actanara_home=actanara_home,
            host=args.host,
            port=args.port,
            foundation=args.foundation,
            logs_dir=logs_dir,
        ),
    )
    write_plist(
        watchdog_path,
        build_watchdog_plist(
            label=args.watchdog_label,
            service_label=args.label,
            python=python,
            script=project_root / "advanced" / "dashboard" / "dashboard_launch_agent.py",
            url=url,
            interval=args.interval,
            actanara_home=actanara_home,
            logs_dir=logs_dir,
        ),
    )
    return service_path, watchdog_path


def install_agents(args: argparse.Namespace) -> int:
    service_path, watchdog_path = write_agents(args)
    domain = f"gui/{os.getuid()}"
    for path in (service_path, watchdog_path):
        launchctl("bootout", domain, str(path))
        result = launchctl("bootstrap", domain, str(path))
        if result.returncode != 0:
            sys.stderr.write(result.stderr or result.stdout)
            return result.returncode
    launchctl("kickstart", "-k", f"{domain}/{args.label}")
    launchctl("kickstart", "-k", f"{domain}/{args.watchdog_label}")
    print(service_path)
    print(watchdog_path)
    return 0


def uninstall_agents(args: argparse.Namespace) -> int:
    domain = f"gui/{os.getuid()}"
    rc = 0
    for path in (service_plist_path(args.label), watchdog_plist_path(args.watchdog_label)):
        result = launchctl("bootout", domain, str(path))
        if result.returncode not in (0, 3, 113):
            sys.stderr.write(result.stderr or result.stdout)
            rc = result.returncode
    return rc


def main(argv: list[str] | None = None) -> int:
    defaults = dashboard_launch_defaults()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--label", default=defaults["label"])
        p.add_argument("--watchdog-label", default=defaults["watchdog_label"])
        p.add_argument("--python", type=Path, default=defaults["python"])
        p.add_argument("--project-root", type=Path, default=defaults["project_root"])
        p.add_argument("--actanara-home", type=Path, default=defaults["actanara_home"])
        p.add_argument("--host", default=defaults["host"])
        p.add_argument("--port", type=int, default=defaults["port"])
        p.add_argument("--url")
        p.add_argument("--logs-dir", type=Path, default=defaults["logs_dir"])
        p.add_argument("--interval", type=int, default=60)
        p.add_argument("--foundation", action="store_true")

    for name in ("write", "install", "uninstall"):
        add_common(sub.add_parser(name))

    check = sub.add_parser("check")
    check.add_argument("--url", default=defaults["url"])
    check.add_argument("--label", default=defaults["label"])
    check.add_argument("--restart", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "write":
        for path in write_agents(args):
            print(path)
        return 0
    if args.command == "install":
        return install_agents(args)
    if args.command == "uninstall":
        return uninstall_agents(args)
    if args.command == "check":
        if check_health(args.url):
            print(f"healthy: {args.url}")
            return 0
        print(f"unhealthy: {args.url}", file=sys.stderr)
        if args.restart:
            return restart_service(args.label)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

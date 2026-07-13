#!/usr/bin/env python3
"""Install or run the Open Nova nova-RAG server LaunchAgent."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


sys.dont_write_bytecode = True


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DEFAULT_PROJECT_ROOT))
sys.path.insert(0, str(DEFAULT_PROJECT_ROOT / "src"))

DEFAULT_PYTHON = Path(sys.executable)
DEFAULT_NOVA_HOME = Path.home() / ".open-nova"
DEFAULT_SERVICE_LABEL = "com.open-nova.rag-server"


def rag_launch_defaults() -> dict:
    try:
        from data_foundation.paths import load_paths
        from data_foundation.settings import read_settings

        selected = load_paths()
        settings = read_settings(selected)
        dashboard = settings.get("dashboard") if isinstance(settings.get("dashboard"), dict) else {}
        return {
            "project_root": Path(str(dashboard.get("projectRoot") or DEFAULT_PROJECT_ROOT)),
            "python": Path(str(dashboard.get("pythonExecutable") or DEFAULT_PYTHON)),
            "nova_home": selected.home,
            "logs_dir": Path(str(dashboard.get("logsDir") or (Path.home() / "Library" / "Logs" / "OpenNova"))),
            "label": DEFAULT_SERVICE_LABEL,
        }
    except Exception:
        return {
            "project_root": DEFAULT_PROJECT_ROOT,
            "python": DEFAULT_PYTHON,
            "nova_home": DEFAULT_NOVA_HOME,
            "logs_dir": Path.home() / "Library" / "Logs" / "OpenNova",
            "label": DEFAULT_SERVICE_LABEL,
        }


def launch_agents_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents"


def service_plist_path(label: str, home: Path | None = None) -> Path:
    return launch_agents_dir(home) / f"{label}.plist"


def build_service_plist(
    *,
    label: str,
    python: Path,
    project_root: Path,
    nova_home: Path,
    script: Path,
    logs_dir: Path | None = None,
) -> dict:
    logs = logs_dir or Path.home() / "Library" / "Logs" / "OpenNova"
    return {
        "Label": label,
        "ProgramArguments": [
            str(python),
            str(script),
            "run",
            "--project-root",
            str(project_root),
            "--nova-home",
            str(nova_home),
        ],
        "EnvironmentVariables": {
            "NOVA_HOME": str(nova_home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": f"{project_root}:{project_root / 'src'}",
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "rag-server-launchagent.out.log"),
        "StandardErrorPath": str(logs / "rag-server-launchagent.err.log"),
    }


def write_plist(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump(payload, fh, sort_keys=False)


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    binary = os.environ.get("NOVA_INSTALL_LAUNCHCTL") or shutil.which("launchctl") or "/bin/launchctl"
    return subprocess.run([binary, *args], text=True, capture_output=True, check=False)


def write_agent(args: argparse.Namespace) -> Path:
    project_root = args.project_root.resolve()
    nova_home = args.nova_home.resolve()
    logs_dir = args.logs_dir.expanduser().resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = service_plist_path(args.label)
    write_plist(
        path,
        build_service_plist(
            label=args.label,
            python=args.python,
            project_root=project_root,
            nova_home=nova_home,
            script=Path(__file__).resolve(),
            logs_dir=logs_dir,
        ),
    )
    return path


def install_agent(args: argparse.Namespace) -> int:
    path = write_agent(args)
    domain = f"gui/{os.getuid()}"
    launchctl("bootout", domain, str(path))
    result = launchctl("bootstrap", domain, str(path))
    if result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout)
        return result.returncode
    print(path)
    return 0


def uninstall_agent(args: argparse.Namespace) -> int:
    domain = f"gui/{os.getuid()}"
    result = launchctl("bootout", domain, str(service_plist_path(args.label)))
    if result.returncode not in (0, 3, 113):
        sys.stderr.write(result.stderr or result.stdout)
        return result.returncode
    return 0


def run_server(args: argparse.Namespace) -> int:
    os.environ["NOVA_HOME"] = str(args.nova_home.resolve())
    project_root = args.project_root.resolve()
    sys.path.insert(0, str(project_root))
    sys.path.insert(0, str(project_root / "src"))

    from agentic_rag.rag_server_lifecycle import read_server_process_state, start_rag_server, stop_rag_server
    from agentic_rag.rag_settings import resolve_rag_settings

    stopping = False

    def handle_stop(signum, frame):  # noqa: ARG001
        nonlocal stopping
        stopping = True
        try:
            stop_rag_server(resolve_rag_settings(), requested_by="launchd")
        finally:
            raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    settings = resolve_rag_settings()
    result = start_rag_server(settings, requested_by="launchd", wait_timeout_seconds=20.0)
    if not result.get("accepted"):
        print(result.get("reason") or result.get("status") or "nova-RAG server start was not accepted", file=sys.stderr)
        return 1

    while not stopping:
        state = read_server_process_state(settings, probe_health=False)
        if not state.get("running"):
            print("nova-RAG server process is not running", file=sys.stderr)
            return 1
        time.sleep(5)
    return 0


def main(argv: list[str] | None = None) -> int:
    defaults = rag_launch_defaults()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--label", default=defaults["label"])
        p.add_argument("--python", type=Path, default=defaults["python"])
        p.add_argument("--project-root", type=Path, default=defaults["project_root"])
        p.add_argument("--nova-home", type=Path, default=defaults["nova_home"])
        p.add_argument("--logs-dir", type=Path, default=defaults["logs_dir"])

    for name in ("write", "install", "uninstall", "run"):
        add_common(sub.add_parser(name))

    args = parser.parse_args(argv)
    if args.command == "write":
        print(write_agent(args))
        return 0
    if args.command == "install":
        return install_agent(args)
    if args.command == "uninstall":
        return uninstall_agent(args)
    if args.command == "run":
        return run_server(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

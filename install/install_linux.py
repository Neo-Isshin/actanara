#!/usr/bin/env python3
"""Fresh-install adapter for Actanara on Linux user sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from install import dependency_contract


ALLOWED_SOURCE_ENTRIES = (
    "advanced",
    "config.py",
    "install",
    "LICENSE",
    "MANIFEST.in",
    "pyproject.toml",
    "src",
)
EXCLUDED_NAMES = {
    ".DS_Store",
    ".env",
    ".git",
    ".mypy_cache",
    ".playwright-cli",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "cache",
    "data",
    "dist",
    "htmlcov",
    "logs",
    "reserved",
    "snapshots",
    "state",
    "tmp",
    "venv",
    "wheelhouse",
}
EXCLUDED_SUFFIXES = (
    ".db",
    ".egg-info",
    ".log",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
)


class LinuxInstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstallPlan:
    source_root: Path
    runtime: Path
    python: Path
    profiles: tuple[str, ...]
    language: str
    dashboard_host: str
    dashboard_port: int
    dashboard_service: bool
    scheduler: bool
    rag_enabled: bool
    rag_embedding_mode: str
    linger_policy: str
    dev_test: bool
    offline: bool
    dry_run: bool


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=str(ROOT))
    parser.add_argument("--runtime", default=os.environ.get("ACTANARA_INSTALL_RUNTIME", "~/.actanara"))
    parser.add_argument("--python", default=os.environ.get("ACTANARA_INSTALL_PYTHON", sys.executable))
    parser.add_argument("--language", choices=("zh-CN", "en-US"), default="zh-CN")
    parser.add_argument("--dashboard-host", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=3036)
    parser.add_argument("--no-dashboard-server", action="store_true")
    parser.add_argument("--no-dashboard", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-scheduler", action="store_true")
    parser.add_argument("--enable-rag", action="store_true")
    parser.add_argument("--rag-embedding-mode", choices=("local", "cloud"), default="cloud")
    linger = parser.add_mutually_exclusive_group()
    linger.add_argument(
        "--enable-linger",
        action="store_true",
        help="Explicitly allow a no-sudo loginctl request for always-on user services.",
    )
    linger.add_argument(
        "--require-linger",
        action="store_true",
        help="Fail before Runtime writes unless linger is already enabled.",
    )
    linger.add_argument(
        "--no-linger-prompt",
        action="store_true",
        help="Preserve the current linger state without prompting.",
    )
    parser.add_argument("--enable-dev-test", action="store_true")
    parser.add_argument("--no-shell-path", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--repair-existing", action="store_true")
    parser.add_argument("--source-only", action="store_true")
    return parser


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def build_plan(args: argparse.Namespace) -> InstallPlan:
    source_root = Path(args.source_root).expanduser().absolute()
    runtime = Path(args.runtime).expanduser().absolute()
    python = Path(shutil.which(args.python) or args.python).expanduser().absolute()
    profiles = {"dashboard"}
    if args.enable_rag:
        profiles.add("rag-server")
    if args.enable_dev_test:
        profiles.add("dev-test")
    linger_policy = (
        "enable"
        if args.enable_linger
        else "require"
        if args.require_linger
        else "preserve"
        if args.no_linger_prompt
        else "prompt"
    )
    return InstallPlan(
        source_root=source_root,
        runtime=runtime,
        python=python,
        profiles=tuple(sorted(profiles)),
        language=args.language,
        dashboard_host=args.dashboard_host,
        dashboard_port=args.dashboard_port,
        dashboard_service=not (args.no_dashboard_server or args.no_dashboard),
        scheduler=not args.no_scheduler,
        rag_enabled=args.enable_rag,
        rag_embedding_mode=args.rag_embedding_mode,
        linger_policy=linger_policy,
        dev_test=args.enable_dev_test,
        offline=bool(args.offline or _env_flag("ACTANARA_INSTALL_OFFLINE")),
        dry_run=bool(args.dry_run or _env_flag("ACTANARA_INSTALL_DRY_RUN")),
    )


def _managed_services_requested(plan: InstallPlan) -> bool:
    return bool(plan.scheduler or plan.dashboard_service or plan.rag_enabled)


def _prompt_enable_linger(language: str) -> bool | None:
    if language == "zh-CN":
        prompt = (
            "是否允许 Actanara 在你退出登录后继续运行 Dashboard 和定时任务？\n"
            "这会为当前 Linux 用户启用 systemd linger，可能持续使用少量 CPU、内存和网络。"
            " [y/N] "
        )
    else:
        prompt = (
            "Keep Actanara Dashboard and scheduled jobs running after you log out?\n"
            "This enables systemd linger for the current Linux user and may continue using a small amount "
            "of CPU, memory, and network. [y/N] "
        )
    try:
        with open("/dev/tty", "r+", encoding="utf-8", buffering=1) as terminal:
            terminal.write(prompt)
            answer = terminal.readline()
    except OSError:
        return None
    return answer.strip().lower() in {"y", "yes"}


def _prepare_linger(plan: InstallPlan) -> dict:
    from data_foundation.systemd_user import SystemdUserError, enable_linger, linger_status

    if not _managed_services_requested(plan):
        return {
            "status": "not-required",
            "enabled": None,
            "changed": False,
            "action": "not-required",
            "requestedPolicy": plan.linger_policy,
            "sudoInvoked": False,
        }
    current = linger_status()
    base = {
        **current,
        "requestedPolicy": plan.linger_policy,
        "sudoInvoked": False,
    }
    if current.get("enabled") is True:
        return {**base, "action": "already-enabled"}
    if plan.linger_policy == "require":
        raise LinuxInstallError(
            "linger is required but is not enabled; run `sudo loginctl enable-linger \"$USER\"` "
            "and retry"
        )
    if plan.linger_policy == "preserve":
        return {
            **base,
            "action": "preserved",
            "manualCommand": 'sudo loginctl enable-linger "$USER"',
        }
    if plan.dry_run:
        return {
            **base,
            "action": "planned-enable" if plan.linger_policy == "enable" else "would-prompt",
            "wouldChange": plan.linger_policy == "enable",
        }
    if plan.linger_policy == "prompt":
        accepted = _prompt_enable_linger(plan.language)
        if accepted is not True:
            return {
                **base,
                "action": "declined" if accepted is False else "non-interactive-preserved",
                "manualCommand": 'sudo loginctl enable-linger "$USER"',
            }
    try:
        enabled = enable_linger()
    except SystemdUserError as exc:
        raise LinuxInstallError(
            f"{exc}; Actanara did not invoke sudo. Run `sudo loginctl enable-linger \"$USER\"` "
            "and retry, or use --no-linger-prompt to keep session-only services"
        ) from exc
    return {
        **enabled,
        "requestedPolicy": plan.linger_policy,
        "sudoInvoked": False,
    }


def _preflight_linux_services(plan: InstallPlan) -> None:
    if not (plan.scheduler or plan.dashboard_service or plan.rag_enabled):
        return
    systemctl = os.environ.get("ACTANARA_INSTALL_SYSTEMCTL") or shutil.which("systemctl")
    if not systemctl:
        raise LinuxInstallError(
            "systemctl is required for requested Linux user services; disable those services or install systemd"
        )
    try:
        manager = subprocess.run(
            [systemctl, "--user", "show-environment"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LinuxInstallError("the systemd user manager preflight could not run") from exc
    if manager.returncode != 0:
        raise LinuxInstallError(
            "the systemd user manager is unavailable; start a user session or disable managed services"
        )

    if not plan.dashboard_service:
        return
    try:
        addresses = socket.getaddrinfo(
            plan.dashboard_host,
            plan.dashboard_port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise LinuxInstallError("the Dashboard loopback host could not be resolved") from exc
    checked: set[tuple[int, tuple]] = set()
    for family, socktype, protocol, _canonical, address in addresses:
        key = (family, address)
        if key in checked:
            continue
        checked.add(key)
        probe = socket.socket(family, socktype, protocol)
        try:
            probe.bind(address)
        except OSError as exc:
            raise LinuxInstallError(
                f"Dashboard port {plan.dashboard_port} is unavailable on {plan.dashboard_host}"
            ) from exc
        finally:
            probe.close()


def _validate_plan(plan: InstallPlan, args: argparse.Namespace) -> dependency_contract.ContractSelection:
    host_platform = platform.system()
    if host_platform != "Linux" and not _env_flag("ACTANARA_INSTALL_TEST_MODE"):
        raise LinuxInstallError("the Linux installer can only run on Linux")
    if args.upgrade or args.repair_existing or args.source_only:
        raise LinuxInstallError("Linux upgrades are not enabled yet; use a fresh Runtime path")
    if plan.rag_enabled and plan.rag_embedding_mode == "local":
        raise LinuxInstallError(
            "Linux phase 1 supports cloud/server RAG; local embedding wheels remain gated"
        )
    if not 1 <= plan.dashboard_port <= 65535:
        raise LinuxInstallError("dashboard port must be between 1 and 65535")
    if plan.dashboard_host not in {"127.0.0.1", "localhost", "::1"}:
        raise LinuxInstallError("Linux phase 1 Dashboard binding must remain loopback-only")
    if not plan.python.is_file() or not os.access(plan.python, os.X_OK):
        raise LinuxInstallError(f"Python executable is unavailable: {plan.python}")
    version = subprocess.run(
        [str(plan.python), "-I", "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        python_version = tuple(int(item) for item in version.stdout.strip().split("."))
    except ValueError:
        python_version = ()
    if version.returncode != 0 or python_version != (3, 13):
        raise LinuxInstallError("the current Linux Runtime lock requires CPython 3.13")
    required = (
        "pyproject.toml",
        "install/dependency_contract.py",
        "install/runtime-dependencies.lock.json",
        "src/data_foundation/migrations/0001_initial.sql",
        "src/dashboard/app/static/index.html",
    )
    missing = [name for name in required if not (plan.source_root / name).is_file()]
    if missing:
        raise LinuxInstallError("source payload is incomplete: " + ", ".join(missing))
    if plan.runtime.is_symlink() or (plan.runtime.exists() and not plan.runtime.is_dir()):
        raise LinuxInstallError("Runtime root must be a real directory, not a symlink or file")
    markers = (
        plan.runtime / "app" / "source",
        plan.runtime / ".venv",
        plan.runtime / "config" / "settings.json",
        plan.runtime / "data" / "actanara_data.sqlite3",
    )
    if any(path.exists() or path.is_symlink() for path in markers):
        raise LinuxInstallError("existing Runtime state requires Linux upgrade support")
    if host_platform == "Linux":
        _preflight_linux_services(plan)
    try:
        return dependency_contract.load_contract_selection(
            plan.source_root / "install" / "runtime-dependencies.lock.json",
            plan.source_root / "pyproject.toml",
            plan.profiles,
            python=plan.python,
        )
    except dependency_contract.ContractError as exc:
        raise LinuxInstallError(f"runtime dependency lock rejected this host: {exc.message}") from exc


def _secure_directory(path: Path, *, parents: bool = True) -> None:
    path.mkdir(parents=parents, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise LinuxInstallError(f"unsafe directory: {path}")
    path.chmod(0o700)


def _ignore_source(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in EXCLUDED_NAMES
        or name.startswith(".env.")
        or any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)
    }


def _migration_evidence(target: Path) -> dict:
    contract_path = target / "src" / "data_foundation" / "migration_compatibility.json"
    migrations_root = target / "src" / "data_foundation" / "migrations"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    records = contract.get("migrations") if isinstance(contract.get("migrations"), list) else []
    if (
        contract.get("schemaVersion") != 1
        or contract.get("policy") != "rollback-compatible-additive-only"
        or not records
    ):
        raise LinuxInstallError("migration compatibility contract is unsupported")
    normalized = []
    digest = hashlib.sha256()
    for record in records:
        version = str(record.get("version") or "")
        expected = str(record.get("sha256") or "")
        rollback_class = str(record.get("rollbackClass") or "")
        migration = migrations_root / f"{version}.sql"
        if (
            not re.fullmatch(r"[0-9]{4}_[a-z0-9_]+", version)
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or rollback_class not in {"rollback-compatible-additive", "breaking"}
            or not migration.is_file()
            or migration.is_symlink()
            or hashlib.sha256(migration.read_bytes()).hexdigest() != expected
        ):
            raise LinuxInstallError(f"migration compatibility evidence is invalid: {version}")
        normalized.append({"version": version, "sha256": expected, "rollbackClass": rollback_class})
        digest.update(f"{version}\0{expected}\0{rollback_class}\n".encode("ascii"))
    if [item["version"] for item in normalized] != sorted(path.stem for path in migrations_root.glob("*.sql")):
        raise LinuxInstallError("migration inventory does not match its compatibility contract")
    return {
        "schemaVersion": 1,
        "policy": contract["policy"],
        "preCommitWriterContract": contract["preCommitWriterContract"],
        "minimumReadableSchema": contract["minimumReadableSchema"],
        "maximumReadableSchema": contract["maximumReadableSchema"],
        "migrationSetSha256": digest.hexdigest(),
        "migrations": normalized,
    }


def _source_identity(source: Path) -> tuple[str, str | None]:
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    version = str((project.get("project") or {}).get("version") or "unknown")
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    commit = result.stdout.strip().lower() if result.returncode == 0 else None
    if commit is not None and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        commit = None
    suffix = commit[:12] if commit else hashlib.sha256(str(source).encode()).hexdigest()[:12]
    return f"actanara-{version}-{suffix}", commit


def _stage_source(plan: InstallPlan, release_target: Path, commit: str | None) -> dict:
    if release_target.exists() or release_target.is_symlink():
        raise LinuxInstallError(f"release generation already exists: {release_target}")
    _secure_directory(release_target)
    for name in ALLOWED_SOURCE_ENTRIES:
        source_path = plan.source_root / name
        if not source_path.exists():
            continue
        target_path = release_target / name
        if source_path.is_dir():
            shutil.copytree(source_path, target_path, ignore=_ignore_source, symlinks=False)
        else:
            shutil.copy2(source_path, target_path, follow_symlinks=False)
    if any(path.is_symlink() for path in release_target.rglob("*")):
        raise LinuxInstallError("runtime source payload must not contain symlinks")
    from data_foundation.release_clean import repository_clean_deployment_check

    clean = repository_clean_deployment_check(release_target)
    if clean.get("status") != "passed":
        raise LinuxInstallError("runtime source payload failed release-clean validation")
    payload_files = []
    payload_digest = hashlib.sha256()
    for path in sorted(release_target.rglob("*")):
        if not path.is_file() or path.name == ".actanara-runtime-source.json":
            continue
        relative = path.relative_to(release_target).as_posix()
        content = path.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        payload_files.append({"path": relative, "sha256": sha256, "size": len(content)})
        payload_digest.update(f"{relative}\0{sha256}\n".encode("utf-8"))
    release_id = release_target.name
    version = tomllib.loads((release_target / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    manifest = {
        "schemaVersion": 2,
        "product": "actanara",
        "sourceLocator": {"kind": "unavailable", "issue": "outside-login-home"},
        "deployedSourceLocator": {"kind": "runtime-relative", "pathComponents": ["app", "source"]},
        "releaseLocator": {"kind": "runtime-relative", "pathComponents": ["app", "releases", release_id]},
        "deploymentMode": "release-symlink",
        "copiedAt": datetime.now().astimezone().isoformat(),
        "pyprojectVersion": version,
        "git": {"available": commit is not None, "commit": commit, "branch": None, "remote": None, "dirty": None},
        "databaseCompatibility": _migration_evidence(release_target),
        "payload": {"fileCount": len(payload_files), "files": payload_files, "sha256": payload_digest.hexdigest()},
        "cleanScan": {
            "status": "passed",
            "scanner": "data_foundation.release_clean.repository_clean_deployment_check",
            "scannedFiles": int(clean.get("scannedFiles") or 0),
            "findingCount": 0,
        },
    }
    manifest_path = release_target / ".actanara-runtime-source.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path.chmod(0o600)
    return manifest


def _run(command: Iterable[str], *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(list(command), env=env, text=True, check=False)
    if result.returncode != 0:
        raise LinuxInstallError(f"command failed with status {result.returncode}: {command}")


def _seed_venv_pip(plan: InstallPlan, venv: Path) -> Path:
    _run([str(plan.python), "-m", "venv", "--without-pip", str(venv)])
    venv_python = venv / "bin" / "python"
    ensurepip = subprocess.run(
        [str(venv_python), "-I", "-m", "ensurepip", "--upgrade"],
        text=True,
        capture_output=True,
        check=False,
    )
    if ensurepip.returncode != 0:
        if plan.offline:
            raise LinuxInstallError("offline install cannot seed pip into this Python venv")
        _run(
            [
                str(plan.python),
                "-I",
                "-m",
                "pip",
                "--python",
                str(venv),
                "install",
                "--disable-pip-version-check",
                "pip==26.1.2",
            ]
        )
    return venv_python


def _write_cli_shim(runtime: Path) -> None:
    shim = runtime / "bin" / "actanara"
    content = (
        "#!/bin/sh\n"
        "set -eu\n"
        f"ACTANARA_HOME={str(runtime)!r}\n"
        'SOURCE="$ACTANARA_HOME/app/source"\n'
        'export ACTANARA_HOME PYTHONDONTWRITEBYTECODE=1\n'
        'export PYTHONPATH="$SOURCE:$SOURCE/src:$SOURCE/src/dashboard"\n'
        'exec "$ACTANARA_HOME/.venv/bin/python" -m data_foundation.cli "$@"\n'
    )
    shim.write_text(content, encoding="utf-8")
    shim.chmod(0o755)


def _configure_runtime(plan: InstallPlan) -> None:
    env = {
        **os.environ,
        "ACTANARA_HOME": str(plan.runtime),
        "ACTANARA_LOCATION_FILE": os.environ.get(
            "ACTANARA_LOCATION_FILE",
            str(Path.home() / ".config" / "actanara" / "location.json"),
        ),
        "PYTHONPATH": os.pathsep.join(
            (str(plan.runtime / "app" / "source"), str(plan.runtime / "app" / "source" / "src"))
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    _run(
        [
            str(plan.runtime / ".venv" / "bin" / "python"),
            "-m",
            "data_foundation.cli",
            "onboarding",
            "runtime-apply",
            "--runtime",
            str(plan.runtime),
            "--select-active-runtime",
            "--confirmation-text",
            "APPLY ACTANARA ONBOARDING",
            "--language",
            plan.language,
            "--json",
        ],
        env=env,
    )
    script = """
from pathlib import Path
from data_foundation.paths import runtime_paths_for_home
from data_foundation.settings import write_settings
runtime = Path(__import__('os').environ['ACTANARA_HOME'])
source = runtime / 'app' / 'source'
write_settings({
    'general': {'workspaceRoot': str(source), 'tmpWorkspace': str(runtime / 'state' / 'tmp')},
    'schedule': {'systemTimer': {'provider': 'systemd', 'label': 'actanara.daily', 'registered': False}},
    'dashboard': {
        'host': __import__('os').environ['ACTANARA_DASHBOARD_HOST'],
        'port': int(__import__('os').environ['ACTANARA_DASHBOARD_PORT']),
        'projectRoot': str(source),
        'pythonExecutable': str(runtime / '.venv' / 'bin' / 'python'),
        'appDir': str(source / 'src' / 'dashboard'),
        'server': {'enabled': __import__('os').environ['ACTANARA_DASHBOARD_SERVICE'] == '1'},
    },
    'pipeline': {'pythonExecutable': str(runtime / '.venv' / 'bin' / 'python'), 'workingDirectory': str(source)},
    'features': {
        'rag': __import__('os').environ['ACTANARA_RAG_ENABLED'] == '1',
        'embeddingServer': False,
    },
    'rag': {
        'enabled': __import__('os').environ['ACTANARA_RAG_ENABLED'] == '1',
        'mode': 'v2' if __import__('os').environ['ACTANARA_RAG_ENABLED'] == '1' else 'disabled',
        'embedding': {'mode': 'cloud', 'provider': 'cloud'},
        'server': {'enabled': __import__('os').environ['ACTANARA_RAG_ENABLED'] == '1'},
    },
}, runtime_paths_for_home(runtime))
"""
    env.update(
        {
            "ACTANARA_DASHBOARD_HOST": plan.dashboard_host,
            "ACTANARA_DASHBOARD_PORT": str(plan.dashboard_port),
            "ACTANARA_DASHBOARD_SERVICE": "1" if plan.dashboard_service else "0",
            "ACTANARA_RAG_ENABLED": "1" if plan.rag_enabled else "0",
        }
    )
    _run([str(plan.runtime / ".venv" / "bin" / "python"), "-c", script], env=env)


def _initialize_database(plan: InstallPlan) -> Path:
    database = plan.runtime / "data" / "actanara_data.sqlite3"
    env = {
        **os.environ,
        "ACTANARA_HOME": str(plan.runtime),
        "PYTHONPATH": os.pathsep.join(
            (
                str(plan.runtime / "app" / "source"),
                str(plan.runtime / "app" / "source" / "src"),
            )
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    script = """
from pathlib import Path
from data_foundation.db import migrate
from data_foundation.paths import runtime_paths_for_home
runtime = Path(__import__('os').environ['ACTANARA_HOME'])
migrate(runtime_paths_for_home(runtime))
"""
    _run([str(plan.runtime / ".venv" / "bin" / "python"), "-c", script], env=env)
    if not database.is_file():
        raise LinuxInstallError("database migration completed without creating the Runtime database")
    database.chmod(0o600)
    return database


def _install_systemd_user_services(plan: InstallPlan) -> dict:
    from data_foundation.paths import runtime_paths_for_home
    from data_foundation.settings import read_settings, write_settings
    from data_foundation.systemd_user import (
        SystemdUserError,
        dashboard_unit,
        install_user_units,
        rag_unit,
        scheduler_units,
    )

    paths = runtime_paths_for_home(plan.runtime)
    settings = read_settings(paths, redact_secrets=False, persist_defaults=False)
    schedule = settings.get("schedule") if isinstance(settings.get("schedule"), dict) else {}
    timer = schedule.get("systemTimer") if isinstance(schedule.get("systemTimer"), dict) else {}
    dashboard = settings.get("dashboard") if isinstance(settings.get("dashboard"), dict) else {}
    units = []
    scheduler_names: list[str] = []
    if plan.scheduler:
        scheduler_specs = scheduler_units(paths, schedule, timer)
        units.extend(scheduler_specs)
        scheduler_names = [unit.name for unit in scheduler_specs]
    if plan.dashboard_service:
        units.append(dashboard_unit(paths, dashboard))
    if plan.rag_enabled:
        units.append(rag_unit(paths))
    if not units:
        return {
            "status": "not-requested",
            "provider": "systemd-user",
            "units": [],
            "linger": {"status": "not-probed", "enabled": None, "changed": False},
        }
    try:
        result = install_user_units(paths, units)
    except SystemdUserError as exc:
        raise LinuxInstallError(str(exc)) from exc

    now = datetime.now().astimezone().isoformat()
    update: dict = {}
    if plan.scheduler:
        update["schedule"] = {
            "enabled": True,
            "mode": "system",
            "systemTimer": {
                "provider": "systemd",
                "label": str(timer.get("label") or "actanara.daily"),
                "registered": True,
                "registrationManagedBy": "linux-installer",
                "registeredAt": now,
                "jobs": scheduler_names,
                "lastAction": "install",
                "lastActionStatus": "success",
                "lastError": None,
                "lastErrorAt": None,
                "stale": False,
                "reinstallRequired": False,
            },
        }
    if plan.dashboard_service:
        update.setdefault("dashboard", {})["systemdUser"] = {
            "registered": True,
            "registrationManagedBy": "linux-installer",
            "registeredAt": now,
            "units": ["actanara-dashboard.service"],
        }
    if plan.rag_enabled:
        rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
        server = rag.get("server") if isinstance(rag.get("server"), dict) else {}
        update["rag"] = {
            "server": {
                **server,
                "systemdUser": {
                    "registered": True,
                    "registrationManagedBy": "linux-installer",
                    "registeredAt": now,
                    "units": ["actanara-rag-server.service"],
                },
            }
        }
    write_settings(update, paths)
    return result


def _install(
    plan: InstallPlan,
    selection: dependency_contract.ContractSelection,
    args: argparse.Namespace,
    *,
    linger: dict | None = None,
) -> dict:
    release_id, commit = _source_identity(plan.source_root)
    release_target = plan.runtime / "app" / "releases" / release_id
    venv_target = plan.runtime / "app" / "venvs" / release_id
    cache_root = plan.runtime / "app" / "dependency-cache" / "v1"
    if plan.dry_run:
        return {
            "schemaVersion": 1,
            "status": "planned",
            "platform": "linux",
            "architecture": selection.lock_environment["architecture"],
            "environmentId": selection.environment_id,
            "runtime": str(plan.runtime),
            "sourceRoot": str(plan.source_root),
            "profiles": list(plan.profiles),
            "releaseTarget": str(release_target),
            "venvTarget": str(venv_target),
            "schedulerProvider": "systemd",
            "linger": linger,
            "writes": False,
        }

    previous_umask = os.umask(0o077)
    try:
        for directory in (
            plan.runtime,
            plan.runtime / "app",
            plan.runtime / "app" / "releases",
            plan.runtime / "app" / "venvs",
            plan.runtime / "bin",
            plan.runtime / "state" / "logs",
        ):
            _secure_directory(directory)
        dependency_contract.materialize_dependency_cache(
            cache_root,
            selection,
            python=plan.python,
            offline=plan.offline,
            timeout=900,
        )
        _stage_source(plan, release_target, commit)
        _secure_directory(venv_target.parent)
        venv_python = _seed_venv_pip(plan, venv_target)
        dependency_contract.install_locked_dependencies(
            cache_root,
            selection,
            venv_python=venv_python,
            timeout=900,
        )
        dependency_contract.write_dependency_marker(venv_target, selection)
        dependency_contract.verify_dependency_marker(venv_target, selection)
        (plan.runtime / "app" / "source").symlink_to(Path("releases") / release_id)
        (plan.runtime / ".venv").symlink_to(Path("app") / "venvs" / release_id)
        _write_cli_shim(plan.runtime)
        _configure_runtime(plan)
        database = _initialize_database(plan)
        systemd_result = _install_systemd_user_services(plan)
        if not args.no_shell_path:
            user_bin = Path.home() / ".local" / "bin"
            _secure_directory(user_bin)
            user_shim = user_bin / "actanara"
            if not user_shim.exists() and not user_shim.is_symlink():
                user_shim.symlink_to(plan.runtime / "bin" / "actanara")
    finally:
        os.umask(previous_umask)
    return {
        "schemaVersion": 1,
        "status": "installed",
        "platform": "linux",
        "architecture": selection.lock_environment["architecture"],
        "environmentId": selection.environment_id,
        "runtime": str(plan.runtime),
        "database": str(database),
        "databaseInitialized": True,
        "profiles": list(plan.profiles),
        "schedulerProvider": "systemd",
        "schedulerRegistration": (
            "registered" if plan.scheduler else "disabled"
        ),
        "dashboardServiceRegistration": (
            "registered" if plan.dashboard_service else "disabled"
        ),
        "linger": linger,
        "systemdUser": systemd_result,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        plan = build_plan(args)
        selection = _validate_plan(plan, args)
        linger = _prepare_linger(plan)
        payload = _install(plan, selection, args, linger=linger)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except LinuxInstallError as exc:
        print(
            json.dumps(
                {"schemaVersion": 1, "status": "error", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except dependency_contract.ContractError as exc:
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "error",
                    "error": f"dependency contract failed: {exc.message}",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

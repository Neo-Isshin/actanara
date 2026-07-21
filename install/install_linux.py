#!/usr/bin/env python3
"""Fresh-install adapter for Actanara on Linux user sessions."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
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
UPDATE_RESULT_PREFIX = "ACTANARA_UPDATE_RESULT_JSON="


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
    update_mode: str
    force_rebuild: bool
    profile_evidence: dict | None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=str(ROOT))
    parser.add_argument("--runtime", default=os.environ.get("ACTANARA_INSTALL_RUNTIME", "~/.actanara"))
    parser.add_argument("--python", default=os.environ.get("ACTANARA_INSTALL_PYTHON", sys.executable))
    parser.add_argument("--language", choices=("zh-CN", "en-US"), default="zh-CN")
    parser.add_argument("--dashboard-host")
    parser.add_argument("--dashboard-port", type=int)
    parser.add_argument("--no-dashboard-server", action="store_true")
    parser.add_argument("--no-dashboard", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-scheduler", action="store_true")
    parser.add_argument("--enable-rag", action="store_true")
    parser.add_argument("--rag-embedding-mode", choices=("local", "cloud"))
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
    parser.add_argument("--force-rebuild", action="store_true")
    parser.add_argument("--result-json", action="store_true", help=argparse.SUPPRESS)
    return parser


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def _requested_update_mode(args: argparse.Namespace) -> str:
    if args.repair_existing:
        if args.upgrade or args.source_only or args.force_rebuild:
            raise LinuxInstallError(
                "--repair-existing cannot be combined with --upgrade, --source-only, or --force-rebuild"
            )
        if not args.dry_run and not args.yes:
            raise LinuxInstallError("--repair-existing requires --yes")
        return "repair"
    if args.source_only:
        if args.force_rebuild:
            raise LinuxInstallError("--source-only and --force-rebuild are mutually exclusive")
        return "source-only"
    if args.force_rebuild and not args.upgrade:
        raise LinuxInstallError("--force-rebuild requires --upgrade")
    if args.upgrade:
        return "upgrade"
    return "fresh"


def _read_update_settings(runtime: Path) -> dict:
    settings_path = runtime / "config" / "settings.json"
    if settings_path.is_symlink() or not settings_path.is_file():
        raise LinuxInstallError("existing Runtime Settings are missing or unsafe")
    metadata = settings_path.stat(follow_symlinks=False)
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        raise LinuxInstallError("existing Runtime Settings have unsafe ownership or permissions")
    try:
        value = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LinuxInstallError("existing Runtime Settings are unreadable") from exc
    if not isinstance(value, dict):
        raise LinuxInstallError("existing Runtime Settings must be a JSON object")
    return value


def _settings_bool(mapping: dict, key: str, fallback: bool) -> bool:
    value = mapping.get(key)
    return value if type(value) is bool else fallback


def build_plan(args: argparse.Namespace) -> InstallPlan:
    source_root = Path(args.source_root).expanduser().absolute()
    runtime = Path(args.runtime).expanduser().absolute()
    python = Path(shutil.which(args.python) or args.python).expanduser().absolute()
    update_mode = _requested_update_mode(args)
    profile_evidence: dict | None = None
    if update_mode == "fresh":
        dashboard_host = args.dashboard_host or "127.0.0.1"
        dashboard_port = args.dashboard_port if args.dashboard_port is not None else 3036
        dashboard_service = not (args.no_dashboard_server or args.no_dashboard)
        scheduler = not args.no_scheduler
        rag_enabled = bool(args.enable_rag)
        rag_embedding_mode = args.rag_embedding_mode or "cloud"
        profiles = {"dashboard"}
        if rag_enabled:
            profiles.add("rag-server")
            if rag_embedding_mode == "local":
                profiles.add("rag-local")
        if args.enable_dev_test:
            profiles.add("dev-test")
    else:
        settings = _read_update_settings(runtime)
        try:
            inherited = dependency_contract.runtime_dependency_profiles(
                runtime,
                allow_untrusted_active_venv=update_mode != "source-only",
                allow_legacy_settings=update_mode == "repair",
            )
        except dependency_contract.ContractError as exc:
            raise LinuxInstallError(
                f"Runtime dependency profile could not be inherited safely: {exc.message}"
            ) from exc
        profile_evidence = dict(inherited["evidence"])
        profiles = set(inherited["profiles"])
        if args.enable_dev_test:
            profiles.add("dev-test")
        rag_enabled = bool(inherited["rag"]["enabled"])
        rag_embedding_mode = str(inherited["rag"].get("embeddingMode") or "cloud")
        dashboard = settings.get("dashboard") if isinstance(settings.get("dashboard"), dict) else {}
        dashboard_server = (
            dashboard.get("server") if isinstance(dashboard.get("server"), dict) else {}
        )
        dashboard_registration = (
            dashboard.get("systemdUser")
            if isinstance(dashboard.get("systemdUser"), dict)
            else {}
        )
        schedule = settings.get("schedule") if isinstance(settings.get("schedule"), dict) else {}
        timer = schedule.get("systemTimer") if isinstance(schedule.get("systemTimer"), dict) else {}
        rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
        rag_server = rag.get("server") if isinstance(rag.get("server"), dict) else {}
        dashboard_service = _settings_bool(
            dashboard_server,
            "enabled",
            _settings_bool(dashboard_registration, "registered", True),
        )
        scheduler = _settings_bool(
            schedule,
            "enabled",
            _settings_bool(timer, "registered", False),
        )
        rag_service_enabled = _settings_bool(rag_server, "enabled", rag_enabled)
        rag_enabled = rag_enabled and rag_service_enabled
        dashboard_host = str(dashboard.get("host") or "127.0.0.1")
        try:
            dashboard_port = int(dashboard.get("port") or 3036)
        except (TypeError, ValueError) as exc:
            raise LinuxInstallError("existing Dashboard port is invalid") from exc
        conflicts = []
        if args.dashboard_host is not None and args.dashboard_host != dashboard_host:
            conflicts.append("--dashboard-host")
        if args.dashboard_port is not None and args.dashboard_port != dashboard_port:
            conflicts.append("--dashboard-port")
        if (args.no_dashboard_server or args.no_dashboard) and dashboard_service:
            conflicts.append("--no-dashboard-server")
        if args.no_scheduler and scheduler:
            conflicts.append("--no-scheduler")
        if args.enable_rag and not inherited["rag"]["enabled"]:
            conflicts.append("--enable-rag")
        if args.rag_embedding_mode is not None and (
            not inherited["rag"]["enabled"]
            or args.rag_embedding_mode != inherited["rag"]["embeddingMode"]
        ):
            conflicts.append("--rag-embedding-mode")
        if conflicts:
            raise LinuxInstallError(
                "update arguments conflict with Runtime Settings: " + ", ".join(conflicts)
            )
    linger_policy = (
        "enable"
        if args.enable_linger
        else "require"
        if args.require_linger
        else "preserve"
        if args.no_linger_prompt
        else "preserve"
        if update_mode != "fresh"
        else "prompt"
    )
    return InstallPlan(
        source_root=source_root,
        runtime=runtime,
        python=python,
        profiles=tuple(sorted(profiles)),
        language=args.language,
        dashboard_host=dashboard_host,
        dashboard_port=dashboard_port,
        dashboard_service=dashboard_service,
        scheduler=scheduler,
        rag_enabled=rag_enabled,
        rag_embedding_mode=rag_embedding_mode,
        linger_policy=linger_policy,
        dev_test=args.enable_dev_test,
        offline=bool(args.offline or _env_flag("ACTANARA_INSTALL_OFFLINE")),
        dry_run=bool(args.dry_run or _env_flag("ACTANARA_INSTALL_DRY_RUN")),
        update_mode=update_mode,
        force_rebuild=bool(args.force_rebuild),
        profile_evidence=profile_evidence,
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


def _preflight_linux_services(plan: InstallPlan, *, check_dashboard_port: bool = True) -> None:
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

    if not plan.dashboard_service or not check_dashboard_port:
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
    if plan.update_mode == "fresh":
        if any(path.exists() or path.is_symlink() for path in markers):
            raise LinuxInstallError("existing Runtime state requires --upgrade or --repair-existing")
    elif plan.update_mode == "repair":
        if not markers[2].is_file() or markers[2].is_symlink():
            raise LinuxInstallError("repair requires trustworthy existing Runtime Settings")
    elif not all(path.exists() or path.is_symlink() for path in markers[:3]):
        raise LinuxInstallError("selected Runtime is incomplete and cannot be updated safely")
    if host_platform == "Linux":
        _preflight_linux_services(
            plan,
            check_dashboard_port=plan.update_mode == "fresh",
        )
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


def _stage_source(
    plan: InstallPlan,
    release_target: Path,
    commit: str | None,
    *,
    manifest_release_id: str | None = None,
    precreated: bool = False,
) -> dict:
    if precreated:
        if release_target.is_symlink() or not release_target.is_dir():
            raise LinuxInstallError("reserved release generation is unavailable or unsafe")
        if {path.name for path in release_target.iterdir()} - {".actanara-update-owner"}:
            raise LinuxInstallError("reserved release generation contains unexpected content")
    else:
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
        if not path.is_file() or path.name in {
            ".actanara-runtime-source.json",
            ".actanara-update-owner",
        }:
            continue
        relative = path.relative_to(release_target).as_posix()
        content = path.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        payload_files.append({"path": relative, "sha256": sha256, "size": len(content)})
        payload_digest.update(f"{relative}\0{sha256}\n".encode("utf-8"))
    release_id = manifest_release_id or release_target.name
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


def _runtime_settings_update(plan: InstallPlan) -> dict:
    source = plan.runtime / "app" / "source"
    embedding: dict[str, object] = {
        "mode": plan.rag_embedding_mode,
        "provider": plan.rag_embedding_mode,
    }
    if plan.rag_embedding_mode == "local":
        embedding.update(
            {
                "providerId": "local",
                "model": "intfloat/multilingual-e5-small",
                "dimension": 384,
                "device": "auto",
            }
        )
    return {
        "general": {
            "workspaceRoot": str(source),
            "tmpWorkspace": str(plan.runtime / "state" / "tmp"),
        },
        "schedule": {
            "systemTimer": {
                "provider": "systemd",
                "label": "actanara.daily",
                "registered": False,
            }
        },
        "dashboard": {
            "host": plan.dashboard_host,
            "port": plan.dashboard_port,
            "projectRoot": str(source),
            "pythonExecutable": str(plan.runtime / ".venv" / "bin" / "python"),
            "appDir": str(source / "src" / "dashboard"),
            "server": {"enabled": plan.dashboard_service},
        },
        "pipeline": {
            "pythonExecutable": str(plan.runtime / ".venv" / "bin" / "python"),
            "workingDirectory": str(source),
        },
        "features": {
            "rag": plan.rag_enabled,
            "embeddingServer": False,
        },
        "rag": {
            "enabled": plan.rag_enabled,
            "mode": "v2" if plan.rag_enabled else "disabled",
            "embedding": embedding,
            "server": {"enabled": plan.rag_enabled},
        },
    }


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
import json
from pathlib import Path
from data_foundation.paths import runtime_paths_for_home
from data_foundation.settings import write_settings
runtime = Path(__import__('os').environ['ACTANARA_HOME'])
update = json.loads(__import__('os').environ['ACTANARA_RUNTIME_SETTINGS_UPDATE'])
write_settings(update, runtime_paths_for_home(runtime))
"""
    env["ACTANARA_RUNTIME_SETTINGS_UPDATE"] = json.dumps(
        _runtime_settings_update(plan),
        ensure_ascii=False,
        sort_keys=True,
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


def _desired_systemd_units(plan: InstallPlan, settings: dict) -> list:
    from data_foundation.paths import runtime_paths_for_home
    from data_foundation.systemd_user import dashboard_unit, rag_unit, scheduler_units

    paths = runtime_paths_for_home(plan.runtime)
    schedule = settings.get("schedule") if isinstance(settings.get("schedule"), dict) else {}
    timer = schedule.get("systemTimer") if isinstance(schedule.get("systemTimer"), dict) else {}
    dashboard = settings.get("dashboard") if isinstance(settings.get("dashboard"), dict) else {}
    rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
    server = rag.get("server") if isinstance(rag.get("server"), dict) else {}
    units = []
    if plan.scheduler:
        units.extend(scheduler_units(paths, schedule, timer))
    if plan.dashboard_service:
        units.append(dashboard_unit(paths, dashboard))
    if plan.rag_enabled:
        units.append(rag_unit(paths, server))
    return units


def _systemd_unit_inventory(plan: InstallPlan, settings: dict) -> tuple[list, tuple[str, ...]]:
    from data_foundation.systemd_user import (
        MANAGED_UNIT_HEADER,
        UNIT_NAME_RE,
        UserUnit,
        default_user_unit_dir,
    )

    desired = _desired_systemd_units(plan, settings)
    names = {unit.name for unit in desired}
    schedule = settings.get("schedule") if isinstance(settings.get("schedule"), dict) else {}
    timer = schedule.get("systemTimer") if isinstance(schedule.get("systemTimer"), dict) else {}
    dashboard = settings.get("dashboard") if isinstance(settings.get("dashboard"), dict) else {}
    rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
    server = rag.get("server") if isinstance(rag.get("server"), dict) else {}
    for registration in (
        timer,
        dashboard.get("systemdUser") if isinstance(dashboard.get("systemdUser"), dict) else {},
        server.get("systemdUser") if isinstance(server.get("systemdUser"), dict) else {},
    ):
        configured = registration.get("jobs") or registration.get("units") or []
        if not isinstance(configured, list):
            raise LinuxInstallError("Runtime Settings contain an unsafe systemd unit inventory")
        for name in configured:
            if not isinstance(name, str) or not UNIT_NAME_RE.fullmatch(name):
                raise LinuxInstallError("Runtime Settings contain an unsafe systemd unit name")
            names.add(name)
    unit_root = default_user_unit_dir()
    if unit_root.is_dir() and not unit_root.is_symlink():
        runtime_binding = f"ACTANARA_HOME={plan.runtime}"
        for target in unit_root.iterdir():
            if not UNIT_NAME_RE.fullmatch(target.name) or target.is_symlink() or not target.is_file():
                continue
            try:
                content = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if (
                content.splitlines()[:1] == [MANAGED_UNIT_HEADER]
                and runtime_binding in content
            ):
                names.add(target.name)
    by_name = {unit.name: unit for unit in desired}
    inventory = [
        by_name.get(name) or UserUnit(name=name, content="", enable_now=False)
        for name in sorted(names)
    ]
    return desired, tuple(unit.name for unit in inventory)


def _reconcile_existing_systemd_units(plan: InstallPlan, settings: dict) -> dict:
    from data_foundation.paths import runtime_paths_for_home
    from data_foundation.systemd_user import (
        SystemdUserError,
        UserUnit,
        inspect_user_units,
        install_user_units,
        uninstall_user_units,
    )

    paths = runtime_paths_for_home(plan.runtime)
    desired, inventory = _systemd_unit_inventory(plan, settings)
    desired_names = {unit.name for unit in desired}
    removed = [
        UserUnit(name=name, content="", enable_now=False)
        for name in inventory
        if name not in desired_names
    ]
    try:
        current = inspect_user_units(desired) if desired else None
        installed_result = (
            None
            if current is not None
            and current.get("definitionsAligned") is True
            and current.get("actualRegistered") is True
            else install_user_units(paths, desired)
            if desired
            else None
        )
        # Establish every desired definition before pruning stale managed
        # definitions. A second-step failure can leave only a harmless stale
        # unit, never remove the service the repaired Runtime needs.
        removed_result = uninstall_user_units(paths, removed) if removed else None
    except SystemdUserError as exc:
        raise LinuxInstallError(str(exc)) from exc
    return {
        "installed": installed_result,
        "removed": removed_result,
        "units": sorted(desired_names),
    }


def _install_systemd_user_services(plan: InstallPlan) -> dict:
    from data_foundation.paths import runtime_paths_for_home
    from data_foundation.settings import read_settings, write_settings
    from data_foundation.systemd_user import (
        SystemdUserError,
        install_user_units,
    )

    paths = runtime_paths_for_home(plan.runtime)
    settings = read_settings(paths, redact_secrets=False, persist_defaults=False)
    schedule = settings.get("schedule") if isinstance(settings.get("schedule"), dict) else {}
    timer = schedule.get("systemTimer") if isinstance(schedule.get("systemTimer"), dict) else {}
    dashboard = settings.get("dashboard") if isinstance(settings.get("dashboard"), dict) else {}
    units = _desired_systemd_units(plan, settings)
    scheduler_names = [
        unit.name
        for unit in units
        if unit.name.endswith((".timer", ".service"))
        and str(timer.get("label") or "actanara.daily") in unit.name
    ]
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
        from data_foundation.systemd_user import dashboard_unit

        update.setdefault("dashboard", {})["systemdUser"] = {
            "registered": True,
            "registrationManagedBy": "linux-installer",
            "registeredAt": now,
            "units": [dashboard_unit(paths, dashboard).name],
        }
    if plan.rag_enabled:
        from data_foundation.systemd_user import rag_unit

        rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
        server = rag.get("server") if isinstance(rag.get("server"), dict) else {}
        update["rag"] = {
            "server": {
                **server,
                "systemdUser": {
                    "registered": True,
                    "registrationManagedBy": "linux-installer",
                    "registeredAt": now,
                    "units": [rag_unit(paths, server).name],
                },
            }
        }
    write_settings(update, paths)
    return result


def _transaction_command(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "install" / "update_transaction.py"), *arguments],
        text=True,
        capture_output=True,
        check=False,
        timeout=1200,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise LinuxInstallError(
            f"update transaction command failed ({arguments[0]}): {detail or result.returncode}"
        )
    return result


def _recover_update_runtime(runtime: Path) -> None:
    if not runtime.is_dir() or runtime.is_symlink():
        return
    _transaction_command("recover", "--runtime", str(runtime))


def _dependency_update_plan(
    plan: InstallPlan,
    selection: dependency_contract.ContractSelection,
) -> dict:
    mode = (
        "explicit-source-only"
        if plan.update_mode == "source-only"
        else "force-rebuild"
        if plan.update_mode == "repair" or plan.force_rebuild
        else "auto"
    )
    payload, return_code = dependency_contract.plan_update(
        plan.runtime,
        selection,
        mode=mode,
        offline=plan.offline,
        cache_root=plan.runtime / "app" / "dependency-cache" / "v1",
    )
    if return_code != 0 or payload.get("status") != "ready":
        raise LinuxInstallError(
            f"runtime dependency plan blocked before service stop: {payload.get('reason') or 'unknown'}"
        )
    return payload


def _verify_updated_systemd_units(plan: InstallPlan, settings: dict) -> dict:
    from data_foundation.systemd_user import SystemdUserError, inspect_user_units

    desired = _desired_systemd_units(plan, settings)
    if not desired:
        return {"status": "not-requested", "definitionsAligned": True, "actualRegistered": False}
    try:
        result = inspect_user_units(desired)
    except SystemdUserError as exc:
        raise LinuxInstallError(str(exc)) from exc
    if (
        result.get("definitionsPresent") is not True
        or result.get("definitionsManaged") is not True
        or result.get("definitionsAligned") is not True
    ):
        raise LinuxInstallError("managed systemd user-unit definitions are not aligned after update")
    return result


def _validate_existing_systemd_units_for_update(
    plan: InstallPlan,
    desired: list,
    inventory: tuple[str, ...],
) -> dict:
    """Reject definition drift before a rollback-capable standard update."""

    from data_foundation.systemd_user import SystemdUserError, inspect_user_units

    desired_names = tuple(sorted(unit.name for unit in desired))
    if tuple(sorted(inventory)) != desired_names:
        raise LinuxInstallError(
            "managed systemd unit inventory is stale; run --repair-existing before upgrade"
        )
    if not desired:
        return {
            "status": "not-requested",
            "definitionsPresent": True,
            "definitionsManaged": True,
            "definitionsAligned": True,
        }
    try:
        result = inspect_user_units(desired)
    except SystemdUserError as exc:
        raise LinuxInstallError(str(exc)) from exc
    if (
        result.get("definitionsPresent") is not True
        or result.get("definitionsManaged") is not True
        or result.get("definitionsAligned") is not True
    ):
        raise LinuxInstallError(
            "managed systemd unit definitions have drifted; run --repair-existing before upgrade"
        )
    return result


def _run_update_doctor(plan: InstallPlan) -> dict:
    env = {
        **os.environ,
        "ACTANARA_HOME": str(plan.runtime),
        "ACTANARA_LOCATION_FILE": os.environ.get(
            "ACTANARA_LOCATION_FILE",
            str(Path.home() / ".config" / "actanara" / "location.json"),
        ),
        "PYTHONPATH": os.pathsep.join(
            (
                str(plan.runtime / "app" / "source"),
                str(plan.runtime / "app" / "source" / "src"),
                str(plan.runtime / "app" / "source" / "src" / "dashboard"),
            )
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    command = [
        str(plan.runtime / ".venv" / "bin" / "python"),
        "-m",
        "data_foundation.cli",
        "doctor",
        "--installer",
        "--runtime",
        str(plan.runtime),
        "--json",
    ]
    result = subprocess.run(
        command,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")
        raise LinuxInstallError(
            f"post-update installer doctor failed: {detail[:500] or result.returncode}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LinuxInstallError("post-update installer doctor returned invalid JSON") from exc
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict) or int(summary.get("errors") or 0) != 0:
        raise LinuxInstallError("post-update installer doctor reported a blocking error")
    return {
        "profile": str(payload.get("doctorProfile") or "installer"),
        "status": str(summary.get("status") or "unknown"),
        "errors": int(summary.get("errors") or 0),
        "warnings": int(summary.get("warnings") or 0),
        "checks": int(summary.get("checks") or 0),
    }


def _wait_for_update_service_health(
    plan: InstallPlan,
    settings: dict,
    *,
    active_units: set[str] | None = None,
) -> None:
    from data_foundation.paths import runtime_paths_for_home
    from data_foundation.systemd_user import dashboard_unit, rag_unit

    endpoints: list[tuple[str, str, int, str]] = []
    paths = runtime_paths_for_home(plan.runtime)
    dashboard = settings.get("dashboard") if isinstance(settings.get("dashboard"), dict) else {}
    rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
    server = rag.get("server") if isinstance(rag.get("server"), dict) else {}
    dashboard_active = (
        active_units is None
        or dashboard_unit(paths, dashboard).name in active_units
    )
    rag_active = active_units is None or rag_unit(paths, server).name in active_units
    if plan.dashboard_service and dashboard_active:
        endpoints.append(
            (
                "dashboard",
                str(dashboard.get("host") or "127.0.0.1"),
                int(dashboard.get("port") or 3036),
                str(dashboard.get("healthPath") or "/health"),
            )
        )
    if plan.rag_enabled and rag_active:
        endpoints.append(
            (
                "rag",
                str(server.get("host") or "127.0.0.1"),
                int(server.get("port") or 3037),
                str(server.get("healthPath") or "/health"),
            )
        )
    for kind, host, port, path in endpoints:
        if host == "0.0.0.0":
            host = "127.0.0.1"
        elif host == "::":
            host = "::1"
        if not path.startswith("/"):
            path = "/" + path
        deadline = time.monotonic() + 30.0
        last_error = "unavailable"
        while True:
            connection: http.client.HTTPConnection | None = None
            try:
                connection = http.client.HTTPConnection(host, port, timeout=2)
                connection.request("GET", path, headers={"Accept": "application/json"})
                response = connection.getresponse()
                body = response.read(65536)
                payload = json.loads(body) if response.status == 200 else {}
                if response.status == 200 and isinstance(payload, dict) and str(
                    payload.get("status") or ""
                ).lower() in {"ok", "healthy"}:
                    break
                last_error = f"HTTP {response.status}"
            except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
                last_error = type(exc).__name__
            finally:
                if connection is not None:
                    connection.close()
            if time.monotonic() >= deadline:
                raise LinuxInstallError(
                    f"managed {kind} health check failed after update: {last_error}"
                )
            time.sleep(0.2)


def _update(
    plan: InstallPlan,
    selection: dependency_contract.ContractSelection,
    args: argparse.Namespace,
) -> dict:
    if plan.profile_evidence is None:
        raise LinuxInstallError("update dependency profile evidence is missing")
    dependency_plan = _dependency_update_plan(plan, selection)
    cache_root = plan.runtime / "app" / "dependency-cache" / "v1"
    rebuild = dependency_plan["updateMode"] == "rebuild-candidate-venv"
    transaction_mode = "repair" if plan.update_mode == "repair" else "upgrade" if rebuild else "source-only"
    settings = _read_update_settings(plan.runtime)
    desired, inventory = _systemd_unit_inventory(plan, settings)
    systemctl = os.environ.get("ACTANARA_INSTALL_SYSTEMCTL") or shutil.which("systemctl") or ""
    tx_id = (
        datetime.now().strftime("%Y%m%dT%H%M%S")
        + f"-{os.getpid()}-{secrets.token_hex(4)}"
    )
    if plan.dry_run:
        return {
            "schemaVersion": 1,
            "status": "planned",
            "platform": "linux",
            "runtime": str(plan.runtime),
            "updateMode": transaction_mode,
            "reason": dependency_plan["reason"],
            "profiles": list(plan.profiles),
            "reusesRuntimeVenv": not rebuild,
            "plannedDependenciesInstalled": rebuild,
            "managedUnits": list(inventory),
            "writes": False,
        }
    if rebuild:
        dependency_contract.materialize_dependency_cache(
            cache_root,
            selection,
            python=plan.python,
            offline=plan.offline,
            timeout=900,
        )
    if plan.update_mode != "repair":
        _validate_existing_systemd_units_for_update(plan, desired, inventory)
    evidence = plan.profile_evidence
    begin_arguments = [
        "begin",
        "--runtime",
        str(plan.runtime),
        "--home",
        str(Path.home()),
        "--source-pointer",
        str(plan.runtime / "app" / "source"),
        "--venv-pointer",
        str(plan.runtime / ".venv"),
        "--expected-settings-sha256",
        str(evidence["settingsSha256"]),
        "--mode",
        transaction_mode,
        "--tx-id",
        tx_id,
        "--owner-pid",
        str(os.getpid()),
        "--platform",
        "Linux",
        "--uid",
        str(os.getuid()),
    ]
    if evidence.get("activeMarkerStatus") == "unavailable":
        begin_arguments.append("--settings-only-profile-evidence")
    else:
        begin_arguments.extend(
            (
                "--expected-active-venv-target",
                str(evidence["activeVenvTarget"]),
                "--expected-active-marker-status",
                str(evidence["activeMarkerStatus"]),
            )
        )
        if evidence.get("activeMarkerSha256"):
            begin_arguments.extend(
                ("--expected-active-marker-sha256", str(evidence["activeMarkerSha256"]))
            )
    if inventory:
        if not systemctl:
            raise LinuxInstallError("systemctl is unavailable for the managed update service inventory")
        begin_arguments.extend(("--systemctl", systemctl))
        for name in inventory:
            begin_arguments.extend(("--systemd-unit", name))
        if plan.update_mode != "repair":
            for unit in desired:
                definition_sha256 = hashlib.sha256(
                    unit.content.encode("utf-8")
                ).hexdigest()
                begin_arguments.extend(
                    (
                        "--expected-systemd-unit-sha256",
                        f"{unit.name}={definition_sha256}",
                    )
                )
    journal: Path | None = None
    prior_active_units: set[str] = set()
    committed = False
    try:
        journal = Path(_transaction_command(*begin_arguments).stdout.strip())
        journal_state = json.loads(journal.read_text(encoding="utf-8"))
        prior_active_units = {
            str(unit.get("name"))
            for unit in journal_state.get("systemdUnits", [])
            if isinstance(unit, dict) and unit.get("active") is True
        }
        temporary = Path(
            _transaction_command(
                "reserve-artifact",
                "--state",
                str(journal),
                "--kind",
                "source-temp",
            ).stdout.strip()
        )
        _release_id, commit = _source_identity(plan.source_root)
        _stage_source(
            plan,
            temporary,
            commit,
            manifest_release_id=tx_id,
            precreated=True,
        )
        candidate_source = Path(
            _transaction_command(
                "promote-source-artifact",
                "--state",
                str(journal),
            ).stdout.strip()
        )
        _transaction_command(
            "record-candidate",
            "--state",
            str(journal),
            "--kind",
            "source",
            "--candidate",
            str(candidate_source),
        )
        migration_arguments = [
            "verify-migration-compatibility",
            "--state",
            str(journal),
        ]
        if plan.update_mode == "repair":
            migration_arguments.append("--allow-legacy-repair")
        _transaction_command(*migration_arguments)
        candidate_venv: Path | None = None
        if rebuild:
            candidate_venv = Path(
                _transaction_command(
                    "reserve-artifact",
                    "--state",
                    str(journal),
                    "--kind",
                    "venv",
                ).stdout.strip()
            )
            venv_python = _seed_venv_pip(plan, candidate_venv)
            dependency_contract.install_locked_dependencies(
                cache_root,
                selection,
                venv_python=venv_python,
                timeout=900,
            )
            dependency_contract.write_dependency_marker(candidate_venv, selection)
            dependency_contract.verify_dependency_marker(candidate_venv, selection)
            _transaction_command(
                "record-candidate",
                "--state",
                str(journal),
                "--kind",
                "venv",
                "--candidate",
                str(candidate_venv),
            )
        _transaction_command("stop", "--state", str(journal))
        home = Path.home()
        _transaction_command(
            "capture-mutable",
            "--state",
            str(journal),
            "--location",
            os.environ.get(
                "ACTANARA_LOCATION_FILE",
                str(home / ".config" / "actanara" / "location.json"),
            ),
            "--cli-shim",
            str(plan.runtime / "bin" / "actanara"),
            "--user-cli-shim",
            str(home / ".local" / "bin" / "actanara"),
            "--desktop-link",
            str(home / "Desktop" / "Actanara"),
            "--shell-profile",
            "" if args.no_shell_path else str(home / ".profile"),
        )
        _transaction_command("normalize-service-plists", "--state", str(journal))
        _transaction_command("promote", "--state", str(journal))
        if plan.update_mode == "repair":
            _transaction_command("commit-repair", "--state", str(journal))
            committed = True
            dependency_contract.migrate_legacy_runtime_settings(
                plan.runtime,
                scheduler_enabled=plan.scheduler,
                dashboard_enabled=plan.dashboard_service,
                dashboard_server_enabled=plan.dashboard_service,
                rag_server_enabled=plan.rag_enabled,
            )
            _write_cli_shim(plan.runtime)
            database = _initialize_database(plan)
            settings = _read_update_settings(plan.runtime)
            systemd_result = _reconcile_existing_systemd_units(plan, settings)
            _wait_for_update_service_health(plan, settings)
            _verify_updated_systemd_units(plan, settings)
            _transaction_command("complete-repair", "--state", str(journal))
            doctor = _run_update_doctor(plan)
            return {
                "schemaVersion": 1,
                "status": "repaired",
                "platform": "linux",
                "runtime": str(plan.runtime),
                "database": str(database),
                "profiles": list(plan.profiles),
                "updateMode": "repair",
                "dependenciesInstalled": True,
                "reusesRuntimeVenv": False,
                "systemdUser": systemd_result,
                "doctor": doctor,
                "transactionJournal": str(journal),
            }
        _initialize_database(plan)
        _transaction_command("restore-services", "--state", str(journal))
        _wait_for_update_service_health(
            plan,
            settings,
            active_units=prior_active_units,
        )
        systemd_probe = _verify_updated_systemd_units(plan, settings)
        doctor = _run_update_doctor(plan)
        _transaction_command("verify", "--state", str(journal))
        _transaction_command("commit", "--state", str(journal))
        committed = True
        return {
            "schemaVersion": 1,
            "status": "updated",
            "platform": "linux",
            "runtime": str(plan.runtime),
            "profiles": list(plan.profiles),
            "updateMode": transaction_mode,
            "reason": dependency_plan["reason"],
            "dependenciesInstalled": rebuild,
            "reusesRuntimeVenv": not rebuild,
            "systemdUser": systemd_probe,
            "doctor": doctor,
            "transactionJournal": str(journal),
        }
    except Exception as exc:
        if journal is not None and not committed:
            rollback = _transaction_command(
                "rollback",
                "--state",
                str(journal),
                check=False,
            )
            if rollback.returncode != 0:
                detail = (rollback.stderr or rollback.stdout).strip()
                raise LinuxInstallError(
                    f"update failed and rollback is incomplete: {detail or rollback.returncode}"
                ) from exc
        if isinstance(exc, LinuxInstallError):
            raise
        if isinstance(exc, dependency_contract.ContractError):
            raise LinuxInstallError(f"dependency contract failed: {exc.message}") from exc
        raise LinuxInstallError(str(exc)) from exc


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


def _result_envelope(
    *,
    payload: dict | None,
    requested_mode: str,
    error: str | None = None,
) -> dict:
    completed = payload is not None and error is None
    status = str((payload or {}).get("status") or "")
    update_mode = str((payload or {}).get("updateMode") or requested_mode or "unknown")
    dependencies_installed = bool((payload or {}).get("dependenciesInstalled"))
    reuses_runtime_venv = bool((payload or {}).get("reusesRuntimeVenv", False))
    planned_dependencies = bool(
        (payload or {}).get("plannedDependenciesInstalled", dependencies_installed)
    )
    systemd = (payload or {}).get("systemdUser")
    services_stopped = bool(
        status in {"updated", "repaired"}
        and isinstance(systemd, dict)
        and systemd.get("units")
    )
    return {
        "schemaVersion": 1,
        "status": "completed" if completed else "failed",
        "updateMode": update_mode,
        "dependenciesInstalled": dependencies_installed,
        "reusesRuntimeVenv": reuses_runtime_venv,
        "sourceUpdated": status in {"updated", "repaired"} if completed else None,
        "reason": str((payload or {}).get("reason") or error or status or "unknown"),
        "cacheUsed": dependencies_installed,
        "servicesStopped": services_stopped,
        "plannedDependenciesInstall": planned_dependencies,
        "managedServiceDefinitionsNormalized": (
            requested_mode == "repair" if completed else None
        ),
        "rollbackComplete": None,
        "stateCertain": completed,
        "stage": "preflight" if status == "planned" else "complete" if completed else "installer",
    }


def _print_result_envelope(payload: dict) -> None:
    print(UPDATE_RESULT_PREFIX + json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    requested_mode = "unknown"
    try:
        args = _parser().parse_args(argv)
        requested_mode = _requested_update_mode(args)
        if requested_mode != "fresh":
            _recover_update_runtime(Path(args.runtime).expanduser().absolute())
        plan = build_plan(args)
        selection = _validate_plan(plan, args)
        linger = _prepare_linger(plan)
        if plan.update_mode == "fresh":
            payload = _install(plan, selection, args, linger=linger)
        else:
            previous_umask = os.umask(0o077)
            try:
                payload = _update(plan, selection, args)
            finally:
                os.umask(previous_umask)
            payload["linger"] = linger
        if args.result_json:
            _print_result_envelope(
                _result_envelope(payload=payload, requested_mode=requested_mode)
            )
        else:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except LinuxInstallError as exc:
        if args is not None and args.result_json:
            _print_result_envelope(
                _result_envelope(
                    payload=None,
                    requested_mode=requested_mode,
                    error=str(exc),
                )
            )
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
        if args is not None and args.result_json:
            _print_result_envelope(
                _result_envelope(
                    payload=None,
                    requested_mode=requested_mode,
                    error=f"dependency contract failed: {exc.message}",
                )
            )
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

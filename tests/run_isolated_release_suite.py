#!/usr/bin/env python3
"""Run the release unittest suite without consulting or mutating a user Runtime."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXED_NOW = "2026-07-11T12:00:00+00:00"
DEFAULT_TIMEZONE = "Asia/Hong_Kong"
REQUIRED_TEST_MODULES = ("fastapi", "uvicorn", "yaml", "croniter", "numpy")
LINUX_EXCLUDED_TEST_MODULES = frozenset(
    {
        "test_installer_full_upgrade",
        "test_installer_v2",
        "test_scheduler_doctor",
        "test_scheduler_handoff",
        "test_update_bootstrap_safety",
        "test_update_transaction",
    }
)
INHERITED_RUNTIME_ENV = (
    "DIARY_OUTPUT_DIR",
    "ACTANARA_DATA_DB_PATH",
    "ACTANARA_DATA_EXPORT_DIR",
    "ACTANARA_INSTALL_REF",
    "ACTANARA_INSTALL_RUNTIME",
    "ACTANARA_INSTALL_SOURCE_ROOT",
    "ACTANARA_INSTALL_SYSTEMCTL",
    "ACTANARA_RUNTIME_ROOT",
    "TARGET_TIMEZONE",
    "TASK_DB_PATH",
    "TMP_WORKSPACE",
    "WORKSPACE_DIR",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--reuse-current-interpreter",
        action="store_true",
        help="Skip the disposable dev-test venv bootstrap; isolation of HOME/ACTANARA_HOME still applies.",
    )
    parser.add_argument("--fixed-now", default=DEFAULT_FIXED_NOW, help="Fixed UTC instant for central business-clock helpers.")
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="Process timezone; Runtime-specific tests may override it.")
    parser.add_argument("--pattern", default="test_*.py", help="unittest discovery pattern.")
    parser.add_argument("--verbosity", type=int, choices=(1, 2), default=1)
    parser.add_argument(
        "--platform-scope",
        choices=("all", "linux"),
        default="all",
        help="Run every test, or exclude explicitly macOS-only installer/launchd modules.",
    )
    return parser


def _bootstrap_disposable_venv(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="actanara-release-venv-") as raw_root:
        root = Path(raw_root)
        build_source = root / "source"
        venv = root / "venv"
        pip_cache = root / "pip-cache"
        build_home = root / "Home"
        xdg_config_home = build_home / ".config"
        build_source.mkdir()
        xdg_config_home.mkdir(parents=True)
        for filename in (
            "pyproject.toml",
            "MANIFEST.in",
            "LICENSE",
            "README.md",
            "README.zh-CN.md",
        ):
            shutil.copy2(ROOT / filename, build_source / filename)
        shutil.copytree(
            ROOT / "src",
            build_source / "src",
            ignore=shutil.ignore_patterns("*.egg-info", "__pycache__", "*.pyc", "*.pyo"),
        )
        install_env = {
            **os.environ,
            "HOME": str(build_home),
            "XDG_CONFIG_HOME": str(xdg_config_home),
            "PIP_CACHE_DIR": str(pip_cache),
            "PIP_CONFIG_FILE": os.devnull,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        for name in INHERITED_RUNTIME_ENV:
            install_env.pop(name, None)
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(venv)],
            env=install_env,
            check=True,
        )
        child_python = venv / "bin" / "python"
        ensurepip = subprocess.run(
            [str(child_python), "-I", "-m", "ensurepip", "--upgrade"],
            cwd=ROOT,
            env=install_env,
            text=True,
            capture_output=True,
            check=False,
        )
        if ensurepip.returncode != 0:
            subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "pip",
                    "--python",
                    str(venv),
                    "install",
                    "--disable-pip-version-check",
                    "pip==26.1.2",
                ],
                cwd=ROOT,
                env=install_env,
                check=True,
            )
        subprocess.run(
            [
                str(child_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                f"{build_source}[dev-test]",
            ],
            cwd=ROOT,
            env=install_env,
            check=True,
        )
        command = [
            str(child_python),
            str(Path(__file__).resolve()),
            "--child",
            "--reuse-current-interpreter",
            "--fixed-now",
            args.fixed_now,
            "--timezone",
            args.timezone,
            "--pattern",
            args.pattern,
            "--verbosity",
            str(args.verbosity),
            "--platform-scope",
            args.platform_scope,
        ]
        return subprocess.run(command, cwd=ROOT, env=install_env, check=False).returncode


def _write_fake_launchctl(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "# A release test must never reach the real per-user launchd domain.\n"
        "if [ \"${1:-}\" = \"print\" ]; then exit 113; fi\n"
        "exit 77\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_fake_systemctl(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "# A release test must never reach the real per-user systemd manager.\n"
        "case \"${2:-}\" in is-enabled|is-active) exit 4 ;; esac\n"
        "exit 77\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _assert_test_dependencies() -> None:
    missing = [name for name in REQUIRED_TEST_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError(
            "isolated release interpreter is missing dev-test modules: " + ", ".join(missing)
        )


def _iter_test_cases(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_test_cases(item)
        else:
            yield item


def _platform_suite(
    suite: unittest.TestSuite,
    *,
    scope: str,
) -> tuple[unittest.TestSuite, int]:
    if scope == "all":
        return suite, 0
    selected = unittest.TestSuite()
    excluded = 0
    for case in _iter_test_cases(suite):
        module = str(case.__class__.__module__).rsplit(".", 1)[-1]
        if module in LINUX_EXCLUDED_TEST_MODULES:
            excluded += 1
        else:
            selected.addTest(case)
    return selected, excluded


@contextmanager
def _restrictive_test_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def _run_isolated(args: argparse.Namespace) -> int:
    fixed_utc = datetime.fromisoformat(args.fixed_now)
    if fixed_utc.tzinfo is None:
        raise ValueError("--fixed-now must include a UTC offset")
    fixed_utc = fixed_utc.astimezone(timezone.utc)

    with _restrictive_test_umask(), tempfile.TemporaryDirectory(prefix="actanara-release-runtime-") as raw_root:
        root = Path(raw_root)
        home = root / "Home"
        actanara_home = home / ".actanara"
        location_file = home / ".config" / "actanara" / "location.json"
        fake_bin = root / "bin"
        temporary_root = root / "temporary"
        fake_bin.mkdir(parents=True)
        home.mkdir(parents=True)
        temporary_root.mkdir()
        _write_fake_launchctl(fake_bin / "launchctl")
        _write_fake_systemctl(fake_bin / "systemctl")

        isolated_env = {
            "HOME": str(home),
            "ACTANARA_HOME": str(actanara_home),
            "ACTANARA_LOCATION_FILE": str(location_file),
            "ACTANARA_SECRET_BACKEND": "memory",
            "ACTANARA_RUN_REAL_LAUNCHD_TESTS": "0",
            "ACTANARA_RUN_REAL_SYSTEMD_TESTS": "0",
            "ACTANARA_INSTALL_LAUNCHCTL": str(fake_bin / "launchctl"),
            "ACTANARA_INSTALL_SYSTEMCTL": str(fake_bin / "systemctl"),
            "TMPDIR": str(temporary_root),
            "TMP": str(temporary_root),
            "TEMP": str(temporary_root),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": args.timezone,
        }
        # User/runtime paths and TARGET_TIMEZONE must not leak into a release
        # test. Individual tests can still set a value explicitly in a patch.
        for name in INHERITED_RUNTIME_ENV:
            os.environ.pop(name, None)
        os.environ.update(isolated_env)
        if hasattr(time, "tzset"):
            time.tzset()

        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "src"))
        sys.path.insert(0, str(ROOT / "src" / "dashboard"))
        _assert_test_dependencies()

        from data_foundation import time as nova_time
        from app.services import tz as dashboard_tz

        def fixed_business_now(paths=None, *, group="general"):
            return fixed_utc.astimezone(nova_time.resolve_timezone(paths, group=group))

        fixed_dashboard_now = fixed_utc.astimezone(nova_time.resolve_timezone()).replace(tzinfo=None)
        print(
            "ISOLATED_RELEASE_SUITE "
            f"python={sys.version.split()[0]} fixedNow={fixed_utc.isoformat()} "
            f"timezone={args.timezone} runtime={actanara_home} launchctl=fake",
            flush=True,
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(tempfile, "tempdir", str(temporary_root)))
            stack.enter_context(patch.object(nova_time, "business_now", side_effect=fixed_business_now))
            stack.enter_context(patch.object(dashboard_tz, "hkt_now", return_value=fixed_dashboard_now))
            discovered = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=args.pattern)
            suite, excluded = _platform_suite(discovered, scope=args.platform_scope)
            if args.platform_scope == "linux":
                print(
                    "ISOLATED_RELEASE_SCOPE "
                    f"scope=linux excluded={excluded} "
                    f"modules={','.join(sorted(LINUX_EXCLUDED_TEST_MODULES))}",
                    flush=True,
                )
            result = unittest.TextTestRunner(verbosity=args.verbosity).run(suite)
        print(
            "ISOLATED_RELEASE_RESULT "
            f"run={result.testsRun} failures={len(result.failures)} "
            f"errors={len(result.errors)} skipped={len(result.skipped)} "
            f"scope={args.platform_scope} excluded={excluded}",
            flush=True,
        )
        return 0 if result.wasSuccessful() else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.child and not args.reuse_current_interpreter:
        return _bootstrap_disposable_venv(args)
    return _run_isolated(args)


if __name__ == "__main__":
    raise SystemExit(main())

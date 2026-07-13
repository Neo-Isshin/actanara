#!/usr/bin/env python3
"""Run the release unittest suite without consulting or mutating a user Runtime."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
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
INHERITED_RUNTIME_ENV = (
    "DIARY_OUTPUT_DIR",
    "NOVA_DATA_DB_PATH",
    "NOVA_DATA_EXPORT_DIR",
    "NOVA_INSTALL_REF",
    "NOVA_INSTALL_RUNTIME",
    "NOVA_INSTALL_SOURCE_ROOT",
    "OPEN_NOVA_RUNTIME_ROOT",
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
        help="Skip the disposable dev-test venv bootstrap; isolation of HOME/NOVA_HOME still applies.",
    )
    parser.add_argument("--fixed-now", default=DEFAULT_FIXED_NOW, help="Fixed UTC instant for central business-clock helpers.")
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="Process timezone; Runtime-specific tests may override it.")
    parser.add_argument("--pattern", default="test_*.py", help="unittest discovery pattern.")
    parser.add_argument("--verbosity", type=int, choices=(1, 2), default=1)
    return parser


def _bootstrap_disposable_venv(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="open-nova-release-venv-") as raw_root:
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
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        child_python = venv / "bin" / "python"
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


def _assert_test_dependencies() -> None:
    missing = [name for name in REQUIRED_TEST_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError(
            "isolated release interpreter is missing dev-test modules: " + ", ".join(missing)
        )


def _run_isolated(args: argparse.Namespace) -> int:
    fixed_utc = datetime.fromisoformat(args.fixed_now)
    if fixed_utc.tzinfo is None:
        raise ValueError("--fixed-now must include a UTC offset")
    fixed_utc = fixed_utc.astimezone(timezone.utc)

    with tempfile.TemporaryDirectory(prefix="open-nova-release-runtime-") as raw_root:
        root = Path(raw_root)
        home = root / "Home"
        nova_home = home / ".open-nova"
        location_file = home / ".config" / "open-nova" / "location.json"
        fake_bin = root / "bin"
        fake_bin.mkdir(parents=True)
        home.mkdir(parents=True)
        _write_fake_launchctl(fake_bin / "launchctl")

        isolated_env = {
            "HOME": str(home),
            "NOVA_HOME": str(nova_home),
            "NOVA_LOCATION_FILE": str(location_file),
            "OPEN_NOVA_SECRET_BACKEND": "memory",
            "OPEN_NOVA_RUN_REAL_LAUNCHD_TESTS": "0",
            "NOVA_INSTALL_LAUNCHCTL": str(fake_bin / "launchctl"),
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
            f"timezone={args.timezone} runtime={nova_home} launchctl=fake",
            flush=True,
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(nova_time, "business_now", side_effect=fixed_business_now))
            stack.enter_context(patch.object(dashboard_tz, "hkt_now", return_value=fixed_dashboard_now))
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=args.pattern)
            result = unittest.TextTestRunner(verbosity=args.verbosity).run(suite)
        print(
            "ISOLATED_RELEASE_RESULT "
            f"run={result.testsRun} failures={len(result.failures)} "
            f"errors={len(result.errors)} skipped={len(result.skipped)}",
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

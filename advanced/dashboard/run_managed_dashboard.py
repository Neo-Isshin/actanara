#!/usr/bin/env python3
"""Run the managed Dashboard with bounded private application logs."""

from __future__ import annotations

import argparse
import logging
import logging.config
import os
import stat
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Sequence


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.scheduled_output import (
    SCHEDULED_STDERR_PATH_ENV,
    SCHEDULED_STDOUT_PATH_ENV,
    rotate_private_output_file,
)


MAX_DASHBOARD_LOG_BYTES = 1024 * 1024
DASHBOARD_LOG_BACKUP_COUNT = 1
LOGGER = logging.getLogger("actanara.dashboard.runner")


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """Rotating handler that refuses symlink/hardlink log targets."""

    def _open(self):
        path = Path(self.baseFilename)
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
            ):
                raise OSError("Dashboard log must be one owned regular file")
            os.fchmod(descriptor, 0o600)
            return os.fdopen(descriptor, self.mode, encoding=self.encoding)
        except Exception:
            os.close(descriptor)
            raise

    def doRollover(self) -> None:
        super().doRollover()
        for candidate in (Path(self.baseFilename), Path(f"{self.baseFilename}.1")):
            try:
                metadata = candidate.lstat()
                if stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid():
                    os.chmod(candidate, 0o600)
            except FileNotFoundError:
                continue


def _create_log_handler(filename: str) -> RotatingFileHandler:
    return _PrivateRotatingFileHandler(
        filename,
        maxBytes=MAX_DASHBOARD_LOG_BYTES,
        backupCount=DASHBOARD_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )


def uvicorn_log_config(log_path: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "managed": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
        },
        "handlers": {
            "bounded_file": {
                "()": _create_log_handler,
                "filename": str(log_path),
                "formatter": "managed",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["bounded_file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["bounded_file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": [], "level": "CRITICAL", "propagate": False},
        },
        "root": {"handlers": ["bounded_file"], "level": "INFO"},
    }


def _detach_inherited_output_streams() -> None:
    """Stop routine writes from extending launchd's fixed output files.

    The bounded logging configuration must be installed before this is called,
    so server and application diagnostics retain an independent rotating file
    descriptor.  Sending inherited stdout/stderr to ``/dev/null`` also avoids a
    rollover retaining an append descriptor to the renamed backup inode.
    """

    descriptor = os.open(os.devnull, os.O_WRONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.dup2(descriptor, 1)
        os.dup2(descriptor, 2)
    finally:
        os.close(descriptor)


def command_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-path", type=Path, required=True)
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    project_root = args.project_root.expanduser().absolute()
    app_dir = args.app_dir.expanduser().absolute()
    if app_dir != project_root / "src" / "dashboard":
        raise ValueError("managed Dashboard app directory does not match its project root")
    host = str(args.host or "").strip()
    if not host or len(host) > 253 or any(character.isspace() for character in host):
        raise ValueError("Dashboard host is invalid")
    if not 1 <= int(args.port) <= 65_535:
        raise ValueError("Dashboard port must be 1..65535")
    log_path = args.log_path.expanduser().absolute()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.parent.is_symlink() or log_path.name != "dashboard-server.log":
        raise ValueError("managed Dashboard log path is unsafe")
    for environment_key in (SCHEDULED_STDOUT_PATH_ENV, SCHEDULED_STDERR_PATH_ENV):
        raw_output_path = str(os.getenv(environment_key) or "").strip()
        if raw_output_path:
            rotate_private_output_file(
                Path(raw_output_path),
                allowed_parent=log_path.parent,
            )
    rotate_private_output_file(log_path, allowed_parent=log_path.parent)

    sys.path.insert(0, str(project_root))
    sys.path.insert(0, str(project_root / "src"))
    sys.path.insert(0, str(app_dir))
    os.chdir(project_root)
    import uvicorn

    # Install the handler before detaching launchd's append-only output files.
    # Configuration failures therefore remain visible on inherited stderr,
    # while all later server/application diagnostics use the bounded log.
    logging.config.dictConfig(uvicorn_log_config(log_path))
    _detach_inherited_output_streams()
    try:
        uvicorn.run(
            "app.main:app",
            app_dir=str(app_dir),
            host=host,
            port=int(args.port),
            access_log=False,
            log_config=None,
        )
    except Exception:
        LOGGER.exception("Actanara Dashboard server terminated unexpectedly.")
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(command_main())

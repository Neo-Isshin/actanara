"""Bound fixed-path launchd output without relying on a resident log daemon."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from .paths import RuntimePaths


SCHEDULED_STDOUT_PATH_ENV = "ACTANARA_SCHEDULED_STDOUT_PATH"
SCHEDULED_STDERR_PATH_ENV = "ACTANARA_SCHEDULED_STDERR_PATH"
MAX_SCHEDULED_LOG_BYTES = 1 * 1024 * 1024


def rotate_private_output_file(
    path: Path,
    *,
    allowed_parent: Path,
    max_bytes: int = MAX_SCHEDULED_LOG_BYTES,
) -> bool:
    """Rotate one owned regular file to a single ``.previous`` generation.

    The helper is deliberately path-bound and fail-closed so it can also be
    reused by long-lived managed services before they open their private log.
    """

    limit = max(1024, int(max_bytes))
    try:
        expected_parent = allowed_parent.resolve(strict=True)
        if path.parent.resolve(strict=True) != expected_parent:
            return False
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size <= limit
        ):
            return False
        previous = path.with_name(f"{path.name}.previous")
        try:
            previous_metadata = previous.lstat()
        except FileNotFoundError:
            previous_metadata = None
        if previous_metadata is not None and (
            not stat.S_ISREG(previous_metadata.st_mode)
            or previous_metadata.st_uid != os.getuid()
        ):
            return False
        os.replace(path, previous)
        try:
            directory_fd = os.open(expected_parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        return True
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return False


@contextmanager
def open_private_append_output(
    path: Path,
    *,
    allowed_parent: Path,
) -> Iterator[BinaryIO]:
    """Open a private append-only output without following a final symlink."""

    expected_parent = allowed_parent.resolve(strict=True)
    if path.parent.resolve(strict=True) != expected_parent:
        raise ValueError("managed output path is outside its allowed directory")
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
            raise OSError("managed output must be one owned regular file")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "ab", buffering=0) as handle:
            descriptor = -1
            yield handle
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def rotate_scheduled_output_files(
    paths: RuntimePaths,
    *,
    environ: Mapping[str, str] | None = None,
    max_bytes: int = MAX_SCHEDULED_LOG_BYTES,
) -> list[str]:
    """Keep one current and one previous private managed-scheduler log.

    launchd opens stdout/stderr before exec. Renaming an oversized path here
    therefore moves the current invocation's open descriptor to ``.previous``;
    the next invocation creates a fresh configured path. At most two bounded
    generations remain. Unknown or non-private paths fail closed.
    """

    source = os.environ if environ is None else environ
    log_root = paths.state_dir / "logs"
    try:
        log_root.resolve(strict=True)
    except OSError:
        return []
    rotated: list[str] = []
    for key in (SCHEDULED_STDOUT_PATH_ENV, SCHEDULED_STDERR_PATH_ENV):
        raw = str(source.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw)
        if rotate_private_output_file(path, allowed_parent=log_root, max_bytes=max_bytes):
            rotated.append(str(path))
    return rotated


__all__ = [
    "MAX_SCHEDULED_LOG_BYTES",
    "SCHEDULED_STDERR_PATH_ENV",
    "SCHEDULED_STDOUT_PATH_ENV",
    "open_private_append_output",
    "rotate_private_output_file",
    "rotate_scheduled_output_files",
]

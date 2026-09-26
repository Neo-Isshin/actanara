"""Bounded, allowlisted file reads shared by optional local runtime adapters."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from .base import DocumentRecord, timestamp

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_FILES = 5000
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def identity(*values) -> str:
    return hashlib.sha256("\0".join(map(str, values)).encode()).hexdigest()[:32]


def safe_path(path: Path, root: Path) -> bool:
    """Reject symlinks below the explicitly configured source boundary."""
    try:
        relative = path.absolute().relative_to(root.absolute())
        current = root
        if current.is_symlink():
            return False
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return False
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError):
        return False


def read_text(path: Path, root: Path, *, limit=MAX_FILE_BYTES) -> str:
    if not safe_path(path, root):
        raise ValueError("source-symlink-or-outside-root")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("source-file-too-large-or-not-regular")
        raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("source-file-too-large")
    return raw.decode("utf-8")


def read_json(path: Path, root: Path):
    return json.loads(read_text(path, root))


def discover(root: Path, *patterns: str) -> tuple[Path, ...]:
    """Patterns are parser-owned; callers never pass a user-controlled glob."""
    paths = set()
    if not root.is_dir() or root.is_symlink():
        return ()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and safe_path(path, root):
                paths.add(path)
                if len(paths) >= MAX_FILES:
                    return tuple(sorted(paths))
    return tuple(sorted(paths))


def markdown_document(path: Path, root: Path, session_key: str, *, variant="default", kind="artifact"):
    text = read_text(path, root, limit=MAX_DOCUMENT_BYTES).strip()
    if not text:
        return None
    return DocumentRecord(
        external_document_key=identity(variant, session_key, path.relative_to(root)),
        external_session_key=session_key, title=path.name, content=text, kind=kind,
        occurred_at=timestamp(path.stat().st_mtime), source_variant=variant,
        raw_locator={"path": str(path), "time_basis": "file-mtime", "verification": "source-authored"},
    )


class CachedFileRuntime:
    usage_status = "unavailable"
    capabilities = frozenset({"session_inventory", "dialogue_partial", "workspace_metadata"})

    def __init__(self):
        self.diagnostics = []
        self._loaded = False
        self._sessions = {}
        self._dialogue = {}
        self._usage = {}
        self._documents = {}
        self._bytes_read = 0
        self._read_paths = set()

    def read_text(self, path, root):
        size = path.stat().st_size
        if path not in self._read_paths:
            if self._bytes_read + size > MAX_TOTAL_BYTES:
                self.problem(path, "source-byte-budget")
                raise ValueError("source-byte-budget")
            self._bytes_read += size
            self._read_paths.add(path)
        return read_text(path, root)

    def read_json(self, path, root):
        return json.loads(self.read_text(path, root))

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._load()
        self._loaded = True

    def sessions(self):
        self._ensure_loaded()
        return tuple(self._sessions.values())

    def dialogue(self):
        self._ensure_loaded()
        return tuple(self._dialogue.values())

    def usage(self):
        self._ensure_loaded()
        return tuple(self._usage.values())

    def documents(self):
        self._ensure_loaded()
        return tuple(self._documents.values())

    def problem(self, path, code="unreadable-or-unsupported"):
        self.diagnostics.append({"path": str(path), "code": code})

    def add_document(self, document):
        if document is not None:
            self._documents[document.external_document_key] = document

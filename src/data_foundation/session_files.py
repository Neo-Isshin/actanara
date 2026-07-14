"""Filename contracts for external-tool session artifacts."""

from __future__ import annotations


_OPENCLAW_IGNORED_MARKERS = (
    ".bak",
    ".checkpoint",
    ".tmp",
    ".trajectory.",
)


def is_openclaw_session_file(filename: str) -> bool:
    """Return whether *filename* is a supported OpenClaw session JSONL file.

    OpenClaw can place metadata sidecars next to a session, including names such
    as ``<session>.jsonl.codex-app-server.json``. A filename merely containing
    ``.jsonl`` is therefore not sufficient evidence that its contents use the
    JSON Lines format.
    """

    name = str(filename or "")
    if not name or name == "sessions.json" or name.endswith(".lock"):
        return False
    if any(marker in name for marker in _OPENCLAW_IGNORED_MARKERS):
        return False
    return (
        name.endswith(".jsonl")
        or ".jsonl.reset." in name
        or ".jsonl.deleted." in name
    )

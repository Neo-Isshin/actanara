"""Small, stable helpers for Actanara's human-readable CLI output."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence


_READY_STATES = {
    "available",
    "complete",
    "completed",
    "fresh",
    "initialized",
    "ok",
    "passed",
    "promoted",
    "ready",
    "running",
    "stored",
    "success",
    "updated",
}
_ATTENTION_STATES = {
    "approval-required",
    "blocked",
    "missing",
    "needs-attention",
    "pending",
    "plan",
    "rejected",
    "stale",
    "unknown",
    "warn",
    "warning",
}
_FAILED_STATES = {"error", "failed", "failure", "partial", "unavailable"}
_SKIPPED_STATES = {"disabled", "not-selected", "skipped"}


def status_label(value: object) -> str:
    """Return a concise product label for an internal status value."""

    if value is True:
        return "Ready"
    if value is False:
        return "Needs attention"
    normalized = str(value or "unknown").strip().lower().replace("_", "-")
    if normalized in _READY_STATES or normalized.endswith("-applied"):
        return "Ready"
    if normalized in _FAILED_STATES:
        return "Failed"
    if normalized in _SKIPPED_STATES:
        return "Skipped"
    if normalized in _ATTENTION_STATES or normalized.endswith("-required"):
        return "Needs attention"
    return "Needs attention"


def status_marker(value: object) -> str:
    """Return an ASCII marker that remains stable when output is redirected."""

    label = status_label(value)
    if label == "Ready":
        return "[OK]"
    if label == "Failed":
        return "[X]"
    if label == "Skipped":
        return "[-]"
    return "[!]"


def status_item(value: object, ready_text: str, attention_text: str | None = None) -> str:
    """Build one friendly status-list item without exposing internal identifiers."""

    label = status_label(value)
    text = ready_text if label == "Ready" else (attention_text or ready_text)
    return f"{status_marker(value)} {text}"


def render_cli(
    title: str,
    *,
    fields: Sequence[tuple[str, object]] = (),
    sections: Sequence[tuple[str, Iterable[object]]] = (),
    next_steps: Iterable[object] = (),
) -> str:
    """Render the shared title -> summary -> list -> next-step layout."""

    lines = [f"Actanara · {str(title).strip()}"]
    visible_fields = [(str(label), _display(value)) for label, value in fields if value is not None]
    if visible_fields:
        width = max(len(label) for label, _value in visible_fields)
        lines.append("")
        lines.extend(f"  {label.ljust(width)}  {value}" for label, value in visible_fields)

    for heading, raw_items in sections:
        items = [str(item).rstrip() for item in raw_items if item is not None and str(item).strip()]
        if not items:
            continue
        lines.extend(["", str(heading).strip()])
        for item in items:
            lines.extend(f"  {line}" for line in item.splitlines())

    steps = [str(item).rstrip() for item in next_steps if item is not None and str(item).strip()]
    if steps:
        lines.extend(["", "Next step" if len(steps) == 1 else "Next steps"])
        for step in steps:
            lines.extend(f"  {line}" for line in step.splitlines())
    return "\n".join(lines) + "\n"


def friendly_name(value: object, *, fallback: str = "Actanara") -> str:
    """Turn a stable machine name into a readable fallback label."""

    text = str(value or "").strip()
    if not text:
        return fallback
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text.replace("_", " ").replace("-", " "))
    labels = {"api": "API", "llm": "AI", "rag": "Memory search", "ui": "UI"}
    return " ".join(labels.get(part.lower(), part.capitalize()) for part in text.split() if part)


def _display(value: object) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if value is None or str(value).strip() == "":
        return "—"
    return str(value)

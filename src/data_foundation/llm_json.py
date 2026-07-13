"""Helpers for parsing JSON-shaped LLM responses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


class LLMJsonParseError(ValueError):
    """Raised when an LLM response does not contain a valid JSON object."""


@dataclass(frozen=True)
class LLMJsonParseResult:
    data: dict[str, Any]
    json_text: str


_FENCED_JSON_RE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)\s*```")


def _candidate_json_texts(text: str) -> list[str]:
    stripped = (text or "").strip()
    candidates: list[str] = []
    for match in _FENCED_JSON_RE.finditer(stripped):
        candidate = match.group(1).strip()
        if candidate:
            candidates.append(candidate)
    if stripped:
        candidates.append(stripped)
    outer = extract_outer_json_object(stripped)
    if outer and outer not in candidates:
        candidates.append(outer)
    return candidates


def extract_outer_json_object(text: str) -> str | None:
    """Extract the first balanced top-level JSON object from surrounding text."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_llm_json_object(text: str) -> LLMJsonParseResult:
    """Parse a JSON object from raw LLM output, including fences and surrounding text."""
    last_error: Exception | None = None
    for candidate in _candidate_json_texts(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(parsed, dict):
            return LLMJsonParseResult(data=parsed, json_text=candidate)
        last_error = TypeError(f"expected JSON object, got {type(parsed).__name__}")
    detail = f": {last_error}" if last_error else ""
    raise LLMJsonParseError(f"LLM response did not contain a valid JSON object{detail}")

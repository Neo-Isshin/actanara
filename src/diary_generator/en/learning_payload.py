#!/usr/bin/env python3
"""English learning prompt payload and LLM dry-run helpers."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_foundation.llm_execution import execute_llm_message
from data_foundation.paths import load_paths
from data_foundation.settings import resolve_llm_provider

_LLM_PROVIDER = resolve_llm_provider(redact_secrets=True)
LLM_TIMEOUT_SECONDS = int(_LLM_PROVIDER.get("timeoutSeconds") or 120)
THINKING_MODE = os.getenv("LLM_THINKING_MODE", "off").strip().lower()

SYSTEM_LEARNING_EN = "You are a precise technical audit assistant. Output structured Markdown only, with no preface or explanation."

PROMPT_LEARNING_EN = """Analyze the provided English diary summary and produce a structured Markdown learning and infrastructure audit.

Target date: {date}

1. Lessons:
   - Look for serious bugs caused by incorrect operation, major architecture mistakes, hidden API pitfalls, unstable prompt behavior, evidence drift, and performance bottlenecks.
   - Each lesson must be split into exactly these subheadings: `Problem`, `Root Cause`, and `Recommendation`.
   - Preserve concrete evidence such as file paths, settings keys, commands, timestamps, task ids, and contract names.
   - Do not invent incidents that are not present in the summary.

2. Infrastructure Updates:
   - Only two infrastructure entity types are valid:
     - `device`: physical devices, routers, servers, PCs, hosts, cloud servers, VPS, remote instances, and LAN instances.
     - `service`: Docker containers, binary services, launchd/systemd services, API services, database services, embedding servers, dashboard servers, and listening-port services.
   - Include only directly evidenced deployments, fixes, port/endpoint/path/connection changes, online/offline changes, or service state changes.
   - Do not classify code modules, functions, documents, tasks, abstract subsystems, or configuration keys as infrastructure unless they identify a real running device or service.
   - Never output password, token, API key, cookie, private key, Bearer, Authorization, or credential values. For credential changes write only `credential_rotated`, `secretRef_changed`, or `[redacted]`.
   - Only include items explicitly supported by the summary.

Output exactly this Markdown structure. Do not output JSON, YAML, code fences, preface, or explanation:

# {date} Learning and Infrastructure Audit

## Lessons
### [agent-or-source] Short problem title
#### Problem
Concrete problem.
#### Root Cause
Concrete root cause.
#### Recommendation
Concrete recommendation.

## Infrastructure Updates
| Entity ID | Type | Object | Host/Location | Change Type | Field | Change | Current Value | Evidence | Confidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| existing entityId or new | device/service | Object name | Host or location | created/updated/port_changed/endpoint_changed/path_changed/status_changed/deployed/fixed/credential_rotated | port/endpoint/path/status/credential/other | Change description | `Non-sensitive current value or [redacted]` | Evidence phrase from the report | high/medium/low |

If there are no lessons or infrastructure updates, write `None` under that section. Do not omit either main section.

Diary summary:
{summary}
"""


def _thinking_instruction() -> str:
    if THINKING_MODE == "low":
        return "\nReasoning effort: low. Extract direct lessons and infrastructure updates only."
    if THINKING_MODE == "medium":
        return "\nReasoning effort: medium. Use moderate reasoning only to connect problem, root cause, and recommendation."
    if THINKING_MODE in {"off", "disabled", "disable"}:
        return "\nReasoning effort: off. Do not expose reasoning; directly output the structured Markdown."
    return ""


def prepare_learning_summary(summary_text: str) -> str:
    text = re.sub(r"\n## Scheduled Jobs\n[\s\S]*?(?=\n## Notes|\n```|\Z)", "\n## Scheduled Jobs\nNone\n", summary_text)
    text = re.sub(r"\n```(?:json|yaml)?\n[\s\S]*?\n```\s*$", "", text).rstrip()
    return text


def build_prompt(date_str: str, summary_text: str) -> str:
    return PROMPT_LEARNING_EN.replace("{date}", date_str).replace("{summary}", prepare_learning_summary(summary_text))


def call_llm(prompt: str) -> str:
    started = time.time()
    print("   [EN-LEARNING-LLM-START] learning audit: max_tokens=8192", flush=True)
    content = execute_llm_message(
        system=SYSTEM_LEARNING_EN + _thinking_instruction(),
        prompt=prompt,
        temperature=0.1,
        max_tokens=8192,
        timeout=LLM_TIMEOUT_SECONDS,
        thinking_mode=THINKING_MODE,
        paths=load_paths(),
        pass_id="learning",
        chunk_id="audit",
        label="learning audit",
    ).text.strip()
    cleaned = re.sub(r"<(think|thinking)>[\s\S]*?</\1>", "", content).strip()
    print(f"   [EN-LEARNING-LLM-END] learning audit: {time.time() - started:.1f}s, chars={len(cleaned):,}", flush=True)
    return cleaned


def generate_from_summary(date_str: str, summary_text: str) -> dict[str, object]:
    markdown = call_llm(build_prompt(date_str, summary_text))
    return {
        "status": "generated",
        "pipelineLanguageProfile": "en",
        "pass": "learning",
        "markdown": markdown,
    }


def load_fixture(path: Path) -> tuple[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("English learning fixture must be an object")
    date_str = str(payload.get("date") or "")
    summary = str(payload.get("summary") or "")
    if not date_str or not summary:
        raise ValueError("English learning fixture requires date and summary")
    return date_str, summary

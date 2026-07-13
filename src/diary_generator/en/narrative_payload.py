#!/usr/bin/env python3
"""English narrative prompt payload and LLM dry-run helpers."""

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

from data_foundation.llm_transport import send_anthropic_message, send_openai_compatible_message
from data_foundation.settings import resolve_llm_provider

_LLM_PROVIDER = resolve_llm_provider(redact_secrets=False)
API_KEY = _LLM_PROVIDER["apiKey"]
API_HOST = _LLM_PROVIDER["endpoint"]
MODEL = _LLM_PROVIDER["model"]
API_TYPE = _LLM_PROVIDER.get("api") or "anthropic-messages"
LLM_TIMEOUT_SECONDS = int(_LLM_PROVIDER.get("timeoutSeconds") or 180)
THINKING_MODE = os.getenv("LLM_THINKING_MODE", "off").strip().lower()

SYSTEM_PARTIAL_EN = "You are a precise AI work-log summarization assistant."
SYSTEM_INTEGRATION_EN = "You are a precise technical diary editor. Start directly with '## Daily Overview'."

PROMPT_PARTIAL_EN = """You are a professional technical work-log summarization assistant. Extract the core substance from the log data below ({agent_info}, business window 04:00 to next-day 04:00 local time).

Requirements:
1. Extract task intent, execution steps, errors, performance signals, and important notices.
2. For important notices, include severity (critical/medium/low), observed behavior, and potential risk.
3. Preserve concrete technical details such as code paths, fix logic, commands, errors, stack traces, hashes, and report names.
4. Preserve observed timestamps. Do not rewrite, infer, or invent time ranges.
5. Write in English only.

Log data:
{raw_text}
"""

PROMPT_INTEGRATION_EN = """You are a professional technical diary integration assistant. Merge the provided agent summaries into one high-quality English diary.

Strict layout:
1. Output exactly these second-level headings:
## Daily Overview
## Agent Work
## Important Notices
## Scheduled Jobs
## Notes

2. Daily Overview:
   - Objectively summarize the day's strategic decisions, architecture changes, and major progress.
   - Use bullets shaped as `* **[Core title]**: concise main statement`.
   - Under each main bullet, include 2-3 nested `-` detail bullets when evidence exists.

3. Agent Work:
   - Group by agent using `### [Agent name]`.
   - Inside each agent, use time blocks formatted as `**[Label HH:MM-HH:MM] - Business summary**`.
   - Use only timestamps or time ranges that appear in the provided summaries. If only a single timestamp is available, use that timestamp as both ends of a narrow range, for example `14:30-14:30`.
   - Do not infer, normalize, or invent time ranges.
   - Use only `-` bullets for concrete work items.

4. Important Notices:
   - Sort by severity: critical, medium, low.
   - Use numbered items: `1. **[Severity] - Title**`.
   - Include Observed, Risk, and Suggested action bullets.
   - If there are no notices, write `None`.

5. Scheduled Jobs:
   - Summarize scheduled-job results if present.
   - If none are present, write `None`.

Agent summary data:
{raw_text}
"""


def _thinking_instruction() -> str:
    if THINKING_MODE == "low":
        return "\nReasoning effort: low. Summarize directly; avoid multi-step speculation."
    if THINKING_MODE == "medium":
        return "\nReasoning effort: medium. Use moderate reasoning only when merging conflicting evidence."
    if THINKING_MODE in {"off", "disabled", "disable"}:
        return "\nReasoning effort: off. Do not expose reasoning; directly produce the requested summary."
    return ""


def build_raw_text(entries: list[dict], max_chars: int = 800) -> str:
    lines = []
    for entry in entries:
        role = str(entry.get("role") or "")
        timestamp = str(entry.get("time") or "")
        content = str(entry.get("content") or "")
        if len(content) > max_chars:
            content = content[:max_chars].rstrip() + "..."
        lines.append(f"[{timestamp}] {role}: {content}")
    return "\n".join(lines)


def partial_prompt(agent: str, entries: list[dict], max_chars: int = 800) -> str:
    return PROMPT_PARTIAL_EN.replace("{agent_info}", agent).replace("{raw_text}", build_raw_text(entries, max_chars))


def integration_prompt(agent_summaries: dict[str, str]) -> str:
    blocks = [f"=== Agent: [{agent}] summary ===\n{summary}" for agent, summary in agent_summaries.items()]
    return PROMPT_INTEGRATION_EN.replace("{raw_text}", "\n\n".join(blocks))


def call_llm(prompt: str, is_integration: bool = False, label: str | None = None, max_tokens: int | None = None) -> str:
    system = SYSTEM_INTEGRATION_EN if is_integration else SYSTEM_PARTIAL_EN
    system += _thinking_instruction()
    call_label = label or ("english final integration" if is_integration else "english partial")
    started = time.time()
    output_budget = max_tokens or (8192 if is_integration else 4096)
    print(f"   [EN-NARRATIVE-LLM-START] {call_label}: max_tokens={output_budget}", flush=True)
    sender = send_anthropic_message if API_TYPE == "anthropic-messages" else send_openai_compatible_message
    content = sender(
        endpoint=API_HOST,
        api_key=API_KEY,
        model=MODEL,
        system=system,
        prompt=prompt,
        temperature=0.05,
        max_tokens=output_budget,
        timeout=LLM_TIMEOUT_SECONDS,
        thinking_mode=THINKING_MODE,
    ).strip()
    cleaned = re.sub(r"<(think|thinking)>[\s\S]*?</\1>|```json[\s\S]*?```", "", content).strip()
    print(f"   [EN-NARRATIVE-LLM-END] {call_label}: {time.time() - started:.1f}s, chars={len(cleaned):,}", flush=True)
    return cleaned


def generate_from_entries(entries_by_agent: dict[str, list[dict]]) -> dict[str, object]:
    summaries: dict[str, str] = {}
    for agent, entries in entries_by_agent.items():
        if not entries:
            continue
        summaries[agent] = call_llm(partial_prompt(agent, entries), label=f"{agent} partial")
    final = call_llm(integration_prompt(summaries), is_integration=True, label="english final integration")
    return {
        "status": "generated",
        "pipelineLanguageProfile": "en",
        "pass": "narrative",
        "agentSummaries": summaries,
        "markdown": final,
    }


def load_fixture(path: Path) -> dict[str, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("English narrative fixture must be an object keyed by agent name")
    result = {}
    for agent, entries in payload.items():
        if not isinstance(entries, list):
            raise ValueError(f"English narrative fixture agent {agent!r} must contain a list")
        result[str(agent)] = [entry for entry in entries if isinstance(entry, dict)]
    return result

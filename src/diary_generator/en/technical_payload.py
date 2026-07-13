#!/usr/bin/env python3
"""English technical prompt payload and LLM dry-run helpers."""

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

TASK_RULES_EN = """Technical Chronicle Contract:
1. This pass produces a high-value engineering chronicle. It does not maintain Nova-Task authoritative graph state.
2. The report must preserve the engineering causal chain: objective, obstacles, detours, implementation path, verification evidence, residual risks, and reusable lessons.
3. Do not write the report as a task queue, candidate queue, or active-graph audit table.
4. You may output lightweight Task Hooks, but they are non-authoritative markers for the Nova-Task reconciliation pass.
5. Do not output `nova_task:` YAML, JSON, a second machine-executable payload, or any language implying task graph writes.
6. Task hooks may describe facts and suggestions: project/workspace hint, task candidate, parent-child hint, suggested level, and evidence. Do not invent authoritative `NT-*` ids.
7. Hierarchy semantics: Level 1 is a project/product root and always requires user approval; Level 2 is a durable subsystem, database/authority, connector, product surface, ingestion/projection, settings/review flow, release gate, or operational stream; Level 3 is a deliverable task; Level 4 is a subtask; Level 5 is a small action/check.
8. Do not promote one file edit, one command, one bug, one visual tweak, or one failed run to Level 2. Those are usually Level 4/5 or evidence-only.
9. If there is no material engineering progress, write `no_material_technical_progress` and explain why.
"""

SYSTEM_TECHNICAL_EN = (
    "You are a senior systems architect extracting a high-value engineering chronicle "
    "for the learning pass and the Nova-Task reconciliation pass.\n"
    + TASK_RULES_EN
)

PROMPT_TECHNICAL_PARTIAL_EN = """Technical evidence packet extraction.

Extract technical progress from this {agent_info} log fragment.

Requirements:
1. Extract objectives, obstacles, detours, implementation path, verification evidence, and residual risks.
2. Preserve concrete files, commands, paths, errors, hashes, report names, and verifiable evidence when present.
3. Mention possible project/workspace/task hooks, but do not invent authoritative task ids.
4. Preserve explicit infrastructure facts: devices, hosts, VPS/cloud/remote/LAN instances, services, containers, listening ports, endpoints, paths, online/offline state, deployments, fixes, and configuration changes.
5. If there is no material engineering progress, write `no_material_technical_progress` and explain why.
6. Treat tiny work, temporary questions, one-off environment checks, and pure exploration as background or evidence-only.
7. Preserve observed timestamps and evidence wording. Do not invent time ranges.
8. Do not output YAML, JSON, or `nova_task` blocks.
9. Write in English only.

Input log:
{raw_text}
"""

PROMPT_TECHNICAL_INTEGRATION_EN = """Senior architect technical report - Engineering Chronicle mode.

Integrate the unified technical evidence stream into a high-value engineering chronicle.
The main consumers are:
- the learning pass, which needs a clean engineering causal chain;
- the Nova-Task reconciliation pass, which needs lightweight hooks rather than direct graph-write YAML.

Inputs:
- Reference active graph context, for naming context only: {task_graph_context}
- Technical evidence stream: {raw_text}

Strict output format:

# {date} Technical Progress Report

If there is no material engineering progress today, write `no_material_technical_progress` under "Engineering Objectives and Outcomes" and explain why.

## Engineering Objectives and Outcomes
List the real engineering objectives, outcomes, and current state by project/workstream. Explain why each item has engineering value.

## Obstacles, Root Causes, and Detours
Record key difficulties, wrong assumptions, failed paths, tool/environment/data issues, root causes, and how to avoid repeating them.

## Implementation Path and Key Decisions
Record the final implementation approach, important files/modules/interfaces/data contracts, and why alternatives were rejected.

## Verification Evidence
List tests, commands, health checks, compile checks, file paths, commits, reports, artifacts, and any missing verification.

## Residual Risks and Follow-up Observation
List remaining failure modes, required regression checks, user confirmations, and cross-day observations.

## Reusable Lessons
Extract patterns, anti-patterns, architecture boundaries, process lessons, and verification strategies for the learning pass.

## Infrastructure Narrative Evidence
Record only direct infrastructure facts for the learning pass to reconcile into device/service changes.
- Device scope: physical devices, routers, servers, PCs, hosts, cloud servers, VPS, remote instances, and LAN instances.
- Service scope: Docker containers, binary services, launchd/systemd services, API services, database services, embedding servers, dashboard servers, and listening-port services.
- Each item should state object, type, host/location, port or endpoint/path when available, change, and evidence source.
- Do not record password, token, API key, cookie, private key, or credential values. Write only credential rotated, secretRef changed, or redacted.

If there are no infrastructure facts, write "None".

## Nova-Task Reconciliation Hooks
Use Markdown bullets only. Do not output YAML or JSON. Each hook should include:
- hook_type: task_candidate | parent_child_hint | project_workspace_hint | status_hint | evidence_only
- title:
- suggested_level: 1 | 2 | 3 | 4 | 5 | unknown
- project_or_workspace:
- parent_hint:
- evidence:
- confidence: high | medium | low

If there are no hooks worth sending to Nova-Task, write "None".
"""


def _thinking_instruction() -> str:
    if THINKING_MODE == "low":
        return "\nReasoning effort: low. Focus on direct engineering evidence extraction."
    if THINKING_MODE == "medium":
        return "\nReasoning effort: medium. Use moderate reasoning only for causal chains, detours, and residual risks."
    if THINKING_MODE in {"off", "disabled", "disable"}:
        return "\nReasoning effort: off. Do not expose reasoning; directly output the structured chronicle."
    return ""


def build_raw_text(entries: list[dict], max_chars: int = 1000) -> str:
    lines = []
    for entry in entries:
        source = str(entry.get("source") or "")
        role = str(entry.get("role") or "")
        timestamp = str(entry.get("time") or "")
        content = str(entry.get("content") or "")
        if len(content) > max_chars:
            content = content[:max_chars].rstrip() + "..."
        prefix = f"[{timestamp}]"
        if source:
            prefix += f"[{source}]"
        lines.append(f"{prefix} {role}: {content}")
    return "\n".join(lines)


def partial_prompt(agent: str, entries: list[dict], max_chars: int = 1000) -> str:
    return PROMPT_TECHNICAL_PARTIAL_EN.replace("{agent_info}", agent).replace("{raw_text}", build_raw_text(entries, max_chars))


def integration_prompt(date_str: str, task_graph_context: str, evidence_packets: dict[str, str]) -> str:
    stream = "\n\n".join(f"=== Source: [{source}] evidence ===\n{packet}" for source, packet in evidence_packets.items())
    return (
        PROMPT_TECHNICAL_INTEGRATION_EN.replace("{date}", date_str)
        .replace("{task_graph_context}", task_graph_context)
        .replace("{raw_text}", stream)
    )


def call_llm(prompt: str, label: str | None = None, max_tokens: int = 6144) -> str:
    call_label = label or "english technical llm"
    started = time.time()
    print(f"   [EN-TECH-LLM-START] {call_label}: max_tokens={max_tokens}", flush=True)
    sender = send_anthropic_message if API_TYPE == "anthropic-messages" else send_openai_compatible_message
    content = sender(
        endpoint=API_HOST,
        api_key=API_KEY,
        model=MODEL,
        system=SYSTEM_TECHNICAL_EN + _thinking_instruction(),
        prompt=prompt,
        temperature=0.05,
        max_tokens=max_tokens,
        timeout=LLM_TIMEOUT_SECONDS,
        thinking_mode=THINKING_MODE,
    ).strip()
    cleaned = re.sub(r"<(think|thinking)>[\s\S]*?</\1>", "", content).strip()
    print(f"   [EN-TECH-LLM-END] {call_label}: {time.time() - started:.1f}s, chars={len(cleaned):,}", flush=True)
    return cleaned


def generate_from_entries(date_str: str, entries_by_source: dict[str, list[dict]], task_graph_context: str) -> dict[str, object]:
    packets: dict[str, str] = {}
    for source, entries in entries_by_source.items():
        if not entries:
            continue
        packets[source] = call_llm(partial_prompt(source, entries), label=f"{source} technical evidence", max_tokens=4096)
    report = call_llm(
        integration_prompt(date_str, task_graph_context, packets),
        label="english technical final integration",
        max_tokens=8192,
    )
    return {
        "status": "generated",
        "pipelineLanguageProfile": "en",
        "pass": "technical",
        "evidencePackets": packets,
        "markdown": report,
    }


def load_fixture(path: Path) -> tuple[dict[str, list[dict]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("English technical fixture must be an object")
    entries = payload.get("entriesBySource")
    if not isinstance(entries, dict):
        raise ValueError("English technical fixture requires entriesBySource object")
    task_graph_context = str(payload.get("taskGraphContext") or "Nova-Task v2 active graph unavailable.")
    normalized = {}
    for source, source_entries in entries.items():
        if not isinstance(source_entries, list):
            raise ValueError(f"English technical fixture source {source!r} must contain a list")
        normalized[str(source)] = [entry for entry in source_entries if isinstance(entry, dict)]
    return normalized, task_graph_context

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import urllib.request
import re
import tempfile
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
import config
from data_foundation.diary_paths import diary_technical_report_path
from data_foundation.filtered_dialogue import load_filtered_source_entries
from data_foundation.environment_assets import (
    parse_technical_environment_output,
    write_environment_observation_ledger,
)
from data_foundation.settings import (
    is_environment_reconciliation_enabled,
    is_nova_task_enabled,
    resolve_llm_provider,
)
from data_foundation.llm_execution import execute_llm_message
from data_foundation.nova_task import render_task_graph_context
from data_foundation.paths import load_paths
from data_foundation.time import business_today

_LLM_PROVIDER = resolve_llm_provider(redact_secrets=True)
THINKING_MODE = os.getenv("LLM_THINKING_MODE", "off").strip().lower()
def _runtime_diary_root() -> Path:
    return load_paths().diary_dir


DEFAULT_GATE_RULE = {"step": 2, "t": 400}


def _technical_gate_rule(manual_rules, source_name):
    if isinstance(manual_rules, dict):
        rule = manual_rules.get(source_name) or manual_rules.get("default")
        if isinstance(rule, dict):
            return rule
    return DEFAULT_GATE_RULE


def _thinking_instruction():
    if THINKING_MODE == "low":
        return "\n推理强度：low。任务是工程事实提纯，不需要深度发散推理；优先保留目标、阻碍、实现路径和验证证据。"
    if THINKING_MODE == "medium":
        return "\n推理强度：medium。只在因果链、弯路归纳和残余风险判断时使用适度推理，避免冗长思考。"
    if THINKING_MODE in {"off", "disabled", "disable"}:
        return "\n推理强度：off。不要展开深度思考；直接基于证据输出结构化技术报告。"
    return ""


def _positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


PIPELINE_CONCURRENCY = max(1, min(_positive_int(_LLM_PROVIDER.get("pipelineConcurrency"), 3), 16))
PIPELINE_GATE_TOKENS = max(1000, _positive_int(_LLM_PROVIDER.get("pipelineGateTokens"), 30000))
TECHNICAL_FINAL_GATE_TOKENS = min(PIPELINE_GATE_TOKENS, 8000)
TECHNICAL_FINAL_MAX_TOKENS = 6144
TECHNICAL_PRECOMPRESS_MAX_TOKENS = 3072
MAX_GATE_SPLIT_CHUNKS = max(1, _positive_int(os.getenv("ACTANARA_TECHNICAL_MAX_GATE_SPLIT_CHUNKS"), 256))
MAX_FINAL_PRECOMPRESS_CHUNKS = max(1, _positive_int(os.getenv("ACTANARA_TECHNICAL_MAX_FINAL_PRECOMPRESS_CHUNKS"), 256))

try:
    import tiktoken
except ImportError:
    tiktoken = None

# ===================== PROMPT MODULES =====================

CORE_RULES = """【Technical Chronicle Core】
1. 本 pass 的权威职责是生成高价值工程技术报告。
2. 报告围绕工程因果链：目标、阻碍、弯路、实现路径、验证证据、残余风险、可复用经验。
3. 无实质工程进展时明确写 no_material_technical_progress，不为了填充报告编造 RCA。"""

TASK_RULES = """【Nova-Task Hook Module】
1. 不直接维护 Nova-Task active graph，只输出轻量候选 hooks。
2. 不得输出 nova_task YAML/JSON、权威 NT-* ID，或暗示已写入 task graph。
3. 单文件修改、一次命令或一个 bug 通常只是 Level 4/5 或 evidence only，不得提升为长期子系统。"""

ENVIRONMENT_RULES = """【Environment Observation Module】
1. 只发现证据中真实存在的设备与常驻运行服务；它们不是 Skill、Lesson、任务或工程成果。
2. 每项必须引用输入中的 E000001 形式证据。没有直接证据就不要输出。
3. service 只有在证据表明它常驻运行、由服务管理器托管、已部署监听、通过健康检查或已配置自动启动时才召回；源码模块、Pipeline 阶段、函数、提示词、CLI 和一次性进程不是服务。
4. commit、技术报告、环境账本、lock/build artifact 和其他工程产出不属于基础设施，不得放入本模块。
5. 只描述“看到了什么”；不要判断类别细分、状态、变更类型、版本、宿主、位置、身份或置信度。这些裁决全部由独立 Environment Reconciliation 完成。"""

PROMPT_TECHNICAL_PARTIAL_BASE = """【技术编年史证据包提炼】
请从以下 {agent_info} 的日志片段中提炼技术进展。
要求：
1. 提炼目标、阻碍、弯路、实现路径、验证证据、残余风险。
2. 保留输入中的 E000001 形式证据标记，以及文件、命令、错误、commit/report 名称等可验证 evidence。
3. 区分高价值工程事实和低价值噪音；临时问询与一次性探索只作为背景或略过。
4. 若日志没有实质工程进展，明确输出 no_material_technical_progress，并说明原因。
{module_rules}

【输入数据】
- 原始日志：{raw_text}
"""

CORE_INTEGRATION = """【高级架构师技术报告 - Engineering Chronicle 模式】
请根据统一技术证据流，整合为一份高价值技术进展报告。

【输入数据】
{input_context}
- 技术证据流或超闸证据包：{{raw_text}}

【输出格式：严禁偏离】

# {{date}} 技术进展报告

如果当天没有实质工程进展，请在“一、工程目标与完成结果”中写 `no_material_technical_progress` 并说明原因。

## 一、工程目标与完成结果
按项目/工作线列出真正发生的工程目标、结果和当前状态。

## 二、阻碍、根因与弯路
写清现象、根因、错误路径以及后续如何避免。

## 三、实现路径与关键决策
记录最终实现方式、重要模块/接口/数据契约变化及取舍理由。

## 四、验证证据
列出测试、health check、编译检查、关键文件和 artifact；未验证必须明示。

## 五、残余风险与后续观察
只列仍可能失败、需回归、需用户确认或跨日观察的事项。

## 六、可沉淀经验
提炼模式、反模式、架构边界和验证策略；不要写成 Skill 提案。
"""

TASK_INTEGRATION = """
## 八、Nova-Task Reconciliation Hooks
只输出 Markdown 列表，不要 YAML/JSON。每条尽量包含 hook_type、title、suggested_level、project_or_workspace、parent_hint、evidence、confidence。没有则写“无”。
"""

ENVIRONMENT_INTEGRATION = """
## 七、环境观察叙事摘要
只用泛化名称说明证据中出现的设备、常驻运行服务、连接关系与拓扑变化。若证据足以支持多个节点及其连接，可以附一幅简短 Mermaid diagram；不要为了成图补写节点或连线。公开 Markdown 不写具体 IP、hostname、URL、绝对路径、端口或凭证值。commit、技术报告、账本和构建物不得进入本节。没有则写“无”。
"""

ENVIRONMENT_PRIVATE_INTEGRATION = """
## 九、环境观察私有账本
最后输出且只输出一个 `json` 代码块：
{
  "schema": "actanara.environment-observations.v2",
  "businessDate": "{{date}}",
  "observations": [
    {
      "assetClass": "device | service",
      "name": "证据中用于称呼该设备或常驻服务的简短名称",
      "summary": "只复述证据明确说明的观察，不做身份、状态或因果裁决",
      "evidenceRefs": ["E000001"]
    }
  ]
}

规则：这是召回候选而非最终固化。device 是证据明确提及的真实物理设备、虚拟机、云实例、网络或存储设备；service 必须有常驻运行、托管、部署监听、健康检查或自动启动证据。仅存在源码/配置、手动运行一次、Pipeline 阶段、模块、函数、提示词或 CLI 不构成 service。commit、技术报告、环境账本、lock/build artifact 与普通工程产出不属于基础设施。普通代码编辑、计划、任务、配置键和诊断目标也不是观察对象。不要输出 kind/state/changeType/host/location/endpoint/port/path/version/project/confidence，不要判断 existing/new，不得生成 entityId。每项必须有当前 evidenceRefs；不得写任何凭证值。没有观察时 observations=[]。
"""


def resolve_technical_prompt_modules(paths=None):
    selected = paths or load_paths()
    return {
        "task": bool(is_nova_task_enabled(selected)),
        "environment": bool(is_environment_reconciliation_enabled(selected)),
    }


def build_technical_system_prompt(modules):
    blocks = ["你是一个高级系统架构师。", CORE_RULES]
    if modules.get("task"):
        blocks.append(TASK_RULES)
    if modules.get("environment"):
        blocks.append(ENVIRONMENT_RULES)
    return "\n".join(blocks) + _thinking_instruction()


def build_technical_partial_template(modules):
    rules = []
    if modules.get("task"):
        rules.append("5. 保留可能的 project/workspace/task hook，但不输出权威任务 ID。")
    if modules.get("environment"):
        rules.append("6. 只保留有直接证据的设备、常驻/托管/监听服务及其 E 编号；排除 commit、报告、账本、构建物和仅存在于源码的模块，不在此阶段判定状态、版本、宿主、locator 或 existing/new。")
    return PROMPT_TECHNICAL_PARTIAL_BASE.replace("{module_rules}", "\n".join(rules))


def build_technical_integration_template(modules):
    context = []
    if modules.get("task"):
        context.append("- 参考 active graph context（只理解已有项目/子系统名称，不用于写入）：{{task_graph_context}}")
    template = CORE_INTEGRATION.replace("{input_context}", "\n".join(context))
    if modules.get("environment"):
        template += ENVIRONMENT_INTEGRATION
    if modules.get("task"):
        template += TASK_INTEGRATION
    if modules.get("environment"):
        template += ENVIRONMENT_PRIVATE_INTEGRATION
    return template


SYSTEM_PROMPT = build_technical_system_prompt({"task": True, "environment": True})
PROMPT_TECHNICAL_PARTIAL = build_technical_partial_template({"task": True, "environment": True})
PROMPT_TECHNICAL_INTEGRATION = build_technical_integration_template({"task": True, "environment": True})

# ===================== CORE LOGIC =====================

def _llm_chunk_id(label):
    raw = str(label or "technical llm").strip()
    slug = re.sub(r"[^\w.-]+", "-", raw, flags=re.UNICODE).strip("-_.").casefold()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{(slug[:80] or 'technical-llm')}-{digest}"

def call_llm(prompt, label=None, max_tokens=16384, *, modules=None):
    call_label = label or "technical llm"
    token_estimate = get_token_count(prompt)
    started = time.time()
    print(
        f"   [TECH-LLM-START] {call_label}: prompt≈{token_estimate:,} tokens, max_tokens={max_tokens}",
        flush=True,
    )
    try:
        content = execute_llm_message(
            system=build_technical_system_prompt(
                modules or {"task": True, "environment": True}
            ),
            prompt=prompt,
            temperature=0.05,
            max_tokens=max_tokens,
            thinking_mode=THINKING_MODE,
            paths=load_paths(),
            pass_id="technical",
            label=call_label,
            chunk_id=_llm_chunk_id(call_label),
        ).text
        cleaned = re.sub(r'<(think|思考)>[\s\S]*?</\1>', '', content).strip()
        print(
            f"   [TECH-LLM-END] {call_label}: {time.time() - started:.1f}s, chars={len(cleaned):,}",
            flush=True,
        )
        return cleaned
    except Exception as exc:
        print(f"   [TECH-LLM-ERROR] {call_label}: {time.time() - started:.1f}s, {exc}", flush=True)
        raise

def build_raw_text(entries, max_chars):
    text = ""
    for e in entries:
        role, t_str, content = e.get("role", ""), e.get("time", ""), e.get("content", "")
        if len(content) > max_chars: content = content[:max_chars] + "..."
        text += f"[{t_str}] {role}: {content}\n"
    return text


def build_unified_evidence_text(entries, truncation_by_source):
    lines = []
    for entry in sorted(entries, key=lambda item: (str(item.get("time", "")), str(item.get("source", "")))):
        source = str(entry.get("source") or "unknown")
        limit = int(truncation_by_source.get(source, 400) or 400)
        role = str(entry.get("role") or "")
        t_str = str(entry.get("time") or "")
        content = str(entry.get("content") or "")
        if len(content) > limit:
            content = content[:limit] + "..."
        evidence_id = str(entry.get("_technicalEvidenceId") or "")
        prefix = f"[{evidence_id}]" if evidence_id else ""
        lines.append(f"{prefix}[{t_str}][{source}][{role}] {content}")
    return "\n".join(lines)


def get_token_count(text):
    if tiktoken:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    return len(str(text)) // 2


def _partial_prompt(agent_info, entries, max_chars, modules=None):
    raw_text = build_raw_text(entries, max_chars)
    template = build_technical_partial_template(
        modules or {"task": True, "environment": True}
    )
    return template.format(agent_info=agent_info, raw_text=raw_text)


def _unified_partial_prompt(chunk_label, entries, truncation_by_source, modules=None):
    raw_text = build_unified_evidence_text(entries, truncation_by_source)
    template = build_technical_partial_template(
        modules or {"task": True, "environment": True}
    )
    return template.format(agent_info=chunk_label, raw_text=raw_text)


def _largest_gate_fitting_prefix(entries, agent_info, max_chars, modules=None):
    best = 0
    low, high = 1, len(entries)
    while low <= high:
        mid = (low + high) // 2
        prompt = _partial_prompt(agent_info, entries[:mid], max_chars, modules)
        if get_token_count(prompt) <= PIPELINE_GATE_TOKENS:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def _split_entries_by_gate(entries, agent_info, max_chars, modules=None):
    chunks = []
    index = 0
    while index < len(entries):
        if len(chunks) + 1 >= MAX_GATE_SPLIT_CHUNKS:
            chunks.append(entries[index:])
            break
        size = _largest_gate_fitting_prefix(entries[index:], agent_info, max_chars, modules)
        if size <= 0:
            size = 1
        chunks.append(entries[index:index + size])
        index += size
    return chunks


def _summarize_entries_with_gate(agent_info, entries, max_chars, modules=None):
    prompt = _partial_prompt(agent_info, entries, max_chars, modules)
    tokens = get_token_count(prompt)
    if tokens <= PIPELINE_GATE_TOKENS:
        return call_llm(prompt, label=agent_info, max_tokens=6144, modules=modules)
    chunks = _split_entries_by_gate(entries, agent_info, max_chars, modules)
    print(
        f"   [TECH-GATE] {agent_info} estimated {tokens:,} tokens > {PIPELINE_GATE_TOKENS:,}; "
        f"split into {len(chunks)} chunks.",
        flush=True,
    )
    summaries = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_prompt = _partial_prompt(f"{agent_info} Chunk {index}", chunk, max_chars, modules)
        result = call_llm(chunk_prompt, label=f"{agent_info} Chunk {index}", max_tokens=6144, modules=modules)
        if result:
            summaries.append(result)
    return "\n\n".join(summaries)


def _summarize_agent(agent, entries, rule, modules=None):
    if rule['step'] == 2:
        print(f"Auditing Agent: {agent} (Step 2, t={rule['t']})")
        return _summarize_entries_with_gate(agent, entries, rule['t'], modules)
    if rule['step'] == 3:
        print(f"Auditing Agent: {agent} (Step 3, Time Split)")
        summaries = []
        for i in range(4):
            chunk = entries[len(entries)//4 * i : len(entries)//4 * (i+1)]
            if not chunk:
                continue
            result = _summarize_entries_with_gate(f"{agent} Block {i}", chunk, rule['t'], modules)
            if result:
                summaries.append(result)
        return "\n\n".join(summaries)
    return _summarize_entries_with_gate(agent, entries, rule.get("t", 400), modules)


def _split_text_for_final_gate(text, modules=None):
    lines = text.splitlines()
    chunks = []
    index = 0
    while index < len(lines):
        if len(chunks) + 1 >= MAX_FINAL_PRECOMPRESS_CHUNKS:
            chunks.append("\n".join(lines[index:]))
            break
        best = 0
        low, high = 1, len(lines) - index
        while low <= high:
            mid = (low + high) // 2
            raw_text = "\n".join(lines[index:index + mid])
            prompt = build_technical_partial_template(
                modules or {"task": True, "environment": True}
            ).format(
                agent_info="technical final pre-compression",
                raw_text=raw_text,
            )
            if get_token_count(prompt) <= PIPELINE_GATE_TOKENS:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        if best <= 0:
            best = 1
        chunks.append("\n".join(lines[index:index + best]))
        index += best
    return chunks


def _build_final_prompt(
    date_str,
    task_graph_context,
    environment_graph_context,
    combined,
    modules=None,
):
    template = build_technical_integration_template(
        modules or {"task": True, "environment": True}
    )
    return (
        template.replace("{{date}}", date_str)
        .replace("{date}", date_str)
        .replace("{{task_graph_context}}", task_graph_context[:2000])
        .replace("{{raw_text}}", combined)
    )


def _call_final_integration(date_str, task_graph_context, environment_graph_context, combined, modules=None):
    final_prompt = _build_final_prompt(
        date_str, task_graph_context, environment_graph_context, combined, modules
    )
    tokens = get_token_count(final_prompt)
    if tokens <= TECHNICAL_FINAL_GATE_TOKENS:
        result = call_llm(final_prompt, label="technical final integration", max_tokens=TECHNICAL_FINAL_MAX_TOKENS, modules=modules)
        if result:
            return result
        print("   [TECH-FINAL-GATE] final integration failed; retrying with bounded prompt.", flush=True)
    print(
        f"   [TECH-FINAL-GATE] final integration estimated {tokens:,} tokens > final target "
        f"{TECHNICAL_FINAL_GATE_TOKENS:,}; pre-compressing summaries.",
        flush=True,
    )
    reduced = []
    for index, chunk in enumerate(_split_text_for_final_gate(combined, modules), start=1):
        prompt = build_technical_partial_template(
            modules or {"task": True, "environment": True}
        ).format(
            agent_info=f"technical final pre-compression #{index}",
            raw_text=chunk,
        )
        result = call_llm(
            prompt,
            label=f"technical final pre-compression #{index}",
            max_tokens=TECHNICAL_PRECOMPRESS_MAX_TOKENS,
            modules=modules,
        )
        reduced.append(result if result else chunk)
    reduced_summary = "\n\n".join(reduced)
    final_prompt = _build_final_prompt(date_str, task_graph_context, environment_graph_context, reduced_summary, modules)
    if get_token_count(final_prompt) <= TECHNICAL_FINAL_GATE_TOKENS:
        result = call_llm(final_prompt, label="technical final integration", max_tokens=TECHNICAL_FINAL_MAX_TOKENS, modules=modules)
        if result:
            return result
        print("   [TECH-FINAL-GATE] final integration failed; retrying with smaller bounded prompt.", flush=True)
    for char_budget in (20000, 15000, 10000, 8000, 6000, 4000, 3000):
        bounded = reduced_summary[:char_budget]
        final_prompt = _build_final_prompt(date_str, task_graph_context, environment_graph_context, bounded, modules)
        if get_token_count(final_prompt) <= TECHNICAL_FINAL_GATE_TOKENS:
            final_max_tokens = TECHNICAL_FINAL_MAX_TOKENS if char_budget >= 6000 else 4096
            print(
                f"   [TECH-FINAL-GATE] final integration bounded to {get_token_count(final_prompt):,} tokens.",
                flush=True,
            )
            result = call_llm(final_prompt, label="technical final integration", max_tokens=final_max_tokens, modules=modules)
            if result:
                return result
            print(
                f"   [TECH-FINAL-GATE] bounded final integration failed at char_budget={char_budget}; trying smaller.",
                flush=True,
            )
    print(
        f"   [TECH-FINAL-GATE] unable to fit final integration under {TECHNICAL_FINAL_GATE_TOKENS:,} tokens.",
        flush=True,
    )
    return None


def _build_unified_final_prompt(date_str, task_graph_context, environment_graph_context, entries, truncation_by_source, modules=None):
    evidence = build_unified_evidence_text(entries, truncation_by_source)
    combined = "\n\n".join(
        [
            "=== Unified Evidence Stream ===",
            evidence,
        ]
    )
    return _build_final_prompt(date_str, task_graph_context, environment_graph_context, combined, modules)


def _largest_unified_gate_fitting_prefix(entries, truncation_by_source, modules=None):
    best = 0
    low, high = 1, len(entries)
    while low <= high:
        mid = (low + high) // 2
        prompt = _unified_partial_prompt(
            "unified evidence chunk gate probe",
            entries[:mid],
            truncation_by_source,
            modules,
        )
        if get_token_count(prompt) <= PIPELINE_GATE_TOKENS:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def _split_unified_entries_by_gate(entries, truncation_by_source, modules=None):
    chunks = []
    index = 0
    while index < len(entries):
        if len(chunks) + 1 >= MAX_GATE_SPLIT_CHUNKS:
            chunks.append(entries[index:])
            break
        size = _largest_unified_gate_fitting_prefix(entries[index:], truncation_by_source, modules)
        if size <= 0:
            size = 1
        chunks.append(entries[index:index + size])
        index += size
    return chunks


def _audit_unified_chunk(label, entries, truncation_by_source, modules=None, depth=0):
    prompt = _unified_partial_prompt(f"unified evidence chunk {label}", entries, truncation_by_source, modules)
    result = call_llm(
        prompt,
        label=f"technical unified evidence chunk {label}",
        max_tokens=6144,
        modules=modules,
    )
    if result:
        return result
    if depth >= 2 or len(entries) <= 1:
        print(f"   [TECH-UNIFIED-GATE] chunk {label} failed after retry split.", flush=True)
        return None
    midpoint = max(1, len(entries) // 2)
    print(
        f"   [TECH-UNIFIED-GATE] chunk {label} returned empty/timeout; "
        f"retrying as {label}a/{label}b with {midpoint}/{len(entries) - midpoint} entries.",
        flush=True,
    )
    first = _audit_unified_chunk(f"{label}a", entries[:midpoint], truncation_by_source, modules, depth + 1)
    second = _audit_unified_chunk(f"{label}b", entries[midpoint:], truncation_by_source, modules, depth + 1)
    if not first or not second:
        return None
    return f"=== Retry Packet {label}a ===\n{first}\n\n=== Retry Packet {label}b ===\n{second}"


def _call_unified_technical_pass(date_str, task_graph_context, environment_graph_context, entries, truncation_by_source, modules=None):
    final_prompt = _build_unified_final_prompt(
        date_str,
        task_graph_context,
        environment_graph_context,
        entries,
        truncation_by_source,
        modules,
    )
    tokens = get_token_count(final_prompt)
    if tokens <= PIPELINE_GATE_TOKENS:
        print(f">>> Technical Unified Gate: single-call prompt estimated {tokens:,} tokens", flush=True)
        return call_llm(final_prompt, label="technical unified single-call", modules=modules)

    chunks = _split_unified_entries_by_gate(entries, truncation_by_source, modules)
    print(
        f"   [TECH-UNIFIED-GATE] unified prompt estimated {tokens:,} tokens > "
        f"{PIPELINE_GATE_TOKENS:,}; split into {len(chunks)} time-ordered chunks.",
        flush=True,
    )
    packets_by_index = {}
    max_workers = min(PIPELINE_CONCURRENCY, len(chunks)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _audit_unified_chunk,
                str(index),
                chunk,
                truncation_by_source,
                modules,
            ): index
            for index, chunk in enumerate(chunks, start=1)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                packets_by_index[index] = future.result()
            except Exception as exc:
                print(f"   [TECH-UNIFIED-GATE] chunk {index} failed during audit: {exc}", flush=True)
                packets_by_index[index] = None
    missing = [index for index in range(1, len(chunks) + 1) if not packets_by_index.get(index)]
    if missing:
        print(f"   [TECH-UNIFIED-GATE] missing packets after retry: {missing}", flush=True)
        return None
    packets = "\n\n".join(
        f"=== Unified Evidence Packet {index} ===\n{packets_by_index[index]}"
        for index in sorted(packets_by_index)
        if packets_by_index.get(index)
    )
    return _call_final_integration(date_str, task_graph_context, environment_graph_context, packets, modules)


def load_agent_entries(agent_dir: Path) -> list[dict]:
    entries = load_filtered_source_entries(agent_dir)
    entries.sort(key=lambda x: x.get("time", ""))
    return entries


def load_unified_source_entries(base_filtered: Path):
    source_entries = {}
    skipped_sources = []
    for directory in base_filtered.iterdir():
        if not directory.is_dir():
            continue
        entries = load_agent_entries(directory)
        if entries:
            normalized = []
            for entry in entries:
                copied = dict(entry)
                copied["source"] = directory.name
                normalized.append(copied)
            source_entries[directory.name] = normalized
        else:
            skipped_sources.append(directory.name)
    return source_entries, sorted(skipped_sources)


def load_task_graph_context():
    try:
        paths = load_paths()
        if not is_nova_task_enabled(paths):
            return "Nova-Task v2 active graph disabled by settings."
        return render_task_graph_context(paths)
    except Exception:
        return "Nova-Task v2 active graph unavailable."


def _assign_technical_evidence_ids(entries):
    result = []
    for index, entry in enumerate(entries, start=1):
        copied = dict(entry)
        copied["_technicalEvidenceId"] = f"E{index:06d}"
        result.append(copied)
    return result


def _technical_evidence_authority(entries, truncation_by_source):
    authority = {}
    for entry in entries:
        source = str(entry.get("source") or "unknown")
        limit = int(truncation_by_source.get(source, 400) or 400)
        content = str(entry.get("content") or "")
        if len(content) > limit:
            content = content[:limit] + "..."
        evidence_id = str(entry.get("_technicalEvidenceId") or "")
        if evidence_id:
            authority[evidence_id] = content
    return authority


def _write_report_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def generate_report(date_str, manual_rules=None, *, return_evidence_ids=False):
    # Resolve once so every LLM call in this run sees the same prompt modules.
    modules = resolve_technical_prompt_modules(load_paths())
    task_graph_context = load_task_graph_context() if modules["task"] else ""

    base_filtered = _runtime_diary_root() / "__diary_daily" / date_str / "_filtered"

    source_entries, skipped_sources = load_unified_source_entries(base_filtered)
    for source in skipped_sources:
        print(f"Skipping Agent: {source} (no filtered entries)")
    active_sources = sorted(source_entries)
    print(f">>> Technical Audit starting for: {active_sources}")
    print(f">>> Technical Gate: max {PIPELINE_GATE_TOKENS:,} tokens/call, concurrency={PIPELINE_CONCURRENCY}")

    def rule_for(agent_name):
        return _technical_gate_rule(manual_rules, agent_name)

    truncation_by_source = {source: int(rule_for(source).get("t", 400) or 400) for source in active_sources}
    all_entries = []
    for source in active_sources:
        all_entries.extend(source_entries[source])
    all_entries.sort(key=lambda item: (str(item.get("time", "")), str(item.get("source", ""))))
    all_entries = _assign_technical_evidence_ids(all_entries)
    print(f">>> Technical Pass unified evidence stream: sources={len(active_sources)}, entries={len(all_entries)}")
    result = _call_unified_technical_pass(
        date_str,
        task_graph_context,
        "",
        all_entries,
        truncation_by_source,
        modules,
    )
    if return_evidence_ids:
        return result, _technical_evidence_authority(all_entries, truncation_by_source)
    return result

if __name__ == "__main__":
    target_date = sys.argv[1] if len(sys.argv) > 1 else business_today().isoformat()

    raw_output, evidence_by_ref = generate_report(target_date, return_evidence_ids=True)
    if raw_output is None:
        print("❌ ERROR: technical report_content is None. LLM integration failed or timed out.")
        sys.exit(1)

    paths = load_paths()
    if is_environment_reconciliation_enabled(paths):
        try:
            environment_result = parse_technical_environment_output(
                raw_output,
                business_date=target_date,
                evidence_by_ref=evidence_by_ref,
            )
            observation_ledger = write_environment_observation_ledger(
                paths,
                business_date=target_date,
                observations=environment_result.assets,
                evidence_by_ref=evidence_by_ref,
            )
            report_content = environment_result.report_markdown
            print(
                ">>> Environment observations: "
                f"private={observation_ledger}, candidates={len(environment_result.assets)}, "
                f"rejected={environment_result.rejected_asset_count}",
                flush=True,
            )
        except Exception as exc:
            print(f"❌ ERROR: environment observation contract failed: {exc}")
            sys.exit(1)
    else:
        report_content = raw_output.strip() + "\n"

    # 🚀 路径对齐
    out_file = diary_technical_report_path(_runtime_diary_root(), target_date)
    _write_report_atomic(out_file, report_content)

    print(f"\n✅ Technical Pass Complete: {out_file}")

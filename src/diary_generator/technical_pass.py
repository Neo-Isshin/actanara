#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import urllib.request
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
import config
from data_foundation.diary_paths import diary_technical_report_path
from data_foundation.settings import is_nova_task_enabled, resolve_llm_provider
from data_foundation.llm_transport import send_anthropic_message, send_openai_compatible_message
from data_foundation.nova_task import render_task_graph_context
from data_foundation.paths import load_paths
from data_foundation.time import business_today

_LLM_PROVIDER = resolve_llm_provider(redact_secrets=False)
API_KEY = _LLM_PROVIDER["apiKey"]
API_HOST = _LLM_PROVIDER["endpoint"]
MODEL = _LLM_PROVIDER["model"]
API_TYPE = _LLM_PROVIDER.get("api") or "anthropic-messages"
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
MAX_GATE_SPLIT_CHUNKS = max(1, _positive_int(os.getenv("NOVA_TECHNICAL_MAX_GATE_SPLIT_CHUNKS"), 256))
MAX_FINAL_PRECOMPRESS_CHUNKS = max(1, _positive_int(os.getenv("NOVA_TECHNICAL_MAX_FINAL_PRECOMPRESS_CHUNKS"), 256))

try:
    import tiktoken
except ImportError:
    tiktoken = None

# ===================== TASK RULES =====================

TASK_RULES = """【Technical Chronicle Contract】
1. 本 pass 的权威职责是生成高价值工程技术报告，不直接维护 Nova-Task active graph。
2. 报告必须围绕工程因果链：目标、阻碍、弯路、实现路径、验证证据、残余风险、可复用经验。
3. 不要把报告写成任务队列表、候选队列或 active graph 审计表。
4. 可以输出轻量 Task Hooks，但它们只是给 Nova-Task reconciliation pass 的候选 marker，不是权威写入。
5. 不得输出 `nova_task:` YAML、JSON、第二套机器可执行 payload，或任何暗示已写入 task graph 的内容。
6. task hook 只能描述事实和建议：project/workspace hint、task candidate、parent-child hint、suggested level、evidence。不要发明权威 NT-* ID。
7. Level 1=项目/产品根节点，必须用户批准；Level 2=长期子系统/数据库/连接件/产品面/运维流；Level 3=可交付任务；Level 4=子任务；Level 5=单点 action/check。
8. 单文件修改、一次命令、一个 bug、一次视觉微调或一次失败运行通常是 Level 4/5 或 evidence only，不得提升为 Level 2。
9. 无实质工程进展时，明确说明 no_material_technical_progress，不要为了填充报告编造 RCA 或任务。
"""

SYSTEM_PROMPT = f"""你是一个高级系统架构师。
你的任务是从多源工程日志中提纯高价值技术报告，为后续 learning pass 和 Nova-Task reconciliation pass 提供干净证据。
{TASK_RULES}
""" + _thinking_instruction()

PROMPT_TECHNICAL_PARTIAL = """【技术编年史证据包提炼】
请从以下 {agent_info} 的日志片段中提炼技术进展。
要求：
1. 提炼目标、阻碍、弯路、实现路径、验证证据、残余风险。
2. 保留具体文件、路径、命令、错误、commit/report 名称等可验证 evidence。
3. 区分高价值工程事实和低价值噪音；tiny/临时问询/一次性探索只作为背景或略过。
4. 保留明确的基础设施事实：设备、主机、VPS、远程/局域网实例、服务、容器、监听端口、endpoint、路径、上下线、部署、修复、配置变更。
5. 可以标出可能的 project/workspace/task hook，但不得输出 YAML/JSON 或权威任务 ID。
6. 若日志没有实质工程进展，明确输出 no_material_technical_progress，并说明原因。

【输入数据】
- 原始日志：{raw_text}
"""

PROMPT_TECHNICAL_INTEGRATION = """【高级架构师技术报告 - Engineering Chronicle 模式】
请根据统一技术证据流，整合为一份高价值技术进展报告。
这份报告的主要消费者是 learning pass 与 Nova-Task reconciliation pass：
- learning pass 需要干净的工程因果链；
- Nova-Task reconciliation pass 只需要轻量 hooks，不需要你直接输出 task graph 写入 payload。

【输入数据】
- 参考 active graph context（只用于理解已有项目/子系统名称，不用于写入）：{{task_graph_context}}
- 技术证据流或超闸证据包：{{raw_text}}

【输出格式：严禁偏离】

# {{date}} 技术进展报告

如果当天没有实质工程进展，请在“一、工程目标与完成结果”中写 `no_material_technical_progress` 并说明证据不足或只有低价值噪音的原因。

## 一、工程目标与完成结果
按项目/工作线列出当天真正发生的工程目标、完成结果和当前状态。每项必须说明为什么它有工程价值。

## 二、阻碍、根因与弯路
记录达到目标前遇到的关键困难、错误假设、失败路径、工具/环境/数据问题。必须写清：
- 现象；
- 根因；
- 为什么当时会走弯路；
- 后续如何避免。

## 三、实现路径与关键决策
记录最终采用的实现方式、重要文件/模块/接口/数据契约变化，以及放弃其他方案的理由。

## 四、验证证据
列出可复核证据：
- 命令、测试、health check、编译检查；
- 关键文件路径；
- commit/report/artifact 名称；
- 若未验证，明确写“未验证”与原因。

## 五、残余风险与后续观察
列出仍可能失败、需要回归、需要用户确认、需要跨日观察的事项。不要把低价值噪音写成风险。

## 六、可沉淀经验
提炼适合 learning pass 消费的经验：可复用模式、反模式、架构边界、流程教训、验证策略。

## 七、基础设施叙事证据
只记录与基础设施直接相关的工程事实，供 learning pass 归纳为设备/服务变更。
- 硬件/设备范围：实体设备、路由器、服务器、PC、主机、云服务器、VPS、远程实例、局域网实例。
- 服务范围：Docker 容器、二进制服务、launchd/systemd 服务、API 服务、数据库服务、embedding server、dashboard server、占用端口的监听服务。
- 每条必须说明对象、类型、宿主/位置、端口或 endpoint/path（如有）、变更、证据来源。
- 不要记录 password、token、API key、cookie、私钥等凭证值；只写 credential rotated、secretRef changed 或已脱敏。

如果没有基础设施事实，写“无”。

## 八、Nova-Task Reconciliation Hooks
只输出 Markdown 列表，不要 YAML/JSON。每条 hook 应尽量包含：
- hook_type: task_candidate | parent_child_hint | project_workspace_hint | status_hint | evidence_only
- title:
- suggested_level: 1 | 2 | 3 | 4 | 5 | unknown
- project_or_workspace:
- parent_hint:
- evidence:
- confidence: high | medium | low

如果没有值得进入 Nova-Task 的 hook，写“无”。
"""

# ===================== CORE LOGIC =====================

def call_llm(prompt, label=None, max_tokens=16384):
    call_label = label or "technical llm"
    token_estimate = get_token_count(prompt)
    started = time.time()
    print(
        f"   [TECH-LLM-START] {call_label}: prompt≈{token_estimate:,} tokens, max_tokens={max_tokens}",
        flush=True,
    )
    try:
        sender = send_anthropic_message if API_TYPE == "anthropic-messages" else send_openai_compatible_message
        content = sender(
            endpoint=API_HOST,
            api_key=API_KEY,
            model=MODEL,
            system=SYSTEM_PROMPT,
            prompt=prompt,
            temperature=0.05,
            max_tokens=max_tokens,
            timeout=180,
            thinking_mode=THINKING_MODE,
        )
        cleaned = re.sub(r'<(think|思考)>[\s\S]*?</\1>', '', content).strip()
        print(
            f"   [TECH-LLM-END] {call_label}: {time.time() - started:.1f}s, chars={len(cleaned):,}",
            flush=True,
        )
        return cleaned
    except Exception as exc:
        print(f"   [TECH-LLM-ERROR] {call_label}: {time.time() - started:.1f}s, {exc}", flush=True)
        return None

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
        lines.append(f"[{t_str}][{source}][{role}] {content}")
    return "\n".join(lines)


def get_token_count(text):
    if tiktoken:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    return len(str(text)) // 2


def _partial_prompt(agent_info, entries, max_chars):
    raw_text = build_raw_text(entries, max_chars)
    return PROMPT_TECHNICAL_PARTIAL.format(agent_info=agent_info, raw_text=raw_text)


def _unified_partial_prompt(chunk_label, entries, truncation_by_source):
    raw_text = build_unified_evidence_text(entries, truncation_by_source)
    return PROMPT_TECHNICAL_PARTIAL.format(agent_info=chunk_label, raw_text=raw_text)


def _largest_gate_fitting_prefix(entries, agent_info, max_chars):
    best = 0
    low, high = 1, len(entries)
    while low <= high:
        mid = (low + high) // 2
        prompt = _partial_prompt(agent_info, entries[:mid], max_chars)
        if get_token_count(prompt) <= PIPELINE_GATE_TOKENS:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def _split_entries_by_gate(entries, agent_info, max_chars):
    chunks = []
    index = 0
    while index < len(entries):
        if len(chunks) + 1 >= MAX_GATE_SPLIT_CHUNKS:
            chunks.append(entries[index:])
            break
        size = _largest_gate_fitting_prefix(entries[index:], agent_info, max_chars)
        if size <= 0:
            size = 1
        chunks.append(entries[index:index + size])
        index += size
    return chunks


def _summarize_entries_with_gate(agent_info, entries, max_chars):
    prompt = _partial_prompt(agent_info, entries, max_chars)
    tokens = get_token_count(prompt)
    if tokens <= PIPELINE_GATE_TOKENS:
        return call_llm(prompt, label=agent_info, max_tokens=6144)
    chunks = _split_entries_by_gate(entries, agent_info, max_chars)
    print(
        f"   [TECH-GATE] {agent_info} estimated {tokens:,} tokens > {PIPELINE_GATE_TOKENS:,}; "
        f"split into {len(chunks)} chunks.",
        flush=True,
    )
    summaries = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_prompt = _partial_prompt(f"{agent_info} Chunk {index}", chunk, max_chars)
        result = call_llm(chunk_prompt, label=f"{agent_info} Chunk {index}", max_tokens=6144)
        if result:
            summaries.append(result)
    return "\n\n".join(summaries)


def _summarize_agent(agent, entries, rule):
    if rule['step'] == 2:
        print(f"Auditing Agent: {agent} (Step 2, t={rule['t']})")
        return _summarize_entries_with_gate(agent, entries, rule['t'])
    if rule['step'] == 3:
        print(f"Auditing Agent: {agent} (Step 3, Time Split)")
        summaries = []
        for i in range(4):
            chunk = entries[len(entries)//4 * i : len(entries)//4 * (i+1)]
            if not chunk:
                continue
            result = _summarize_entries_with_gate(f"{agent} Block {i}", chunk, rule['t'])
            if result:
                summaries.append(result)
        return "\n\n".join(summaries)
    return _summarize_entries_with_gate(agent, entries, rule.get("t", 400))


def _split_text_for_final_gate(text):
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
            prompt = PROMPT_TECHNICAL_PARTIAL.format(
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


def _build_final_prompt(date_str, task_graph_context, combined):
    return (
        PROMPT_TECHNICAL_INTEGRATION.replace("{{date}}", date_str)
        .replace("{date}", date_str)
        .replace("{{task_graph_context}}", task_graph_context[:2000])
        .replace("{{raw_text}}", combined)
    )


def _call_final_integration(date_str, task_graph_context, combined):
    final_prompt = _build_final_prompt(date_str, task_graph_context, combined)
    tokens = get_token_count(final_prompt)
    if tokens <= TECHNICAL_FINAL_GATE_TOKENS:
        result = call_llm(final_prompt, label="technical final integration", max_tokens=TECHNICAL_FINAL_MAX_TOKENS)
        if result:
            return result
        print("   [TECH-FINAL-GATE] final integration failed; retrying with bounded prompt.", flush=True)
    print(
        f"   [TECH-FINAL-GATE] final integration estimated {tokens:,} tokens > final target "
        f"{TECHNICAL_FINAL_GATE_TOKENS:,}; pre-compressing summaries.",
        flush=True,
    )
    reduced = []
    for index, chunk in enumerate(_split_text_for_final_gate(combined), start=1):
        prompt = PROMPT_TECHNICAL_PARTIAL.format(
            agent_info=f"technical final pre-compression #{index}",
            raw_text=chunk,
        )
        result = call_llm(
            prompt,
            label=f"technical final pre-compression #{index}",
            max_tokens=TECHNICAL_PRECOMPRESS_MAX_TOKENS,
        )
        reduced.append(result if result else chunk)
    reduced_summary = "\n\n".join(reduced)
    final_prompt = _build_final_prompt(date_str, task_graph_context, reduced_summary)
    if get_token_count(final_prompt) <= TECHNICAL_FINAL_GATE_TOKENS:
        result = call_llm(final_prompt, label="technical final integration", max_tokens=TECHNICAL_FINAL_MAX_TOKENS)
        if result:
            return result
        print("   [TECH-FINAL-GATE] final integration failed; retrying with smaller bounded prompt.", flush=True)
    for char_budget in (20000, 15000, 10000, 8000, 6000, 4000, 3000):
        bounded = reduced_summary[:char_budget]
        final_prompt = _build_final_prompt(date_str, task_graph_context, bounded)
        if get_token_count(final_prompt) <= TECHNICAL_FINAL_GATE_TOKENS:
            final_max_tokens = TECHNICAL_FINAL_MAX_TOKENS if char_budget >= 6000 else 4096
            print(
                f"   [TECH-FINAL-GATE] final integration bounded to {get_token_count(final_prompt):,} tokens.",
                flush=True,
            )
            result = call_llm(final_prompt, label="technical final integration", max_tokens=final_max_tokens)
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


def _build_unified_final_prompt(date_str, task_graph_context, entries, truncation_by_source):
    evidence = build_unified_evidence_text(entries, truncation_by_source)
    combined = "\n\n".join(
        [
            "=== Unified Evidence Stream ===",
            evidence,
        ]
    )
    return _build_final_prompt(date_str, task_graph_context, combined)


def _largest_unified_gate_fitting_prefix(entries, truncation_by_source):
    best = 0
    low, high = 1, len(entries)
    while low <= high:
        mid = (low + high) // 2
        prompt = _unified_partial_prompt(
            "unified evidence chunk gate probe",
            entries[:mid],
            truncation_by_source,
        )
        if get_token_count(prompt) <= PIPELINE_GATE_TOKENS:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def _split_unified_entries_by_gate(entries, truncation_by_source):
    chunks = []
    index = 0
    while index < len(entries):
        if len(chunks) + 1 >= MAX_GATE_SPLIT_CHUNKS:
            chunks.append(entries[index:])
            break
        size = _largest_unified_gate_fitting_prefix(entries[index:], truncation_by_source)
        if size <= 0:
            size = 1
        chunks.append(entries[index:index + size])
        index += size
    return chunks


def _audit_unified_chunk(label, entries, truncation_by_source, depth=0):
    prompt = _unified_partial_prompt(f"unified evidence chunk {label}", entries, truncation_by_source)
    result = call_llm(
        prompt,
        label=f"technical unified evidence chunk {label}",
        max_tokens=6144,
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
    first = _audit_unified_chunk(f"{label}a", entries[:midpoint], truncation_by_source, depth + 1)
    second = _audit_unified_chunk(f"{label}b", entries[midpoint:], truncation_by_source, depth + 1)
    if not first or not second:
        return None
    return f"=== Retry Packet {label}a ===\n{first}\n\n=== Retry Packet {label}b ===\n{second}"


def _call_unified_technical_pass(date_str, task_graph_context, entries, truncation_by_source):
    final_prompt = _build_unified_final_prompt(date_str, task_graph_context, entries, truncation_by_source)
    tokens = get_token_count(final_prompt)
    if tokens <= PIPELINE_GATE_TOKENS:
        print(f">>> Technical Unified Gate: single-call prompt estimated {tokens:,} tokens", flush=True)
        return call_llm(final_prompt, label="technical unified single-call")

    chunks = _split_unified_entries_by_gate(entries, truncation_by_source)
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
    return _call_final_integration(date_str, task_graph_context, packets)


def load_agent_entries(agent_dir: Path) -> list[dict]:
    entries = []
    for f in agent_dir.glob("*.jsonl"):
        with open(f) as fin:
            for line in fin:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
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


def generate_report(date_str, manual_rules=None):
    # 1. 加载 active graph 和策略。Task intelligence hints 暂停注入，避免与 active graph 任务匹配权威冲突。
    task_graph_context = load_task_graph_context()

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
    print(f">>> Technical Pass unified evidence stream: sources={len(active_sources)}, entries={len(all_entries)}")
    return _call_unified_technical_pass(date_str, task_graph_context, all_entries, truncation_by_source)

if __name__ == "__main__":
    target_date = sys.argv[1] if len(sys.argv) > 1 else business_today().isoformat()

    report_content = generate_report(target_date)
    if report_content is None:
        print("❌ ERROR: technical report_content is None. LLM integration failed or timed out.")
        sys.exit(1)

    # 🚀 路径对齐
    out_file = diary_technical_report_path(_runtime_diary_root(), target_date)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"\n✅ Technical Pass Complete: {out_file}")

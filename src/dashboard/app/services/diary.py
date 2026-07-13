#!/usr/bin/env python3
"""解析日记 markdown，提取结构化数据"""
import re
import json
from pathlib import Path
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
import config
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from collections import Counter, defaultdict
from contextlib import closing
from data_foundation.diary_markdown import (
    DIARY_MARKDOWN_PROJECTION,
    _period_lessons,
    _period_summary_topics,
    read_diary_markdown_documents,
)
from data_foundation.diary_paths import (
    diary_learning_report_path,
    diary_markdown_paths,
    diary_report_paths,
    iter_diary_markdown_files,
)
from data_foundation.db import connect
from data_foundation.paths import load_paths
from data_foundation.settings import default_external_tool_path, ensure_settings, external_tool_path, resolve_llm_provider, resolve_runtime_source
from data_foundation.time import business_date_for, business_window, parse_timestamp, resolve_timezone
from data_foundation.workspace_attribution import canonical_workspace_name
from data_foundation.llm_transport import (
    ANTHROPIC_VERSION,
    anthropic_messages_payload,
    anthropic_messages_url,
    parse_anthropic_text,
)
from .dashboard_state import attach_dashboard_state, dashboard_failure, source_error

SESSION_DIR = default_external_tool_path("openclaw", "agentsRoot")
_DEFAULT_SESSION_DIR = SESSION_DIR

# LLM 精简结果缓存（key=date, value=summarized agent_work）
_SUMMARIZE_CACHE: Dict[str, Dict[str, list]] = {}
_JSONL_CACHE: Dict[str, Dict[str, Any]] = {}
DAY_NAME = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
_SUMMARY_HEADING_ALIASES = ("今日概要", "Daily Overview")
_WEATHER_HEADING_ALIASES = ("天气", "Weather")
_DAILY_STATS_HEADING_ALIASES = ("本日统计", "Daily Stats", "Daily Statistics")
_AGENT_WORK_HEADING_ALIASES = ("Agent工作", "Agent Work")
_SCHEDULED_JOBS_HEADING_ALIASES = ("定时任务", "Scheduled Jobs")
_IMPORTANT_NOTICES_HEADING_ALIASES = ("重要提醒", "Important Notices")
_NOTES_HEADING_ALIASES = ("备注", "Notes")
_LESSONS_HEADING_ALIASES = ("黄金教训", "Lessons")
_INFRA_HEADING_ALIASES = ("基建变动", "Infrastructure Updates")
_LESSON_FIELD_ALIASES = {
    "问题": "problem",
    "Problem": "problem",
    "根因": "rootCause",
    "Root Cause": "rootCause",
    "建议": "suggestion",
    "Recommendation": "suggestion",
}


import os as _os

# ── 文件级 LLM 结果缓存 ──────────────────────────────────
_LLM_CACHE_DIR = config.WORKSPACE_DIR / "src" / "dashboard" / "app" / "diary-data" / ".cache"


def _report_read_source() -> str:
    return resolve_runtime_source("REPORT_READ_SOURCE", load_paths())

def _diary_root() -> Path:
    paths = load_paths()
    return paths.diary_dir


def _pipeline_language_profile() -> str:
    paths = load_paths()
    settings = ensure_settings(paths)
    pipeline = settings.get("pipeline") if isinstance(settings.get("pipeline"), dict) else {}
    return str(pipeline.get("languageProfile") or "zh")


def _heading_text(line: str) -> str:
    stripped = str(line or "").strip()
    stripped = re.sub(r"^#{1,6}\s+", "", stripped)
    stripped = re.sub(r"^[^\w\u4e00-\u9fff\[]+\s*", "", stripped)
    return stripped.strip()


def _heading_matches(line: str, aliases: tuple[str, ...]) -> bool:
    heading = _heading_text(line)
    return any(alias in heading for alias in aliases)


def _clean_markdown_summary_item(value: str) -> str:
    item = str(value or "").strip()
    item = re.sub(r"^\*\*(.+?)\*\*[：:]\s*", r"\1: ", item)
    item = item.replace("**", "").strip()
    return re.sub(r"^\[([^\]]+)\]([：:])", r"\1\2", item)


def _is_table_separator(value: str) -> bool:
    return bool(re.match(r"^:?-{3,}:?$", str(value or "").strip()))

def _external_tool_path(tool: str, key: str) -> Path:
    try:
        return external_tool_path(tool, key)
    except Exception:
        if tool == "openclaw" and key == "agentsRoot":
            return _DEFAULT_SESSION_DIR
        raise

def _session_dir() -> Path:
    if SESSION_DIR != _DEFAULT_SESSION_DIR:
        return SESSION_DIR
    return _external_tool_path("openclaw", "agentsRoot")

def _llm_cache_load(key: str) -> Optional[Any]:
    """从文件加载 LLM 缓存"""
    try:
        fpath = _LLM_CACHE_DIR / f"{key}.json"
        if fpath.exists():
            return json.loads(fpath.read_text())
    except Exception:
        pass
    return None

def _llm_cache_save(key: str, value: Any) -> None:
    """保存 LLM 结果到文件"""
    try:
        _LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fpath = _LLM_CACHE_DIR / f"{key}.json"
        fpath.write_text(json.dumps(value, ensure_ascii=False))
    except Exception:
        pass


def _summarize_agent_work(agent_work: Dict[str, List[str]], date: str = "") -> Dict[str, List[str]]:
    """如果 entry 总数超过阈值，用 LLM 精简每个 agent 的工作内容（按日期缓存）"""
    import urllib.request, urllib.error, re as re_module

    # 有日期时先查缓存
    # 文件级缓存
    if date:
        cached = _llm_cache_load(f"summarize-{date}")
        if cached is not None:
            _SUMMARIZE_CACHE[date] = cached
            return cached
    if date and date in _SUMMARIZE_CACHE:
        return _SUMMARIZE_CACHE[date]

    total_entries = sum(len(v) for v in agent_work.values())
    if total_entries <= 15:
        return agent_work

    # 构建 prompt（不用 f-string，避免大括号转义混乱）
    parts = ["你是一个助手，负责精简 Agent 工作日志。", "", "每个 Agent 有多条工作记录，请将每条记录压缩为一句话（不超过 50 字），保留核心动作和结果。", "", "原文："]
    for agent, entries in agent_work.items():
        parts.append("## " + agent)
        for e in entries:
            parts.append("- " + e)
        parts.append("")
    parts.append("")
    parts.append("精简版（只输出 JSON，格式：{" + '"agent_name": ["精简描述1", "精简描述2", ...]}' + "，不要有其他内容）：")
    prompt = "\n".join(parts)

    llm_provider = _dashboard_llm_provider()
    if not llm_provider.get("apiKey"):
        return agent_work
    use_anthropic = _dashboard_uses_anthropic(llm_provider)
    if use_anthropic:
        req_body = anthropic_messages_payload(
            llm_provider["model"],
            "你是一个助手，负责精简 Agent 工作日志。只输出 JSON。",
            prompt,
            0.3,
            1024,
        )
        headers = {
            "X-Api-Key": llm_provider["apiKey"],
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
    else:
        req_body = {
            "model": llm_provider["model"],
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
            "temperature": 0.3,
        }
        headers = {
            "Authorization": "Bearer " + llm_provider["apiKey"],
            "Content-Type": "application/json",
        }

    try:
        req = urllib.request.Request(
            _dashboard_llm_url(llm_provider),
            data=json.dumps(req_body).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            text = parse_anthropic_text(result) if use_anthropic else result["choices"][0]["message"]["content"]
            m = re_module.search(r'\{.*\}', text, re_module.DOTALL)
            if m:
                summarized = json.loads(m.group())
                for agent in agent_work:
                    if agent not in summarized:
                        summarized[agent] = agent_work[agent]
                if date:
                    _SUMMARIZE_CACHE[date] = summarized
                    _llm_cache_save(f"summarize-{date}", summarized)
                return summarized
    except Exception:
        pass

    return agent_work


def _dashboard_llm_provider() -> dict:
    provider = resolve_llm_provider(redact_secrets=False)
    return {
        "provider": provider.get("provider") or "",
        "endpoint": provider.get("endpoint") or "",
        "model": provider.get("model") or "",
        "api": provider.get("api") or "",
        "contextWindow": provider.get("contextWindow"),
        "maxTokens": provider.get("maxTokens"),
        "apiKey": provider.get("apiKey") or "",
    }


def _dashboard_llm_url(provider: dict) -> str:
    endpoint = str(provider.get("endpoint") or "").rstrip("/")
    if not endpoint:
        raise ValueError("Dashboard LLM endpoint is required")
    if _dashboard_uses_anthropic(provider):
        return anthropic_messages_url(endpoint)
    if endpoint.endswith(("/v1", "/v2", "/v3", "/v4")):
        return endpoint + "/chat/completions"
    return endpoint + "/v1/chat/completions"


def _dashboard_uses_anthropic(provider: dict) -> bool:
    api = str(provider.get("api") or "").lower()
    endpoint = str(provider.get("endpoint") or "").lower()
    provider_name = str(provider.get("provider") or "").lower()
    return (
        api == "anthropic-messages"
        or "anthropic" in provider_name
        or "anthropic" in endpoint
        or "minimax" in provider_name
        or "minimax" in endpoint
        or "minimaxi" in endpoint
    )


# 认可的 Agent 名称白名单

def _utc_ms_to_hkt_approx(ts_ms: int) -> datetime:
    """Convert UTC millisecond timestamp to configured local timezone. Name kept for compatibility."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(resolve_timezone())


def detect_cron_tasks(full_date: str) -> List[Dict[str, str]]:
    """
    Detect cron tasks for a given HKT date from real OpenClaw cron data.
    Returns list of {task, time, status, note}.
    """
    CRON_JOBS_FILE = _first_existing_path(
        _external_tool_path("openclaw", "cronJobsPath"),
        _external_tool_path("openclaw", "cronJobsMigratedPath"),
    )
    CRON_RUNS_DIR = _external_tool_path("openclaw", "cronRunsRoot")

    if not CRON_JOBS_FILE:
        return []

    try:
        with open(CRON_JOBS_FILE) as f:
            jobs_data = json.load(f)
    except Exception:
        return []

    jobs = {j["id"]: j for j in jobs_data.get("jobs", []) if j.get("enabled", True)}

    # Target date in configured business timezone.
    try:
        target_date = datetime.strptime(full_date, "%Y-%m-%d").date()
    except ValueError:
        return []

    local_tz = resolve_timezone()
    utc_start, utc_end = business_window(target_date, tz=local_tz)
    utc_start_ms = int(utc_start.timestamp() * 1000)
    utc_end_ms = int(utc_end.timestamp() * 1000)

    # Collect all runs in range, grouped by job_id
    run_records: Dict[str, List[dict]] = {}
    if CRON_RUNS_DIR.exists():
        for run_file in _iter_cron_run_files(CRON_RUNS_DIR):
            try:
                with open(run_file) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = rec.get("ts", 0)
                        if utc_start_ms <= ts < utc_end_ms:
                            job_id = rec.get("jobId", "")
                            if job_id not in run_records:
                                run_records[job_id] = []
                            run_records[job_id].append(rec)
            except Exception:
                continue

    # For each enabled job, check if it should run on target date. croniter is
    # optional in lightweight pipeline environments; missing support should not
    # block diary markdown materialization.
    try:
        from croniter import croniter
    except ModuleNotFoundError:
        return []

    tasks: List[Dict[str, str]] = []
    for job_id, job in jobs.items():
        schedule = job.get("schedule", {})
        expr = schedule.get("expr", "")
        if not expr:
            continue

        try:
            cron = croniter(expr, datetime.combine(target_date, datetime.min.time()))
        except Exception:
            continue

        scheduled_times = []
        for _ in range(100):  # max 100 occurrences
            try:
                next_time = cron.get_next(datetime)
                if next_time.date() != target_date:
                    break
                scheduled_times.append(next_time)
            except StopIteration:
                break

        if not scheduled_times:
            continue

        runs = sorted(run_records.get(job_id, []), key=lambda r: r.get("runAtMs") or r.get("ts") or 0)

        if not runs:
            # Job was scheduled but no run record → treat as "未执行"
            times_str = ", ".join(t.strftime("%H:%M") for t in scheduled_times)
            tasks.append({
                "task": job.get("name", job_id),
                "status": "⚠️ 未执行",
                "note": times_str,
            })
        else:
            for run in runs:
                run_dt = _utc_ms_to_hkt_approx(run.get("runAtMs") or run.get("ts") or 0)
                run_time_str = run_dt.strftime("%H:%M")
                status_str = run.get("status", "")
                error = run.get("error", "") or ""
                last_err = job.get("state", {}).get("lastError", "") if not error else error

                if status_str == "ok":
                    status = "✅ 成功"
                elif status_str == "error":
                    if "timeout" in last_err.lower():
                        status = "⚠️ 超时"
                    else:
                        status = "❌ 失败"
                elif status_str == "skipped":
                    status = "➖ 跳过"
                else:
                    status = f"{status_str}"

                note = run_time_str
                if last_err and status_str == "error":
                    short_err = last_err.split("\n")[0][:40]
                    note = f"{run_time_str} · {short_err}"

                tasks.append({
                    "task": job.get("name", job_id),
                    "status": status,
                    "note": note,
                })

    # Sort by task name for consistent display
    tasks.sort(key=lambda t: t["task"])
    return tasks


def _first_existing_path(*paths: Path) -> Optional[Path]:
    return next((path for path in paths if path.exists()), None)


def _iter_cron_run_files(root: Path):
    seen = set()
    for pattern in ("*.jsonl", "*.jsonl.migrated"):
        for path in sorted(root.glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            yield path


def _narrative_filename_date(filename: str, *, language_profile: str) -> Optional[str]:
    profile = "en" if str(language_profile or "").lower().startswith("en") else "zh"
    prefixes = ("diary",) if profile == "en" else ("日记",)
    for prefix in prefixes:
        match = re.search(rf"{re.escape(prefix)}-(\d{{6}})(?:-no-activity)?\.md$", filename)
        if match:
            return match.group(1)
    return None


def get_diary_list(*, include_state: bool = False):
    diaries_by_date: Dict[str, Dict[str, Any]] = {}
    source_errors: List[Dict[str, Any]] = []
    diary_root = _diary_root()
    if not diary_root.exists():
        if include_state:
            return attach_dashboard_state({"items": []}, empty=True)
        return []
    language_profile = _pipeline_language_profile()
    narrative_files = [
        path
        for path in iter_diary_markdown_files(diary_root)
        if _narrative_filename_date(path.name, language_profile=language_profile) is not None
    ]
    for md_file in sorted(narrative_files, reverse=True):
        stamp = _narrative_filename_date(md_file.name, language_profile=language_profile)
        if not stamp:
            continue
        try:
            dt = datetime.strptime(stamp, "%y%m%d")
            date_str = dt.strftime("%Y-%m-%d")
            short_date = dt.strftime("%m%d")
            dow = DAY_NAME[dt.weekday()]
        except:
            dow = ""
        item = {
            "date": short_date,
            "fullDate": date_str,
            "displayDate": date_str[5:].replace("-", "-"),
            "dayOfWeek": dow,
            "filename": md_file.name,
            "isBlankDay": md_file.name.endswith("-no-activity.md"),
        }
        existing = diaries_by_date.get(date_str)
        # Preserve the explicit no-activity artifact as the disk fallback.
        # Foundation truth below takes precedence whenever the date was materialized.
        if existing is None or item["isBlankDay"]:
            diaries_by_date[date_str] = item
    if diaries_by_date:
        try:
            paths = load_paths()
            with connect(paths, read_only=True) as connection:
                placeholders = ",".join("?" for _ in diaries_by_date)
                rows = connection.execute(
                    f"""
                    SELECT business_date,
                           MAX(CASE WHEN tool_key != 'cron'
                                     AND (tokens > 0 OR messages > 0 OR sessions > 0 OR api_calls > 0)
                                    THEN 1 ELSE 0 END) AS has_activity
                    FROM daily_tool_usage
                    WHERE business_date IN ({placeholders})
                    GROUP BY business_date
                    """,
                    tuple(diaries_by_date),
                ).fetchall()
            for row in rows:
                item = diaries_by_date.get(str(row["business_date"]))
                if item is not None:
                    item["isBlankDay"] = not bool(row["has_activity"])
                    item["activityStateSource"] = "foundation-daily-tool-usage"
        except Exception:
            source_errors.append(source_error("foundation-daily-tool-usage"))
    items = sorted(diaries_by_date.values(), key=lambda item: item["fullDate"], reverse=True)
    if include_state:
        return attach_dashboard_state({"items": items}, empty=not items, source_errors=source_errors)
    return items


def parse_diary(full_date: str) -> Optional[Dict[str, Any]]:
    md_files = diary_report_paths(_diary_root(), full_date, "narrative", language_profile=_pipeline_language_profile())
    if not md_files:
        return None
    raw = md_files[0].read_text(encoding="utf-8")
    json_block = _extract_json_block(raw)
    return _parse_raw(raw, full_date, json_block)


def _empty_diary_page(full_date: str, freshness: Dict[str, Any]) -> Dict[str, Any]:
    try:
        dt = datetime.strptime(full_date, "%Y-%m-%d")
        dow = DAY_NAME[dt.weekday()]
        display_date = dt.strftime("%m-%d")
    except ValueError:
        dow = ""
        display_date = full_date[5:] if len(full_date) >= 10 else full_date
    return {
        "date": full_date,
        "languageProfile": _pipeline_language_profile(),
        "displayDate": display_date,
        "dayOfWeek": dow,
        "weather": "",
        "summary": "",
        "agentWork": {},
        "agentWorkNew": {},
        "summaryTopics": [],
        "cronTasks": [],
        "todos": [],
        "dailyStats": [],
        "dailyStatsHeaders": [],
        "reminders": [],
        "notes": [],
        "hourlyTokens": {},
        "agentStats": {},
        "sessionBySource": {},
        "tokenStats": {},
        "sessionStats": {"sessions": 0, "messages": 0},
        "parsedKpi": {},
        "rawContent": "",
        "lessons": [],
        "infraChanges": [],
        "ragStatsSnapshot": {},
        "memoryStatsSnapshot": {},
        "dataFreshness": {"diaryPage": freshness},
    }


def _missing_diary_page(full_date: str) -> Dict[str, Any]:
    return attach_dashboard_state(
        _empty_diary_page(
            full_date,
            {
                "source": "snapshot-missing",
                "status": "projection_missing",
                "projectionType": DIARY_MARKDOWN_PROJECTION,
                "date": full_date,
                "refreshRequired": True,
                "refreshPolicy": "manual-foundation-refresh",
            },
        ),
        empty=True,
    )


def _document_to_markdown(document: Dict[str, Any]) -> str:
    parts: List[str] = []
    title = document.get("title")
    if title:
        parts.append(f"# {title}")
    for section in document.get("sections") or []:
        level = max(1, min(int(section.get("headingLevel") or 2), 6))
        heading = section.get("heading") or ""
        body = section.get("bodyMarkdown") or ""
        if heading:
            parts.append("#" * level + " " + heading)
        if body:
            parts.append(body)
    return "\n\n".join(parts).strip() + "\n"


def _foundation_infra_changes(document: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not document:
        return []
    changes: List[Dict[str, str]] = []
    for section in document.get("sections") or []:
        if not any(_heading_matches(heading, _INFRA_HEADING_ALIASES) for heading in section.get("headingPath") or []):
            continue
        for line in (section.get("bodyMarkdown") or "").splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            cols = [col.strip() for col in stripped.strip().strip("|").split("|")]
            if len(cols) < 3:
                continue
            if cols[0] in ("对象", "Object", "", "---", ":---") or re.match(r"^:?-{3,}$", cols[0]):
                continue
            changes.append({"target": cols[0], "change": cols[1], "current": cols[2]})
    return changes


def _foundation_graph_infra_changes(business_date: date) -> List[Dict[str, str]]:
    try:
        from data_foundation.infrastructure import infrastructure_events_for_date

        events = infrastructure_events_for_date(load_paths(), business_date)
    except Exception:
        return []
    changes: List[Dict[str, str]] = []
    for event in events:
        changes.append(
            {
                "target": event.get("name", ""),
                "entityId": event.get("entityId", ""),
                "entityType": event.get("entityType", ""),
                "eventType": event.get("eventType", ""),
                "field": event.get("field", ""),
                "change": event.get("summary", ""),
                "current": event.get("currentValue", ""),
                "confidence": event.get("confidence", ""),
            }
        )
    return changes


def get_diary_page(full_date: str) -> Dict[str, Any]:
    """Read the single-day Dashboard page from Foundation snapshots only."""
    try:
        business_date = datetime.strptime(full_date, "%Y-%m-%d").date()
    except ValueError:
        return _missing_diary_page(full_date)
    try:
        documents = read_diary_markdown_documents(load_paths(), business_date, business_date)
    except Exception:
        return dashboard_failure(
            "diary-markdown-documents",
            fallback=_empty_diary_page(
                full_date,
                {
                    "source": "foundation",
                    "status": "source_error",
                    "projectionType": DIARY_MARKDOWN_PROJECTION,
                    "date": full_date,
                    "refreshRequired": False,
                },
            ),
        )

    narrative = next((doc for doc in documents if doc.get("report_type") == "narrative"), None)
    if narrative is None:
        return _missing_diary_page(full_date)

    learning = next((doc for doc in documents if doc.get("report_type") == "learning"), None)
    embedded = narrative.get("embeddedJson") or {}
    data = _parse_raw(_document_to_markdown(narrative), full_date, embedded, use_live_sources=False)
    _apply_embedded_json_authority(data, embedded)
    _apply_foundation_usage_rollup(data, load_paths(), business_date)
    if not data.get("hourlyTokens"):
        data["hourlyTokens"] = _foundation_hourly_tokens(load_paths(), business_date)
    metrics = (narrative.get("embeddedJson") or {}).get("metrics")
    if isinstance(metrics, dict):
        parsed_kpi = data.setdefault("parsedKpi", {})
        metric_sources = [metrics]
        if isinstance(metrics.get("total"), dict):
            metric_sources.insert(0, metrics["total"])
        for source_key, target_key in (
            ("total_tokens", "total_tokens"),
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("cache_read", "cache_read"),
            ("cache_write", "cache_write"),
            ("api_calls", "api_calls"),
            ("sessions_count", "sessions_count"),
            ("sessions_total", "sessions_total"),
            ("active_sessions", "active_sessions"),
            ("messages_count", "messages_count"),
        ):
            for source in metric_sources:
                if source.get(source_key) is not None and _kpi_missing(parsed_kpi.get(target_key)):
                    parsed_kpi[target_key] = source[source_key]
                    break
    structured_topics = _period_summary_topics(narrative)
    if structured_topics:
        data["summaryTopics"] = [
            {"title": item.get("title", ""), "items": item.get("items") or []}
            for item in structured_topics
        ]
    if learning is not None:
        data["lessons"] = [
            {
                "agent": lesson.get("agent", ""),
                "problem": lesson.get("problem", ""),
                "rootCause": lesson.get("rootCause", ""),
                "suggestion": lesson.get("suggestion", ""),
            }
            for lesson in _period_lessons(learning)
        ]
        data["infraChanges"] = _foundation_graph_infra_changes(business_date) or _foundation_infra_changes(learning)
    data["dataFreshness"] = {
        **data.get("dataFreshness", {}),
        "diaryPage": {
            "source": "foundation",
            "status": narrative.get("status") or "ready",
            "projectionType": DIARY_MARKDOWN_PROJECTION,
            "date": full_date,
            "documentKey": narrative.get("document_key"),
            "generatedAt": narrative.get("parsed_at"),
            "refreshRequired": False,
        },
    }
    return attach_dashboard_state(data)


def _kpi_missing(value: Any) -> bool:
    return value is None or value == "" or value == 0


def _apply_foundation_usage_rollup(data: Dict[str, Any], paths, business_date: date) -> None:
    """Prefer materialized usage rollups over embedded legacy JSON for session details."""
    try:
        with connect(paths, read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT tool_key, messages, sessions
                FROM daily_tool_usage
                WHERE business_date = ?
                ORDER BY sessions DESC, tool_key
                """,
                (business_date.isoformat(),),
            ).fetchall()
    except Exception:
        return
    session_by_source: Dict[str, Dict[str, int]] = {}
    total_sessions = 0
    for row in rows:
        sessions = _safe_int(row["sessions"])
        messages = _safe_int(row["messages"])
        if sessions <= 0 and messages <= 0:
            continue
        session_by_source[row["tool_key"]] = {
            "active_sessions": sessions,
            "sessions_total": sessions,
            "messages_count": messages,
        }
        total_sessions += sessions
    if not session_by_source:
        return
    data["sessionBySource"] = session_by_source
    parsed_kpi = data.setdefault("parsedKpi", {})
    parsed_kpi["active_sessions"] = total_sessions
    parsed_kpi["sessions_total"] = total_sessions
    if _kpi_missing(parsed_kpi.get("sessions_count")):
        parsed_kpi["sessions_count"] = total_sessions


def _apply_embedded_json_authority(data: Dict[str, Any], embedded: Dict[str, Any]) -> None:
    """Use embedded JSON as the single-day Foundation authority for non-text KPIs."""
    if not isinstance(embedded, dict):
        return
    metrics = embedded.get("metrics")
    parsed_kpi = data.setdefault("parsedKpi", {})
    if isinstance(metrics, dict):
        sources = [metrics]
        if isinstance(metrics.get("total"), dict):
            sources.insert(0, metrics["total"])
        for source_key, target_key in (
            ("total_tokens", "total_tokens"),
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("cache_read", "cache_read"),
            ("cache_write", "cache_write"),
            ("api_calls", "api_calls"),
            ("sessions_count", "sessions_count"),
            ("sessions_total", "sessions_total"),
            ("active_sessions", "active_sessions"),
            ("messages_count", "messages_count"),
        ):
            for source in sources:
                if isinstance(source, dict) and source.get(source_key) is not None:
                    parsed_kpi[target_key] = source[source_key]
                    break
    cron_tasks = embedded.get("cronTasks")
    if isinstance(cron_tasks, list):
        data["cronTasks"] = [_normalize_cron_task(task) for task in cron_tasks if isinstance(task, dict)]
    hourly = embedded.get("hourlyTokens")
    if isinstance(hourly, dict):
        data["hourlyTokens"] = hourly


def _normalize_cron_task(task: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(task)
    normalized["task"] = task.get("task") or task.get("taskId") or task.get("name") or task.get("time") or ""
    normalized["note"] = task.get("note") or task.get("conclusion") or task.get("duration") or ""
    normalized["status"] = task.get("status") or ""
    normalized["time"] = task.get("time") or ""
    return normalized


def _foundation_hourly_tokens(paths, business_date: date) -> Dict[str, int]:
    hourly = {f"{hour:02d}": 0 for hour in range(24)}
    try:
        with connect(paths, read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT occurred_at, protocol_total_tokens
                FROM usage_events
                WHERE business_date = ?
                """,
                (business_date.isoformat(),),
            ).fetchall()
    except Exception:
        return {}
    for row in rows:
        hour = _hour_from_iso(row["occurred_at"])
        if hour is None:
            continue
        hourly[f"{hour:02d}"] += int(row["protocol_total_tokens"] or 0)
    return {key: value for key, value in hourly.items() if value}


def _normalize_hourly_total(hourly: Dict[Any, Any], authoritative_total: Any) -> Dict[str, int]:
    normalized = {f"{int(hour):02d}": _safe_int(value) for hour, value in (hourly or {}).items() if _safe_int(value) > 0}
    target_total = _safe_int(authoritative_total)
    current_total = sum(normalized.values())
    if not normalized or target_total <= 0 or current_total <= 0 or current_total == target_total:
        return normalized
    scaled: Dict[str, int] = {}
    remainder_key = max(normalized, key=lambda key: normalized[key])
    running = 0
    for hour, value in normalized.items():
        scaled_value = int(round(value * target_total / current_total))
        scaled[hour] = scaled_value
        running += scaled_value
    scaled[remainder_key] = max(0, scaled[remainder_key] + target_total - running)
    return {hour: value for hour, value in scaled.items() if value > 0}


def _hour_from_iso(value: str) -> int | None:
    try:
        dt = parse_timestamp(value)
        if dt is None:
            return None
        return int(dt.astimezone(resolve_timezone()).hour)
    except (TypeError, ValueError):
        return None


def _extract_session_by_source(json_block: Optional[Dict]) -> Dict[str, Dict]:
    """从 JSON block metrics 中提取每个源的 sessions 数据"""
    if not json_block or not isinstance(json_block.get("metrics"), dict):
        return {}
    result = {}
    for src in ("openclaw", "gemini-cli", "claude-code", "hermes", "cron"):
        m = json_block["metrics"].get(src)
        if isinstance(m, dict):
            result[src] = {
                "active_sessions": m.get("active_sessions", 0) or 0,
                "sessions_total": m.get("sessions_total", 0) or 0,
            }
    return result


def _extract_json_block(markdown: str) -> Optional[Dict[str, Any]]:
    """从日记 markdown 底部提取 ```json 块（metrics/tasks/todos）。
    新日记（04-10+）底部有 Python 自动追加的 JSON 数据块，
    直接读取可避免重复扫描 JSONL 文件。"""
    import re as _re
    # 匹配 ```json ... ``` 块，用平衡花括号确保完整 JSON
    m = _re.search(r'```json\n(\{.+?\})\n```', markdown, _re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        # Fallback: 尝试找到最后一个 ``` 之前的内容
        m2 = _re.search(r'```json\n([\s\S]+?)\n```', markdown)
        if m2:
            try:
                return json.loads(m2.group(1))
            except (json.JSONDecodeError, ValueError):
                pass
        return None


def _parse_raw(raw: str, full_date: str, json_block: Optional[Dict] = None, *, use_live_sources: bool = True) -> Dict[str, Any]:
    lines = raw.split("\n")
    weather = ""
    summary = ""
    summary_topics: List[Dict[str, Any]] = []  # [{title, items}]
    agent_work: Dict[str, List[str]] = {}       # legacy flat format
    # 新格式：agent_work_new = { agent: [{period, main_task, sub_items: []}] }
    agent_work_new: Dict[str, List[Dict[str, Any]]] = {}
    current_agent: Optional[str] = None
    cron_tasks: List[Dict[str, str]] = []
    daily_stats: List[Dict[str, str]] = []
    stats_headers: List[str] = []
    reminders: List[Dict[str, str]] = []
    notes: List[str] = []
    in_cron_table = False
    cron_headers: List[str] = []
    in_stats_table = False
    in_agent_section = False
    in_reminders = False
    in_notes = False

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # Weather / 天气
        if (line.startswith("## ") and _heading_matches(line, _WEATHER_HEADING_ALIASES)) or (
            line.startswith("**") and _heading_matches(line, _WEATHER_HEADING_ALIASES)
        ):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                next_line = lines[j].strip()
                if next_line and not next_line.startswith("## "):
                    weather = next_line
                    i = j + 1
                    continue
            i += 1
            continue

        # Daily overview / 今日概要
        if line.startswith("## ") and _heading_matches(line, _SUMMARY_HEADING_ALIASES):
            j = i + 1
            paras = []
            while j < len(lines) and not lines[j].startswith("## "):
                t = lines[j].strip()
                if t:
                    paras.append(t)
                j += 1
            summary = " ".join(paras)

            # 解析 * 主条目 + - 子条目格式（按行解析）
            current_topic_title = ""
            current_items: List[str] = []
            has_star_format = any(p.startswith("* ") or re.match(r"^-\s+\*\*.+?\*\*[：:]", p) for p in paras)
            if has_star_format:
                for p in paras:
                    p = p.strip()
                    if not p:
                        continue
                    if p.startswith("* ") or re.match(r"^-\s+\*\*.+?\*\*[：:]", p):
                        # 保存上一个 topic
                        if current_topic_title:
                            summary_topics.append({"title": current_topic_title, "items": current_items})
                        current_topic_title = _clean_markdown_summary_item(p[2:].strip())
                        current_items = []
                    elif p.startswith("- ") and current_topic_title:
                        current_items.append(_clean_markdown_summary_item(p[2:].strip()))
                    elif p.startswith("---"):
                        continue
                if current_topic_title:
                    summary_topics.append({"title": current_topic_title, "items": current_items})
            else:
                # Fallback: 解析 **bold** 分割的 topic
                full_text = " ".join(paras)
                parts = re.split(r"\*\*(.+?)\*\*", full_text)
                if len(parts) > 1:
                    idx = 0
                    current_topic_title = ""
                    current_items = []
                    while idx < len(parts):
                        part = parts[idx].strip()
                        if idx % 2 == 1:
                            if current_topic_title and current_items:
                                summary_topics.append({"title": current_topic_title, "items": current_items})
                            current_topic_title = part
                            current_items = []
                        else:
                            if part:
                                sub_parts = re.split(r"[；;]", part)
                                for sp in sub_parts:
                                    sp = sp.strip().rstrip("。").rstrip("：").rstrip(":")
                                    if sp and len(sp) > 5:
                                        current_items.append(sp)
                        idx += 1
                    if current_topic_title and current_items:
                        summary_topics.append({"title": current_topic_title, "items": current_items})
            i = j
            continue

        # Scheduled jobs / 定时任务表格（5列）
        if line.startswith("## ") and _heading_matches(line, _SCHEDULED_JOBS_HEADING_ALIASES):
            in_cron_table = True
            cron_headers = []
            i += 1
            continue

        if in_cron_table:
            if line.startswith("|"):
                cols = [col.strip() for col in line.strip().strip("|").split("|")]
                first_col = cols[0] if cols else ""
                if not cols or _is_table_separator(first_col) or all(_is_table_separator(col) for col in cols):
                    i += 1
                    continue
                if any(h in first_col for h in ["任务名称", "任务", "触发方式", "执行时间", "状态", "备注", "时间", "Task", "Trigger", "Execution Time", "Status", "Note", "Time"]):
                    cron_headers = cols
                    i += 1
                    continue
                if len(cols) >= 5:
                    if cron_headers and (cron_headers[0] in ("时间", "Time") or "时间" in cron_headers[0]):
                        cron_tasks.append({
                            "task": cols[1],
                            "trigger": "",
                            "time": cols[0],
                            "status": cols[2],
                            "note": cols[4],
                        })
                    else:
                        cron_tasks.append({
                            "task": cols[0],
                            "trigger": cols[1],
                            "time": cols[2],
                            "status": cols[3],
                            "note": cols[4],
                        })
                elif len(cols) >= 3:
                    if cron_headers and (cron_headers[0] in ("时间", "Time") or "时间" in cron_headers[0]):
                        cron_tasks.append({"task": cols[1], "status": cols[2], "note": cols[0]})
                    else:
                        cron_tasks.append({"task": cols[1], "status": cols[2], "note": cols[0]})
            elif line.startswith("## ") or (line.strip() == "" and i + 1 < len(lines) and lines[i + 1].startswith("## ")):
                in_cron_table = False
            i += 1
            continue

        # Daily stats / 本日统计表格（支持多列：指标 | openclaw | gemini-cli | ... | 合计）
        if line.startswith("## ") and _heading_matches(line, _DAILY_STATS_HEADING_ALIASES):
            in_stats_table = True
            stats_headers = []
            i += 1
            continue

        if in_stats_table:
            if line.startswith("|---") or re.match(r"^\|\s*:?-+\s*\|", line):
                i += 1
                continue
            # 解析表格行，提取所有列
            cols = [c.strip().replace("**", "") for c in line.strip().strip("|").split("|")]
            if len(cols) >= 2:
                # 首行：表头（指标 | openclaw | ... | 合计）
                if cols[0] in ("指标", "Metric") or cols[0] == "":
                    stats_headers = cols
                    i += 1
                    continue
                label = cols[0].strip()
                if not label:
                    i += 1
                    continue
                # 检测多列表格（>=3列，最后一列含“合计”）
                if len(cols) >= 3 and (len(stats_headers) > 2 and stats_headers[-1] in ("合计", "Total")):
                    # 多列模式：存每一行所有列值
                    row = {"label": label, "_cols": cols[1:]}
                    daily_stats.append(row)
                    # 如果有 byAgent 行
                    if label == "byAgent":
                        daily_stats.append({"label": "byAgent", "value": " / ".join(v for v in cols[1:] if v and v != ":---")})
                else:
                    # 旧两列模式
                    value = cols[1].strip()
                    if label in ("指标", "Metric") or value in ("数值", "Value"):
                        i += 1
                        continue
                    daily_stats.append({"label": label, "value": value})
            elif line.startswith("## ") or line.startswith("- byAgent"):
                if line.startswith("- byAgent"):
                    daily_stats.append({"label": "byAgent", "value": line.replace("- byAgent: ", "").strip()})
                    i += 1
                    continue
                in_stats_table = False
            elif not line.startswith("|"):
                in_stats_table = False
            i += 1
            continue

        # ── Agent Work / Agent工作 section ──
        if line.startswith("## ") and _heading_matches(line, _AGENT_WORK_HEADING_ALIASES):
            in_agent_section = True
            i += 1
            continue
        if in_agent_section and line.startswith("## "):
            in_agent_section = False
            current_agent = None

        # Important notices / 重要提醒（结构化：支持分类标题 + 子条目）
        if line.startswith("## ") and _heading_matches(line, _IMPORTANT_NOTICES_HEADING_ALIASES):
            in_reminders = True
            current_reminder_title = ""
            current_reminder_items: List[str] = []
            i += 1
            continue

        if in_reminders:
            if line.startswith("## ") or line.strip() == "---":
                # 保存最后一个 reminder group
                if current_reminder_title or current_reminder_items:
                    reminders.append({"title": current_reminder_title, "items": current_reminder_items})
                    current_reminder_title = ""
                    current_reminder_items = []
                in_reminders = False
                if line.strip() == "---":
                    i += 1
                    continue
            else:
                stripped = line.strip()
                if not stripped:
                    i += 1
                    continue
                # 格式1a：1. **标题**：描述（单行，无子条目）
                rm = re.match(r"^\d+\.\s*\*\*(.+?)\*\*[：:]\s*(.+)$", stripped)
                if rm:
                    # 保存上一个 group
                    if current_reminder_title or current_reminder_items:
                        reminders.append({"title": current_reminder_title, "items": current_reminder_items})
                    current_reminder_title = rm.group(1).strip()
                    current_reminder_items = [rm.group(2).strip()]
                    i += 1
                    continue
                # 格式1b：1. **标题**（无冒号，后跟缩进子条目）
                rm2 = re.match(r"^\d+\.\s*\*\*(.+?)\*\*\s*$", stripped)
                if rm2:
                    if current_reminder_title or current_reminder_items:
                        reminders.append({"title": current_reminder_title, "items": current_reminder_items})
                    current_reminder_title = rm2.group(1).strip()
                    current_reminder_items = []
                    i += 1
                    continue
                # 格式2：**分类标题**（无序号，可能后跟子条目）
                cat_m = re.match(r"^\*\*(.+?)\*\*\s*$", stripped)
                if cat_m:
                    if current_reminder_title or current_reminder_items:
                        reminders.append({"title": current_reminder_title, "items": current_reminder_items})
                    current_reminder_title = cat_m.group(1).strip()
                    current_reminder_items = []
                    i += 1
                    continue
                # 子条目：- xxx 或 3空格缩进的子项（如   - 现象：...）
                if stripped.startswith("- "):
                    current_reminder_items.append(stripped[2:].strip())
                    i += 1
                    continue
                # 续行文本（追加到当前 reminder 的最后一个 item 或 desc）
                if current_reminder_items:
                    current_reminder_items[-1] += " " + stripped
                i += 1
                continue

        # Notes / 备注（结构化：按 **bold** 标题分组）
        if line.startswith("## ") and _heading_matches(line, _NOTES_HEADING_ALIASES):
            in_notes = True
            current_note_title = ""
            current_note_items: List[str] = []
            i += 1
            continue

        if in_notes:
            if line.startswith("## ") or line.startswith("```json"):
                # 保存最后一个 note group
                if current_note_title or current_note_items:
                    notes.append({"title": current_note_title, "items": current_note_items})
                in_notes = False
            else:
                stripped = line.strip()
                if not stripped:
                    i += 1
                    continue
                # **Bold标题**（支持 "1. **Title**：" 格式）
                bold_m = re.match(r"^(?:\d+[.、]\s*)?\*\*(.+?)\*\*[：:]?\s*(.*)$", stripped)
                if bold_m:
                    if current_note_title or current_note_items:
                        notes.append({"title": current_note_title, "items": current_note_items})
                    current_note_title = bold_m.group(1).strip()
                    rest = bold_m.group(2).strip()
                    current_note_items = [rest] if rest else []
                elif stripped.startswith("- ") or re.match(r"^\d+[.、]", stripped):
                    text = re.sub(r"^[-\d.、]\s*", "", stripped)
                    if text:
                        current_note_items.append(text)
                elif stripped.startswith("|"):
                    # 表格行，保留原文
                    current_note_items.append(stripped)
                elif stripped.startswith("---"):
                    pass
                else:
                    # 普通文本
                    current_note_items.append(stripped)
                i += 1
                continue

        # ── 新格式 Agent 工作段落 ─────────────────────────────
        # 仅在 ## Agent工作 section 内识别
        if not in_agent_section:
            current_agent = None
        agent_h3 = re.match(r"^###\s+【([^】]+)】(?:\([^)]*\))?\s*$", line)
        bracket_agent_h3 = re.match(r"^###\s+\[([^\]]+)\](?:\s+\([^)]*\))?\s*$", line)
        plain_agent_h3 = re.match(r"^###\s+([A-Za-z][\w.-]*)(?:\s+\([^)]*\))?\s*$", line)
        # 旧格式：【agent】（无前缀）或 #### 【agent】**日期**
        agent_h = re.match(r"^【([^】]+)】(?:\*\*[^*]+\*\*)?\s*$", line)
        old_agent_h = re.match(r"^#### 【([^】]+)】(?:\*\*[^*]+\*\*)?", line)
        agent_match = agent_h3 or bracket_agent_h3 or plain_agent_h3 or agent_h or old_agent_h
        if agent_match and in_agent_section:
            current_agent = agent_match.group(1).strip()
            # 去掉可能的 (main) / (Isshin/lune) 后缀
            if "(" in current_agent:
                current_agent = current_agent.split("(")[0].strip()
            if current_agent not in agent_work:
                agent_work[current_agent] = []
            if current_agent not in agent_work_new:
                agent_work_new[current_agent] = []
            i += 1
            continue

        # 主任务行：
        # 任务行解析
        # 生产格式：**[时间段 HH:MM-HH:MM] - 任务名**
        # 兼容旧格式（宽松 fallback）
        if current_agent and in_agent_section:
            stripped = line.strip()
            task_candidate = stripped[2:].strip() if re.match(r"^[-*]\s+", stripped) else stripped
            is_task_line = False
            period = "其他"
            task_title = ""
            initial_sub_items: List[str] = []

            # 1. 生产格式：**[时间段 HH:MM(-HH:MM)] - 任务名**
            prod_m = re.match(r'^\*\*\[(凌晨|深夜|上午|中午|下午|傍晚|晚间|全天定时执行|Implementation|Review|Research|Maintenance|Scheduled Job|Other)\s*\d{1,2}:\d{2}(?:-\d{1,2}:\d{2})?\]\s*-\s*(.+?)\*\*\s*$', task_candidate)
            if prod_m:
                period = prod_m.group(1)
                task_title = prod_m.group(2).strip()
                is_task_line = True

            # 2. Fallback：任何 **...** 行视为任务行（兼容历史格式）
            if not is_task_line:
                bold_m = re.match(r'^\*\*(.+?)\*\*\s*$', task_candidate)
                if bold_m:
                    inner = bold_m.group(1).strip()
                    period_m = re.search(r'(凌晨|深夜|上午|中午|下午|傍晚|晚间|全天定时执行|Implementation|Review|Research|Maintenance|Scheduled Job|Other)', inner)
                    if period_m:
                        period = period_m.group(1)
                    task_title = inner
                    task_title = re.sub(r'^\[?[^\]]*\]?\s*(?:\([^)]*\))?\s*[-–——·]\s*', '', task_title, count=1)
                    task_title = re.sub(r'^\[?(?:凌晨|深夜|上午|中午|下午|傍晚|晚间|全天定时执行|Implementation|Review|Research|Maintenance|Scheduled Job|Other)\]?', '', task_title, count=1)
                    task_title = re.sub(r'^\s*(?:\([^)]*\))?\s*[-–——·]\s*', '', task_title, count=1)
                    task_title = task_title.strip().strip('—').strip('-').strip()
                    is_task_line = True

            # 2b. Bullet-bold summary line: - **任务名**：说明
            if not is_task_line:
                bullet_bold_m = re.match(r'^\*\*(.+?)\*\*(?:[：:]\s*(.*))?$', task_candidate)
                if bullet_bold_m:
                    task_title = bullet_bold_m.group(1).strip()
                    rest = (bullet_bold_m.group(2) or "").strip()
                    if rest:
                        initial_sub_items.append(rest)
                    is_task_line = True

            # 3. 旧格式：时间段 - 任务名（无 ** 包裹）
            if not is_task_line:
                old_m = re.match(r'^(凌晨|深夜|上午|中午|下午|傍晚|晚间|Implementation|Review|Research|Maintenance|Scheduled Job|Other)\s*[-–—]\s*(.+?)\s*[：:]?\s*$', stripped)
                if old_m:
                    period = old_m.group(1)
                    task_title = old_m.group(2).strip()
                    is_task_line = True

            # 4. 旧格式：- **任务名**：
            if not is_task_line:
                old_dash_m = re.match(r'^- \*\*(.+?)\*\*[：:]\s*$', stripped)
                if not old_dash_m:
                    old_dash_m = re.match(r'^- ([^：:]+?)[：:]\s*$', stripped)
                if old_dash_m:
                    task_title = old_dash_m.group(1).strip()
                    is_task_line = True

            if is_task_line and task_title:
                current_task_entry = {"period": period, "main_task": task_title, "sub_items": list(initial_sub_items)}
                agent_work_new[current_agent].append(current_task_entry)
                j = i + 1
                while j < len(lines):
                    raw_line = lines[j]
                    next_l = raw_line.strip()
                    if not next_l:
                        j += 1
                        continue
                    if re.match(r"^##\s", next_l) or re.match(r"^###\s", next_l) or re.match(r"^####\s", next_l):
                        break
                    if re.match(r"^【[^】]+】\s*$", next_l):
                        break
                    if re.match(r'^\*\*', next_l):
                        break
                    if re.match(r'^[-*]\s+\*\*', next_l):
                        break
                    if re.match(r'^(凌晨|深夜|上午|中午|下午|傍晚|晚间|全天定时执行|Implementation|Review|Research|Maintenance|Scheduled Job|Other)\s*[-–—\[\*]', next_l):
                        break
                    # Level 3: indented sub-detail (3+ spaces + "- ")
                    if raw_line.startswith("   - ") or raw_line.startswith("\t- "):
                        # Attach to last sub_item as a detail
                        if current_task_entry["sub_items"]:
                            last_sub = current_task_entry["sub_items"][-1]
                            if isinstance(last_sub, dict):
                                last_sub.setdefault("details", []).append(next_l[2:].strip())
                            else:
                                # Convert plain string sub_item to dict with details
                                detail_text = next_l[2:].strip()
                                current_task_entry["sub_items"][-1] = {
                                    "text": last_sub,
                                    "details": [detail_text],
                                }
                        j += 1
                        continue
                    # Level 2: sub-item
                    if next_l.startswith("- "):
                        current_task_entry["sub_items"].append(next_l[2:].strip())
                    elif re.match(r'^\d+[.、]', next_l):
                        num_m2 = re.match(r'^(\d+)[.、](.+)$', next_l)
                        if num_m2:
                            current_task_entry["sub_items"].append(num_m2.group(2).strip())
                    j += 1
                i = j
                continue

            # 续行：加入最后一项的 sub_items
            if stripped and not stripped.startswith("## ") and not stripped.startswith("### ") and not stripped.startswith("#### ") and not stripped.startswith("【") and not stripped.startswith("**"):
                if current_agent in agent_work_new and agent_work_new[current_agent]:
                    last_entry = agent_work_new[current_agent][-1]
                    if stripped.startswith("- "):
                        last_entry["sub_items"].append(stripped[2:].strip())
                    elif re.match(r'^\d+[.、]', stripped):
                        m3 = re.match(r'^(\d+)[.、](.+)$', stripped)
                        if m3:
                            last_entry["sub_items"].append(m3.group(2).strip())
                    else:
                        last_entry["sub_items"].append(stripped)

        # 旧格式：#### 【agent】
        agent_h2 = re.match(r"^#### \s*【([^】]+)】\s*$", line)
        if agent_h2 and in_agent_section:
            current_agent = agent_h2.group(1).strip()
            if current_agent not in agent_work:
                agent_work[current_agent] = []
            if current_agent not in agent_work_new:
                agent_work_new[current_agent] = []
            i += 1
            continue

        # - **【coder】**：描述
        agent_m = re.match(r"^-\s+\*\*【([^】]+)】\*\*[：:]?\s*(.*)$", line)
        if agent_m and in_agent_section:
            name = agent_m.group(1).strip()
            entry = agent_m.group(2).strip()
            current_agent = name
            if current_agent not in agent_work:
                agent_work[current_agent] = []
            if current_agent not in agent_work_new:
                agent_work_new[current_agent] = []
            if entry:
                agent_work[current_agent].append(entry)
                current_agent = None
            i += 1
            continue

        # 遇到任何三级/二级标题重置 agent 上下文
        if (line.startswith("### ") and not re.match(r"^###\s+【", line)) or (line.startswith("## ") and "Agent" not in line):
            current_agent = None

        i += 1

    if in_notes and (current_note_title or current_note_items):
        notes.append({"title": current_note_title, "items": current_note_items})
    if in_reminders and (current_reminder_title or current_reminder_items):
        reminders.append({"title": current_reminder_title, "items": current_reminder_items})

    try:
        dt = datetime.strptime(full_date, "%Y-%m-%d")
        display_date = full_date[5:].replace("-", "-")
        dow = DAY_NAME[dt.weekday()]
    except:
        display_date = full_date
        dow = ""

    jsonl_stats = _get_jsonl_stats(full_date) if use_live_sources else {
        "hourlyTokens": {},
        "agentStats": {},
        "sessionStats": {"sessions": 0, "messages": 0},
        "tokenUsage": {},
    }
    hourly_tokens = jsonl_stats["hourlyTokens"]
    agent_stats = jsonl_stats["agentStats"]

    # ── 从 ## Notes / 备注 解析 Token 真实数据 ────────────────
    token_stats: Dict[str, Any] = {}
    in_note = False
    note_lines = []
    for l in lines:
        if l.strip().startswith("## ") and _heading_matches(l, _NOTES_HEADING_ALIASES):
            in_note = True
            continue
        if in_note:
            if l.strip().startswith("## "):
                break
            note_lines.append(l.strip())

    all_note = " ".join(note_lines)
    if "入力" in all_note or "输入" in all_note or "Token 消耗" in all_note:
        field_map = {
            r"入力.*?([\d,]+)": "input",
            r"出品.*?([\d,]+)": "output",
            r"キャッシュ.*?([\d,]+)": "cache",
            r"総処理.*?([\d,]+)": "total",
            r"输入.*?([\d,]+)": "input",
            r"输出.*?([\d,]+)": "output",
            r"缓存.*?([\d,]+)": "cache",
            r"总处理.*?([\d,]+)": "total",
            r"API 调用.*?([\d,]+)": "apiCalls",
            r"预估费用.*?([\d.]+)": "cost",
            r"命中率.*?([\d.]+)%": "cacheHitRate",
        }
        for pat, key in field_map.items():
            m = re.search(pat, all_note)
            if m:
                val = m.group(1).replace(",","").strip()
                if val:
                    token_stats[key] = float(val) if "." in val else int(val)
                else:
                    token_stats[key] = 0

    # 优先从 JSON 块读数据（零扫描），fallback 到 JSONL
    jb_metrics = (json_block or {}).get("metrics") if json_block else None
    jb_token_metrics = jb_metrics if isinstance(jb_metrics, dict) else None
    if jb_token_metrics and not jb_token_metrics.get("total_tokens") and isinstance(jb_token_metrics.get("total"), dict):
        jb_token_metrics = jb_token_metrics["total"]
    if jb_token_metrics and isinstance(jb_token_metrics, dict) and jb_token_metrics.get("total_tokens"):
        # JSON 块有完整 metrics，直接用，不扫 JSONL
        token_stats_merged = {
            "input": jb_token_metrics.get("input_tokens"),
            "output": jb_token_metrics.get("output_tokens"),
            "cache": jb_token_metrics.get("cache_read"),
            "total": jb_token_metrics.get("total_tokens"),
            "apiCalls": jb_token_metrics.get("api_calls"),
            "cacheHitRate": jb_token_metrics.get("cache_hit_rate"),
            "cost": jb_token_metrics.get("estimated_cost_cny"),
            "source": "json_block",
        }
        token_stats_merged = {k: v for k, v in token_stats_merged.items() if v is not None}
    elif not use_live_sources:
        token_stats_merged = {**token_stats, "source": "foundation_snapshot"} if token_stats else {"source": "foundation_snapshot"}
    else:
        token_stats_merged = _merge_token_stats(token_stats, jsonl_stats.get("tokenUsage", {}))

    # JSON 块的 todos/tasks 作为数据源
    jb_todos = (json_block or {}).get("todos") if json_block else None
    jb_tasks = (json_block or {}).get("tasks") if json_block else None

    # ── 从 dailyStats 提取 KPI 结构化字段 ──
    # 优先从多列表格的「合计」列读取，退回旧两列模式
    parsed_kpi = {}

    # Pre-fill parsedKpi with JSON block metrics.total sessions data
    if json_block and isinstance(json_block.get("metrics"), dict):
        jb_total = json_block["metrics"].get("total")
        if isinstance(jb_total, dict):
            if jb_total.get("active_sessions"):
                parsed_kpi["active_sessions"] = jb_total["active_sessions"]
            if jb_total.get("sessions_total"):
                parsed_kpi["sessions_total"] = jb_total["sessions_total"]

    _total_row = None  # 合计行
    for s in daily_stats:
        label = s["label"].replace("**", "").strip()
        if "_cols" in s:
            cols = s["_cols"]
            if label == "total_tokens":
                _total_row = cols
        else:
            value = s.get("value", "").replace("**", "").replace(",", "").strip()
            if label == "total_tokens":
                try: parsed_kpi["total_tokens"] = int(value)
                except: pass
            elif label == "input_tokens":
                try: parsed_kpi["input_tokens"] = int(value)
                except: pass
            elif label == "output_tokens":
                try: parsed_kpi["output_tokens"] = int(value)
                except: pass
            elif label == "cache_read":
                try: parsed_kpi["cache_read"] = int(value)
                except: pass
            elif label == "cache_write":
                try: parsed_kpi["cache_write"] = int(value)
                except: pass
            elif label == "api_calls":
                try: parsed_kpi["api_calls"] = int(value)
                except: pass
            elif label == "sessions_count":
                try: parsed_kpi["sessions_count"] = int(value)
                except: pass
            elif label == "messages_count":
                try: parsed_kpi["messages_count"] = int(value)
                except: pass
            elif label == "byAgent":
                parsed_kpi["byAgent"] = s.get("value", "")

    # 多列表格：从合计列提取各字段（按 stats_headers 顺序对应）
    if _total_row and len(stats_headers) > 2:
        for idx, s in enumerate(daily_stats):
            if "_cols" not in s:
                continue
            cols = s["_cols"]
            # 合计列是最后一列
            total_val = cols[-1].replace(",", "").replace("**", "").strip() if cols else ""
            label = s["label"].replace("**", "").strip()
            try:
                if total_val:
                    val_int = int(float(total_val))
                    if label == "input_tokens":
                        parsed_kpi["input_tokens"] = val_int
                    elif label == "output_tokens":
                        parsed_kpi["output_tokens"] = val_int
                    elif label == "cache_read":
                        parsed_kpi["cache_read"] = val_int
                    elif label == "cache_write":
                        parsed_kpi["cache_write"] = val_int
                    elif label == "api_calls":
                        parsed_kpi["api_calls"] = val_int
                    elif label == "sessions_count":
                        parsed_kpi["sessions_count"] = val_int
                    elif label == "messages_count":
                        parsed_kpi["messages_count"] = val_int
                    elif label == "total_tokens":
                        parsed_kpi["total_tokens"] = val_int
            except (ValueError, TypeError):
                pass

    # 缓存命中率 = cache_read / (input + cache_read)
    if parsed_kpi.get("cache_read") and parsed_kpi.get("input_tokens"):
        parsed_kpi["cache_hit_rate"] = round(
            parsed_kpi["cache_read"] / (parsed_kpi["input_tokens"] + parsed_kpi["cache_read"]) * 100, 1
        )

    # ── 解析智慧沉淀文件 ──
    lessons = []
    infra_changes = []
    short_date = full_date[2:].replace("-", "")  # "2026-04-28" → "260428"
    root = _diary_root()
    language_profile = _pipeline_language_profile()
    wisdom_file = next(
        iter(diary_report_paths(root, full_date, "learning", language_profile=language_profile)),
        diary_learning_report_path(root, full_date, language_profile=language_profile),
    )
    if wisdom_file.exists():
        try:
            wisdom_text = wisdom_file.read_text(encoding="utf-8")
            wisdom_lines = wisdom_text.split("\n")
            # Parse Lessons / 黄金教训 section
            in_lessons = False
            current_lesson = None
            current_field = None
            field_buffer = []

            def flush_structured_lesson_field():
                nonlocal current_field, field_buffer, current_lesson
                if current_lesson is not None and current_field:
                    current_lesson[current_field] = "\n".join(field_buffer).strip()
                current_field = None
                field_buffer = []

            def flush_structured_lesson():
                nonlocal current_lesson
                flush_structured_lesson_field()
                if current_lesson:
                    problem = current_lesson.get("problem") or current_lesson.get("title") or ""
                    root_cause = current_lesson.get("rootCause") or ""
                    suggestion = current_lesson.get("suggestion") or ""
                    if problem or root_cause or suggestion:
                        lessons.append(
                            {
                                "agent": current_lesson.get("agent") or "unknown",
                                "problem": problem,
                                "rootCause": root_cause,
                                "suggestion": suggestion,
                            }
                        )
                current_lesson = None

            for wl in wisdom_lines:
                wl_s = wl.strip()
                if wl_s.startswith("## ") and _heading_matches(wl_s, _LESSONS_HEADING_ALIASES):
                    in_lessons = True
                    continue
                if wl_s.startswith("## ") and in_lessons:
                    flush_structured_lesson()
                    in_lessons = False
                    continue
                if in_lessons and wl_s.startswith("### "):
                    flush_structured_lesson()
                    title = wl_s[4:].strip()
                    tm = re.match(r"^【([^】]+)】\s*(.*)$", title)
                    if tm is None:
                        tm = re.match(r"^\[([^\]]+)\]\s*(.*)$", title)
                    current_lesson = {
                        "agent": tm.group(1).strip() if tm else "unknown",
                        "title": tm.group(2).strip() if tm else title,
                    }
                    continue
                if in_lessons and current_lesson is not None and wl_s.startswith("#### "):
                    flush_structured_lesson_field()
                    label = wl_s[5:].strip()
                    current_field = _LESSON_FIELD_ALIASES.get(label)
                    field_buffer = []
                    continue
                if in_lessons and current_lesson is not None and current_field:
                    field_buffer.append(wl)
                    continue
                if in_lessons and wl_s.startswith("- "):
                    # Format: - **【agent】**: 问题描述。解决建议：xxx。
                    lm = re.match(r"^-\s+\*\*【([^】]+)】\*\*[：:]\s*(.+)$", wl_s)
                    if lm:
                        agent_name = lm.group(1).strip()
                        rest = lm.group(2).strip()
                        suggestion_sep = "解决建议"
                        sep_idx = rest.find(suggestion_sep)
                        if sep_idx >= 0:
                            problem = rest[:sep_idx].rstrip("。：:")
                            suggestion = rest[sep_idx + len(suggestion_sep):].lstrip("：: ")
                        else:
                            problem = rest
                            suggestion = ""
                        lessons.append({"agent": agent_name, "problem": problem, "suggestion": suggestion})
            if in_lessons:
                flush_structured_lesson()
            # Parse Infrastructure Updates / 基建变动 section
            in_infra = False
            for wl in wisdom_lines:
                wl_s = wl.strip()
                if wl_s.startswith("## ") and _heading_matches(wl_s, _INFRA_HEADING_ALIASES):
                    in_infra = True
                    continue
                if wl_s.startswith("## ") and in_infra:
                    in_infra = False
                    continue
                if in_infra and wl_s.startswith("|"):
                    cols = [c.strip() for c in wl_s.strip().strip("|").split("|")]
                    if len(cols) >= 3:
                        # Skip header row and separator row
                        if cols[0] in ("对象", "Object", "", "---", ":---") or re.match(r'^:?-{3,}$', cols[0]):
                            continue
                        infra_changes.append({"target": cols[0], "change": cols[1], "current": cols[2] if len(cols) > 2 else ""})
        except Exception:
            pass

    # Strip JSON block from raw content for display
    raw_display = re.sub(r"```json\n[\s\S]+?\n```", "", raw).strip()

    return {
        "date": full_date,
        "languageProfile": _pipeline_language_profile(),
        "displayDate": display_date,
        "dayOfWeek": dow,
        "weather": weather,
        "summary": summary,
        "agentWork": _summarize_agent_work(agent_work, full_date),
        "agentWorkNew": agent_work_new,
        "summaryTopics": summary_topics,
        "cronTasks": detect_cron_tasks(full_date) if use_live_sources else cron_tasks,
        "todos": jb_todos or [],
        "dailyStats": daily_stats,
        "dailyStatsHeaders": stats_headers,
        "reminders": reminders,
        "notes": notes,
        "activityState": (json_block or {}).get("activityState") or "",
        "hourlyTokens": hourly_tokens,
        "agentStats": agent_stats,
        "sessionBySource": _extract_session_by_source(json_block),
        "tokenStats": token_stats_merged,
        "sessionStats": jsonl_stats["sessionStats"],
        "parsedKpi": {
            **parsed_kpi,
            "messages_count": (
                sum((a.get("messages") or 0) for a in agent_stats.values())
                if use_live_sources
                else parsed_kpi.get("messages_count", 0)
            ),
        },
        "rawContent": raw_display,
        "lessons": lessons,
        "infraChanges": infra_changes,
        "ragStatsSnapshot": (json_block or {}).get("ragStats") or {},
        "memoryStatsSnapshot": (json_block or {}).get("memoryStats") or {},
    }


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _safe_rate(num: int, den: int) -> float:
    return round(num / den * 100, 1) if den else 0


def _normalize_llm_high_frequency_topics(value: Any, *, limit: int = 8) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    topics = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or item.get("title") or item.get("name") or "").replace("**", "").strip()
        if not topic or topic in seen:
            continue
        normalized: Dict[str, Any] = {"topic": topic}
        count = _safe_int(item.get("count"))
        if count > 0:
            normalized["count"] = count
        reason = str(item.get("reason") or item.get("evidence") or item.get("description") or "").replace("**", "").strip()
        if reason:
            normalized["reason"] = reason
        topics.append(normalized)
        seen.add(topic)
        if len(topics) >= limit:
            break
    return topics


def _period_summary_high_frequency_topics(summary_metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
    summary = summary_metrics.get("summary") if isinstance(summary_metrics, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    return _normalize_llm_high_frequency_topics(
        summary.get("highFrequencyTopics") or summary_metrics.get("highFrequencyTopics")
    )


def _period_bucket(hour: int) -> str:
    if 4 <= hour < 12:
        return "上午"
    if 12 <= hour < 18:
        return "下午"
    if 18 <= hour < 24:
        return "晚上"
    return "凌晨"


def _get_rag_memory_stats(*, include_rag: bool = True, include_memory: bool = True) -> tuple[dict, dict]:
    rag_stats = {"entries": 0, "sizeMB": 0}
    if include_rag:
        rag_stats = _active_rag_index_stats()

    memory_files = 0
    memory_size = 0
    if include_memory:
        memory_root = _external_tool_path("openclaw", "memoryRoot")
        if memory_root.exists():
            for f in memory_root.rglob("*"):
                if f.is_file():
                    memory_files += 1
                    try:
                        memory_size += f.stat().st_size
                    except OSError:
                        pass

    return (
        rag_stats,
        {"sessionFiles": memory_files, "totalSizeMB": round(memory_size / 1024 / 1024, 1)},
    )


def _active_rag_index_stats() -> dict:
    try:
        from agentic_rag.rag_status import read_rag_status

        status = read_rag_status(count_legacy_entries=False, probe_server=False)
        v2 = status.get("v2") if isinstance(status.get("v2"), dict) else {}
        active_index = status.get("activeIndex") if isinstance(status.get("activeIndex"), dict) else {}
        index_path_value = v2.get("activeIndexPath") or active_index.get("indexPath")
        if not index_path_value:
            return _unavailable_rag_index_stats("active-v2-index-missing")
        index_path = Path(str(index_path_value))
        if v2.get("ready") and index_path.exists():
            stat = index_path.stat()
            return {
                "entries": _safe_int(v2.get("chunkCount")),
                "sizeMB": round(stat.st_size / 1024 / 1024, 1),
                "source": "rag-v2-active",
                "indexPath": str(index_path),
                "updatedAt": v2.get("updatedAt"),
            }
    except Exception:
        return _unavailable_rag_index_stats("rag-status-unavailable")
    return _unavailable_rag_index_stats("active-v2-index-not-ready")


def _unavailable_rag_index_stats(reason: str) -> dict:
    return {
        "entries": 0,
        "sizeMB": 0,
        "source": "rag-v2-unavailable",
        "reason": reason,
    }


def _task_stats_snapshot(*, source: str = "foundation") -> dict:
    if source == "foundation":
        try:
            from data_foundation.nova_task import diary_tasks_snapshot
            from data_foundation.paths import load_paths

            paths = load_paths()
            snapshot = diary_tasks_snapshot(paths)
            completed = int(snapshot.get("Completed", 0) or 0)
            in_progress = int(snapshot.get("InProgress", 0) or 0)
            total = completed + in_progress
            return {
                "completed": completed,
                "inProgress": in_progress,
                "total": total,
                "completionRate": _safe_rate(completed, total),
                "source": "foundation",
                "authority": "Nova-Task v2 SQLite",
            }
        except Exception:
            return {
                "completed": 0,
                "inProgress": 0,
                "total": 0,
                "completionRate": 0,
                "source": "foundation",
                "authority": "Nova-Task v2 SQLite",
            }
    return {
        "completed": 0,
        "inProgress": 0,
        "total": 0,
        "completionRate": 0,
        "source": "legacy-retired",
        "authority": "Nova-Task v2 SQLite",
    }


def _period_diary_rollup(start_date, days: int, *, include_prior_baseline: bool = True) -> Dict[str, Any]:
    """Materialize the dashboard report fields that otherwise require per-day diary parsing."""
    date_list = _period_dates(start_date, days)
    daily_series = []
    agent_activity: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"messages": 0, "tokens": 0, "days": set()})
    model_counter: Counter[str] = Counter()
    topic_counter: Counter[str] = Counter()
    cron_success = 0
    cron_failed = 0
    total_tokens = 0
    total_messages = 0
    total_api_calls = 0
    total_cache_read = 0
    total_cache_write = 0
    total_input = 0
    active_sessions = 0
    total_sessions = 0
    heatmap_values = {p: [0] * days for p in ("上午", "下午", "晚上", "凌晨")}
    heatmap_index = {dt.strftime("%Y-%m-%d"): index for index, dt in enumerate(date_list)}
    rag_snapshots = []
    memory_snapshots = []
    parsed_days = 0

    for dt in date_list:
        full_date = dt.strftime("%Y-%m-%d")
        _SUMMARIZE_CACHE.setdefault(full_date, {})
        diary_data = parse_diary(full_date)
        if not diary_data:
            daily_series.append({"date": full_date, "displayDate": dt.strftime("%m-%d"), "tokens": 0, "messages": 0, "cacheHitRate": 0})
            continue

        parsed_days += 1
        kpi = diary_data.get("parsedKpi") or {}
        token_stats = diary_data.get("tokenStats") or {}
        tokens = _safe_int(kpi.get("total_tokens") or token_stats.get("total"))
        messages = _safe_int(kpi.get("messages_count") or (diary_data.get("sessionStats") or {}).get("messages"))
        api_calls = _safe_int(kpi.get("api_calls"))
        cache_read = _safe_int(kpi.get("cache_read") or token_stats.get("cache"))
        cache_write = _safe_int(kpi.get("cache_write"))
        input_tokens = _safe_int(kpi.get("input_tokens") or token_stats.get("input"))
        cache_rate = kpi.get("cache_hit_rate") or token_stats.get("cacheHitRate") or _safe_rate(cache_read, input_tokens + cache_read)
        cache_rate = round(float(cache_rate or 0), 1)

        total_tokens += tokens
        total_messages += messages
        total_api_calls += api_calls
        total_cache_read += cache_read
        total_cache_write += cache_write
        total_input += input_tokens
        active_sessions += _safe_int(kpi.get("active_sessions"))
        total_sessions += _safe_int(kpi.get("sessions_total") or kpi.get("sessions_count"))
        if diary_data.get("ragStatsSnapshot"):
            rag_snapshots.append((full_date, diary_data["ragStatsSnapshot"]))
        if diary_data.get("memoryStatsSnapshot"):
            memory_snapshots.append((full_date, diary_data["memoryStatsSnapshot"]))

        daily_series.append({
            "date": full_date,
            "displayDate": dt.strftime("%m-%d"),
            "tokens": tokens,
            "messages": messages,
            "cacheHitRate": cache_rate,
        })

        for agent, stats in (diary_data.get("agentStats") or {}).items():
            agent_activity[agent]["messages"] += _safe_int(stats.get("messages"))
            agent_activity[agent]["tokens"] += _safe_int(stats.get("tokens"))
            agent_activity[agent]["days"].add(full_date)

        for topic in diary_data.get("summaryTopics") or []:
            title = (topic.get("title") or topic.get("topic") or "").replace("**", "").strip()
            if title:
                topic_counter[title] += 1

        for task in diary_data.get("cronTasks") or []:
            status = task.get("status", "")
            if "成功" in status or "✅" in status:
                cron_success += 1
            elif status:
                cron_failed += 1

        hourly = diary_data.get("hourlyTokens") or {}
        for hour, value in hourly.items():
            try:
                h = int(hour)
            except (TypeError, ValueError):
                continue
            business_date = dt - timedelta(days=1) if h < 4 else dt
            index = heatmap_index.get(business_date.strftime("%Y-%m-%d"))
            if index is not None:
                heatmap_values[_period_bucket(h)][index] += _safe_int(value)

    agent_activity_out = {}
    for agent, stats in agent_activity.items():
        days_active = len(stats["days"])
        agent_activity_out[agent] = {
            "messages": stats["messages"],
            "tokens": stats["tokens"],
            "days_active": days_active,
            "total_days": days,
            "active_rate": _safe_rate(days_active, days),
        }

    model_usage = [
        {"model": name, "tokens": tokens}
        for name, tokens in model_counter.most_common(8)
    ]
    if not model_usage:
        model_usage = [
            {"model": agent, "tokens": stats["tokens"]}
            for agent, stats in sorted(agent_activity_out.items(), key=lambda kv: kv[1]["tokens"], reverse=True)[:8]
            if stats["tokens"] > 0
        ]
    cron_total = cron_success + cron_failed
    prior_snapshots = _prior_knowledge_snapshot(start_date) if include_prior_baseline else {}
    rag_current = rag_snapshots[-1][1] if rag_snapshots else {}
    memory_current = memory_snapshots[-1][1] if memory_snapshots else {}
    return {
        "parsedDays": parsed_days,
        "kpi": {
            "totalTokens": total_tokens,
            "totalMessages": total_messages,
            "totalApiCalls": total_api_calls,
            "activeSessions": active_sessions,
            "totalSessions": total_sessions,
            "cacheHitRate": _safe_rate(total_cache_read + total_cache_write, total_input + total_cache_read + total_cache_write),
            "cronSuccessRate": _safe_rate(cron_success, cron_total),
            "agentCount": len(agent_activity_out),
        },
        "dailyTokenSeries": daily_series,
        "modelUsage": model_usage,
        "agentActivity": agent_activity_out,
        "cronStats": {"success": cron_success, "failed": cron_failed, "rate": _safe_rate(cron_success, cron_total)},
        "topTopics": [{"topic": topic, "count": count} for topic, count in topic_counter.most_common(20)],
        "knowledgePeriod": {
            "rag": _period_snapshot_delta(rag_current, rag_snapshots, prior_snapshots.get("rag"), "entries", "sizeMB"),
            "memory": _period_snapshot_delta(memory_current, memory_snapshots, prior_snapshots.get("memory"), "sessionFiles", "totalSizeMB"),
        },
        "hourlyHeatmap": {
            "dates": [d.strftime("%Y-%m-%d") for d in date_list],
            "periods": [{"label": p, "values": heatmap_values[p]} for p in ("上午", "下午", "晚上", "凌晨")],
        },
    }


def _period_asset_breakdown(start_date, days: int) -> Dict[str, Any]:
    """Use the AI Assets attribution logic, restricted to a selected report period."""
    from . import ai_assets

    period_dates = {start_date + timedelta(days=index) for index in range(days)}
    try:
        all_entries, _session_counts, _usage_cache = ai_assets._scan_usage_incremental()
        try:
            hermes_entries, _hermes_session_count = ai_assets._scan_all_hermes()
        except Exception:
            hermes_entries = []
        all_entries = {**all_entries, "Hermes": hermes_entries}
    except Exception:
        all_entries = {}
        for tool_name, scanner in ai_assets._ALL_SCANNERS:
            try:
                entries, _ = scanner()
            except Exception:
                entries = []
            all_entries[tool_name] = entries
    filtered: Dict[str, list] = {}
    for tool_name, entries in all_entries.items():
        kept = []
        for entry in entries:
            timestamp = entry.get("timestamp", "")
            if not timestamp:
                continue
            current = ai_assets._utc_to_local(timestamp)
            business_date = current.date() - timedelta(days=1) if current.hour < 4 else current.date()
            if business_date in period_dates:
                kept.append(entry)
        filtered[tool_name] = kept

    workspace_rows = ai_assets._aggregate_by_workspace(filtered)
    workspace_attribution_qa = ai_assets._workspace_attribution_qa(filtered, workspace_rows)
    activity_days: Dict[tuple, set] = defaultdict(set)
    for tool_name, entries in filtered.items():
        for entry in entries:
            current = ai_assets._utc_to_local(entry.get("timestamp", ""))
            business_date = current.date() - timedelta(days=1) if current.hour < 4 else current.date()
            group = canonical_workspace_name(entry.get("usageGroup") or tool_name)
            activity_days[(tool_name, group)].add(business_date.isoformat())
    for row in workspace_rows:
        active = len(activity_days[(row["tool"], row["name"])])
        row.update({
            "days_active": active,
            "total_days": days,
            "active_rate": _safe_rate(active, days),
        })

    periods = {slot: [0] * days for slot in ("上午", "下午", "晚上", "凌晨")}
    indices = {(start_date + timedelta(days=index)): index for index in range(days)}
    for entries in filtered.values():
        for entry in entries:
            current = ai_assets._utc_to_local(entry.get("timestamp", ""))
            business_date = current.date() - timedelta(days=1) if current.hour < 4 else current.date()
            index = indices.get(business_date)
            if index is None:
                continue
            tokens = (entry.get("input") or 0) + (entry.get("output") or 0) + (entry.get("cacheRead") or 0)
            periods[_period_bucket(current.hour)][index] += tokens

    return {
        "workspaceUsage": workspace_rows,
        "workspaceAttributionQa": workspace_attribution_qa,
        "models": ai_assets._aggregate_by_model(filtered)[:10],
        "assetHourlyHeatmap": {
            "dates": [(start_date + timedelta(days=index)).strftime("%Y-%m-%d") for index in range(days)],
            "periods": [{"label": slot, "values": periods[slot]} for slot in ("上午", "下午", "晚上", "凌晨")],
        },
    }


def _period_non_rag_asset_projection(start_date, days: int) -> Dict[str, Any]:
    """Build all non-RAG period assets that must not run during Foundation reads."""
    _, memory_stats = _get_rag_memory_stats(include_rag=False)
    return {
        **_period_diary_rollup(start_date, days, include_prior_baseline=False),
        **_period_asset_breakdown(start_date, days),
        "memoryStats": memory_stats,
        "knowledgePeriodMemoryCurrent": _session_memory_stats(),
    }


def _foundation_period_asset_breakdown(start_date, days: int) -> Optional[Dict[str, Any]]:
    """Read a precomputed legacy-compatible non-RAG period projection."""
    try:
        foundation_src = config.WORKSPACE_DIR / "src"
        if str(foundation_src) not in sys.path:
            sys.path.insert(0, str(foundation_src))
        from data_foundation.paths import load_paths
        from data_foundation.reports import read_period_projection

        end_date = start_date + timedelta(days=days - 1)
        return read_period_projection(load_paths(), start_date, end_date)
    except Exception:
        return None


def _foundation_period_page_projection(start_date, days: int) -> Optional[Dict[str, Any]]:
    """Read a precomputed structured diary period page projection."""
    try:
        foundation_src = config.WORKSPACE_DIR / "src"
        if str(foundation_src) not in sys.path:
            sys.path.insert(0, str(foundation_src))
        from data_foundation.diary_markdown import DIARY_PERIOD_PAGE_PROJECTION
        from data_foundation.paths import load_paths
        from data_foundation.reports import read_period_projection

        end_date = start_date + timedelta(days=days - 1)
        return read_period_projection(
            load_paths(),
            start_date,
            end_date,
            projection_type=DIARY_PERIOD_PAGE_PROJECTION,
        )
    except Exception:
        return None


def _foundation_period_summary_projection(start_date, days: int) -> Optional[Dict[str, Any]]:
    """Read a precomputed weekly/monthly generated summary snapshot."""
    try:
        foundation_src = config.WORKSPACE_DIR / "src"
        if str(foundation_src) not in sys.path:
            sys.path.insert(0, str(foundation_src))
        from data_foundation.paths import load_paths
        from data_foundation.period_summary import DIARY_PERIOD_SUMMARY_PROJECTION
        from data_foundation.reports import read_period_projection

        end_date = start_date + timedelta(days=days - 1)
        return read_period_projection(
            load_paths(),
            start_date,
            end_date,
            projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
        )
    except Exception:
        return None


def _previous_period_range(start_date, days: int) -> tuple:
    period_end = start_date + timedelta(days=days - 1)
    previous_end = start_date - timedelta(days=1)
    if start_date.day == 1:
        previous_start = previous_end.replace(day=1)
        return previous_start, previous_end
    return previous_end - timedelta(days=days - 1), previous_end


def _read_rag_daily_payload(snapshot_date) -> tuple | None:
    try:
        foundation_src = config.WORKSPACE_DIR / "src"
        if str(foundation_src) not in sys.path:
            sys.path.insert(0, str(foundation_src))
        from data_foundation.paths import load_paths
        from data_foundation.snapshots import read_rag_daily_status_snapshot

        snapshot = read_rag_daily_status_snapshot(load_paths(), snapshot_date)
    except Exception:
        return None
    if not snapshot:
        return None
    payload = snapshot.get("payload") or {}
    return payload.get("businessDate") or snapshot_date.isoformat(), payload


def _rag_period_delta_from_daily_snapshots(start_date, days: int, current_fallback: dict | None = None) -> dict:
    period_end = start_date + timedelta(days=days - 1)
    current = _read_rag_daily_payload(period_end)
    if current is None and current_fallback:
        current = (period_end.isoformat(), current_fallback)
    previous_start, previous_end = _previous_period_range(start_date, days)
    baseline = _read_rag_daily_payload(previous_end)
    if current is None:
        current = (period_end.isoformat(), {})
    return _period_snapshot_delta(current[1], [current], baseline, "entries", "sizeMB")


def _metric_delta(current: float, previous: float) -> dict:
    delta = round(current - previous, 1) if isinstance(current, float) or isinstance(previous, float) else current - previous
    return {
        "current": current,
        "previous": previous,
        "delta": delta,
        "percentDelta": round(delta / previous * 100, 1) if previous else None,
        "deltaAvailable": previous > 0,
    }


def _workload_comparison(current_kpi: dict, previous_kpi: dict | None) -> dict:
    previous_kpi = previous_kpi or {}
    return {
        "totalTokens": _metric_delta(_safe_int(current_kpi.get("totalTokens")), _safe_int(previous_kpi.get("totalTokens"))),
        "totalMessages": _metric_delta(_safe_int(current_kpi.get("totalMessages")), _safe_int(previous_kpi.get("totalMessages"))),
        "cacheHitRate": _metric_delta(float(current_kpi.get("cacheHitRate") or 0), float(previous_kpi.get("cacheHitRate") or 0)),
    }


def _period_refresh_policy(start_date, days: int) -> str:
    today = datetime.now(resolve_timezone()).date()
    period_end = start_date + timedelta(days=days - 1)
    if start_date <= today <= period_end:
        return "current-period-refresh"
    return "historical-manual-rebuild"


def _period_dates(start_date, days: int) -> list:
    return [start_date + timedelta(days=i) for i in range(days)]


def _empty_daily_token_series(date_list: list) -> list:
    return [
        {"date": dt.strftime("%Y-%m-%d"), "displayDate": dt.strftime("%m-%d"), "tokens": 0, "messages": 0, "cacheHitRate": 0}
        for dt in date_list
    ]


def _daily_token_series_from_asset_heatmap(date_list: list, asset_heatmap: Optional[Dict[str, Any]]) -> list:
    if not asset_heatmap:
        return _empty_daily_token_series(date_list)
    heatmap_dates = asset_heatmap.get("dates") or []
    totals = {selected_date: 0 for selected_date in heatmap_dates}
    for period in asset_heatmap.get("periods") or []:
        for index, value in enumerate(period.get("values") or []):
            if index < len(heatmap_dates):
                totals[heatmap_dates[index]] = totals.get(heatmap_dates[index], 0) + _safe_int(value)
    return [
        {
            "date": dt.strftime("%Y-%m-%d"),
            "displayDate": dt.strftime("%m-%d"),
            "tokens": totals.get(dt.strftime("%Y-%m-%d"), 0),
            "messages": 0,
            "cacheHitRate": 0,
        }
        for dt in date_list
    ]


def _projection_freshness(projection: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not projection:
        return None
    return {
        "source": "foundation",
        "projectionType": projection["projectionType"],
        "generatedAt": projection["generatedAt"],
        "status": projection["status"],
    }


def _missing_projection_freshness(start_date, days: int, *, source: str = "snapshot-missing") -> Dict[str, Any]:
    end_date = start_date + timedelta(days=days - 1)
    return {
        "source": source,
        "status": "projection_missing",
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "days": days,
        "refreshRequired": True,
        "refreshPolicy": _period_refresh_policy(start_date, days),
    }


def _foundation_snapshot_period_report(
    start_date,
    days: int,
    *,
    include_assets: bool,
    asset_projection: Optional[Dict[str, Any]],
    page_projection: Optional[Dict[str, Any]],
    summary_projection: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return an exact-period Foundation snapshot without falling back to daily Markdown parsing."""
    date_list = _period_dates(start_date, days)
    page_metrics = (page_projection or {}).get("metrics") or {}
    asset_metrics = (asset_projection or {}).get("metrics") or {}
    summary_metrics = (summary_projection or {}).get("metrics") or {}
    page_days = page_metrics.get("days") or []
    parsed_days = asset_metrics.get("parsedDays")
    if parsed_days is None:
        parsed_days = sum(1 for item in page_days if item.get("documents")) if page_days else 0
    workspace_usage = asset_metrics.get("workspaceUsage") or []
    models = asset_metrics.get("models") or []
    total_tokens = sum(_safe_int(item.get("tokens")) for item in workspace_usage)
    total_messages = sum(_safe_int(item.get("messages")) for item in workspace_usage)
    kpi = asset_metrics.get("kpi") or {
        "totalTokens": total_tokens,
        "totalMessages": total_messages,
        "totalApiCalls": 0,
        "activeSessions": 0,
        "totalSessions": 0,
        "cacheHitRate": 0,
        "cronSuccessRate": 0,
        "agentCount": len(workspace_usage),
    }
    if _safe_int(kpi.get("totalTokens")) == 0 and total_tokens > 0:
        kpi = {
            **kpi,
            "totalTokens": total_tokens,
            "totalMessages": total_messages,
            "agentCount": len(workspace_usage),
        }
    daily_token_series = asset_metrics.get("dailyTokenSeries") or []
    if not daily_token_series or sum(_safe_int(item.get("tokens")) for item in daily_token_series) == 0:
        daily_token_series = _daily_token_series_from_asset_heatmap(date_list, asset_metrics.get("assetHourlyHeatmap"))
    generated_summary = summary_metrics.get("summary") if summary_projection else None
    high_frequency_topics = _period_summary_high_frequency_topics(summary_metrics)
    rag_stats, _ = _get_rag_memory_stats(include_memory=False)
    memory_stats = asset_metrics.get("memoryStats") or {"sessionFiles": 0, "totalSizeMB": 0}
    period_memory = asset_metrics.get("knowledgePeriodMemoryCurrent") or memory_stats
    rag_period = _rag_period_delta_from_daily_snapshots(start_date, days, rag_stats)
    previous_start, previous_end = _previous_period_range(start_date, days)
    previous_asset_projection = _foundation_period_asset_breakdown(previous_start, (previous_end - previous_start).days + 1)
    previous_asset_metrics = (previous_asset_projection or {}).get("metrics") or {}
    workload_comparison = _workload_comparison(kpi, previous_asset_metrics.get("kpi"))
    data_freshness = {
        "periodSummary": (
            _projection_freshness(summary_projection)
            if summary_projection
            else _missing_projection_freshness(start_date, days)
        ),
        "periodPage": (
            _projection_freshness(page_projection)
            if page_projection
            else _missing_projection_freshness(start_date, days)
        ),
    }

    result = {
        "period": f"{date_list[0].strftime('%Y-%m-%d')} ~ {date_list[-1].strftime('%Y-%m-%d')}",
        "days": parsed_days,
        "kpi": kpi,
        "dailyTokenSeries": daily_token_series,
        "modelUsage": asset_metrics.get("modelUsage") or [{"model": item.get("name"), "tokens": item.get("tokens", 0)} for item in models],
        "models": models,
        "agentActivity": asset_metrics.get("agentActivity") or {},
        "taskStats": _task_stats_snapshot(source="foundation"),
        "cronStats": asset_metrics.get("cronStats") or {"success": 0, "failed": 0, "rate": 0},
        "ragStats": rag_stats,
        "memoryStats": memory_stats,
        "workloadComparison": workload_comparison,
        "highFrequencyTopics": high_frequency_topics,
        "topTopics": high_frequency_topics,
        "summaryTopics": page_metrics.get("summaryTopics") or [],
        "periodSummary": generated_summary,
        "agentWork": [],
        "hourlyHeatmap": asset_metrics.get("hourlyHeatmap") or asset_metrics.get("assetHourlyHeatmap") or {
            "dates": [d.strftime("%Y-%m-%d") for d in date_list],
            "periods": [{"label": p, "values": [0] * days} for p in ("上午", "下午", "晚上", "凌晨")],
        },
        "assetHourlyHeatmap": asset_metrics.get("assetHourlyHeatmap"),
        "lessons": page_metrics.get("lessons") or [],
        "dataFreshness": data_freshness,
    }

    if include_assets:
        if asset_projection:
            for key in ("workspaceUsage", "models", "assetHourlyHeatmap"):
                if key in asset_metrics:
                    result[key] = asset_metrics[key]
            knowledge_period = dict(asset_metrics.get("knowledgePeriod") or {})
            knowledge_period["rag"] = rag_period
            knowledge_period.setdefault(
                "memory",
                {
                    "currentCount": _safe_int(period_memory.get("sessionFiles")),
                    "currentSizeMB": round(float(period_memory.get("totalSizeMB") or 0), 1),
                    "deltaAvailable": False,
                },
            )
            result["knowledgePeriod"] = knowledge_period
            result["dataFreshness"]["periodAssets"] = {
                **(_projection_freshness(asset_projection) or {}),
                "memorySource": (
                    "foundation"
                    if asset_metrics.get("memoryStats") is not None and asset_metrics.get("knowledgePeriodMemoryCurrent") is not None
                    else "snapshot-missing"
                ),
            }
        else:
            result["knowledgePeriod"] = {
                "rag": rag_period,
                "memory": {"currentCount": 0, "currentSizeMB": 0, "deltaAvailable": False},
            }
            result["dataFreshness"]["periodAssets"] = _missing_projection_freshness(start_date, days)
    return result


def _prior_knowledge_snapshot(start_date) -> Dict[str, tuple]:
    """Find the last stored RAG/Memory snapshot before a report period."""
    result: Dict[str, tuple] = {}
    candidates = []
    root = _diary_root()
    language_profile = _pipeline_language_profile()
    for file in iter_diary_markdown_files(root):
        stamp = _narrative_filename_date(file.name, language_profile=language_profile)
        if not stamp:
            continue
        try:
            snapshot_date = datetime.strptime(stamp, "%y%m%d").date()
        except ValueError:
            continue
        if snapshot_date < start_date:
            candidates.append((snapshot_date, file))
    for snapshot_date, file in sorted(candidates, reverse=True):
        try:
            block = _extract_json_block(file.read_text(encoding="utf-8"))
        except OSError:
            continue
        if "rag" not in result and (block or {}).get("ragStats"):
            result["rag"] = (snapshot_date.strftime("%Y-%m-%d"), block["ragStats"])
        if "memory" not in result and (block or {}).get("memoryStats"):
            result["memory"] = (snapshot_date.strftime("%Y-%m-%d"), block["memoryStats"])
        if "rag" in result and "memory" in result:
            break
    return result


def _session_memory_stats() -> Dict[str, Any]:
    """Match the session-file metric persisted in daily memoryStats snapshots."""
    session_files = 0
    total_size = 0
    session_dir = _session_dir()
    if session_dir.exists():
        for root, _, files in os.walk(session_dir):
            for name in files:
                if not name.endswith(".jsonl"):
                    continue
                try:
                    total_size += os.path.getsize(os.path.join(root, name))
                    session_files += 1
                except OSError:
                    continue
    return {
        "sessionFiles": session_files,
        "totalSizeMB": round(total_size / (1024 * 1024), 2),
    }


def generate_weekly_report(days: int = 7, start: Optional[str] = None, include_assets: bool = False) -> Dict[str, Any]:
    """Return the dashboard weekly/monthly shape from Foundation projections."""
    days = max(1, min(int(days or 7), 62))
    if start:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
    else:
        start_date = datetime.now(resolve_timezone()).date() - timedelta(days=days - 1)
    date_list = _period_dates(start_date, days)

    requested_report_source = _report_read_source()
    asset_projection = None
    if include_assets:
        asset_projection = _foundation_period_asset_breakdown(start_date, days)
    summary_projection = _foundation_period_summary_projection(start_date, days)
    page_projection = _foundation_period_page_projection(start_date, days)
    snapshot_report = _foundation_snapshot_period_report(
        start_date,
        days,
        include_assets=include_assets,
        asset_projection=asset_projection,
        page_projection=page_projection,
        summary_projection=summary_projection,
    )
    if requested_report_source != "foundation":
        snapshot_report.setdefault("dataFreshness", {})["reportReadSource"] = {
            "source": "foundation",
            "retiredSourceRequested": requested_report_source,
            "status": "retired_source_ignored",
        }
    return snapshot_report


def _period_snapshot_delta(current: dict, snapshots: list, baseline: Optional[tuple], count_key: str, size_key: str) -> dict:
    result = {
        "currentCount": _safe_int(current.get(count_key)),
        "currentSizeMB": round(float(current.get(size_key) or 0), 1),
        "deltaAvailable": False,
    }
    if not snapshots or not baseline:
        return result
    first_date, first = baseline
    last_date, last = snapshots[-1]
    result.update({
        "deltaAvailable": True,
        "from": first_date,
        "to": last_date,
        "deltaCount": _safe_int(last.get(count_key)) - _safe_int(first.get(count_key)),
        "deltaSizeMB": round(float(last.get(size_key) or 0) - float(first.get(size_key) or 0), 1),
    })
    return result


def _merge_token_stats(note_stats: Dict, jsonl_usage: Dict) -> Dict[str, Any]:
    """合并备注解析的 tokenStats 与 JSONL 扫描的 tokenUsage，互为 fallback"""
    merged = dict(note_stats) if note_stats else {}
    if jsonl_usage:
        if not merged.get("cacheHitRate"):
            merged["cacheHitRate"] = jsonl_usage.get("cacheHitRate")
        if not merged.get("input"):
            merged["input"] = jsonl_usage.get("input")
        if not merged.get("output"):
            merged["output"] = jsonl_usage.get("output")
        if not merged.get("cache"):
            merged["cache"] = jsonl_usage.get("cacheRead")
        if not merged.get("total"):
            merged["total"] = jsonl_usage.get("total")
    if not merged:
        merged = _get_token_from_service(merged.get("date", ""))
    return merged


def _get_token_from_service(full_date: str) -> Dict[str, Any]:
    """从 tokens service 获取某日 token 统计（diary 无备注时的 fallback）"""
    try:
        from .tokens import parse_by_date
        week = parse_by_date(days=30)
        total = 0
        for key, val in week.items():
            if key.startswith(full_date + ":"):
                total += val
        if total > 0:
            return {"total": total, "source": "session_jsonl"}
    except Exception:
        pass
    return {}


def _scan_claude_code_for_date(full_date: str) -> Dict[int, int]:
    """扫描 Claude Code JSONL，返回按 HKT 小时分桶的 token 字典。"""
    from .tz import utc_ts_to_hkt
    from data_foundation.time import resolve_timezone

    target = datetime.strptime(full_date, "%Y-%m-%d").date()
    local_tz = resolve_timezone()
    hourly: Dict[int, int] = {h: 0 for h in range(24)}
    claude_dir = _external_tool_path("claudeCode", "projectsRoot")

    if not claude_dir.exists():
        return hourly

    for project_dir in claude_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for fname in project_dir.glob("*.jsonl"):
            try:
                with open(fname) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            d = json.loads(line)
                            if d.get("type") != "assistant":
                                continue
                            msg = d.get("message", {})
                            if msg.get("role") != "assistant":
                                continue
                            # Claude Code: timestamp at top level d.timestamp
                            ts = d.get("timestamp", "") or msg.get("timestamp", "")
                            if len(ts) < 19:
                                continue
                            hkt_date, hkt_hour = utc_ts_to_hkt(ts, tz=local_tz)
                            if hkt_date is None or hkt_date != target:
                                continue
                            u = msg.get("usage", {})
                            if u:
                                inp = u.get("input_tokens", 0) or u.get("input", 0) or 0
                                out = u.get("output_tokens", 0) or u.get("output", 0) or 0
                                cr = u.get("cache_read_input_tokens", 0) or u.get("cacheRead", 0) or 0
                                cw = u.get("cache_creation_input_tokens", 0) or u.get("cacheWrite", 0) or 0
                                hourly[hkt_hour] += inp + out + cr + cw
                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue
            except Exception:
                pass
    return hourly


def _scan_gemini_cli_for_date(full_date: str) -> Dict[int, int]:
    """扫描 Gemini CLI session JSON，返回按 HKT 小时分桶的 token 字典。"""
    from .tz import utc_ts_to_hkt
    from data_foundation.time import resolve_timezone

    target = datetime.strptime(full_date, "%Y-%m-%d").date()
    local_tz = resolve_timezone()
    hourly: Dict[int, int] = {h: 0 for h in range(24)}
    gemini_chats = _external_tool_path("geminiCli", "chatsRoot")

    if not gemini_chats.exists():
        return hourly

    for session_file in gemini_chats.glob("session-*.json"):
        try:
            with open(session_file) as f:
                data = json.load(f)
            for m in data.get("messages", []):
                # Gemini CLI: type == "gemini" (model response)
                if m.get("type") != "gemini":
                    continue
                ts = m.get("timestamp", "")
                if len(ts) < 19:
                    continue
                hkt_date, hkt_hour = utc_ts_to_hkt(ts, tz=local_tz)
                if hkt_date is None or hkt_date != target:
                    continue
                tokens = m.get("tokens", {})
                if tokens:
                    total = tokens.get("total", 0) or 0
                    if total == 0:
                        total = sum(tokens.get(k, 0) for k in ("input", "output", "cached", "thoughts", "tool"))
                    hourly[hkt_hour] += total
        except Exception:
            pass
    return hourly


def _scan_hermes_for_date(full_date: str) -> Dict[int, int]:
    """扫描 Hermes SQLite，返回按配置业务时区小时分桶的 token 字典。"""
    target = datetime.strptime(full_date, "%Y-%m-%d").date()
    hourly: Dict[int, int] = {h: 0 for h in range(24)}
    hermes_db = _external_tool_path("hermes", "stateDbPath")
    local_tz = resolve_timezone()

    if not hermes_db.exists():
        return hourly

    try:
        import sqlite3
        with closing(sqlite3.connect(str(hermes_db))) as conn:
            cursor = conn.cursor()
            # started_at is Unix seconds.
            cursor.execute(
                "SELECT started_at, input_tokens, output_tokens, cache_read_tokens FROM sessions WHERE input_tokens > 0 OR output_tokens > 0"
            )
            for row in cursor.fetchall():
                started_at, inp, out, cr = row
                if started_at is None:
                    continue
                try:
                    utc_dt = datetime.fromtimestamp(started_at, tz=timezone.utc)
                    local_dt = utc_dt.astimezone(local_tz)
                    local_date = business_date_for(utc_dt, tz=local_tz)
                    local_hour = local_dt.hour
                except (ValueError, OSError):
                    continue
                if local_date != target:
                    continue
                token_total = (inp or 0) + (out or 0) + (cr or 0)
                hourly[local_hour] += token_total
    except Exception:
        pass
    return hourly


def _scan_jsonl_for_date(full_date: str) -> Dict[str, Any]:
    """
    一次遍历所有 JSONL，同时计算 hourlyTokens、agentStats、sessionStats。

    时区逻辑：
    日界规则：HKT 04:00 ~ 次日 HKT 04:00
    使用共享的 utc_ts_to_hkt() 函数进行转换。
    """
    from .tz import utc_ts_to_hkt
    from data_foundation.time import resolve_timezone

    target = datetime.strptime(full_date, "%Y-%m-%d").date()
    local_tz = resolve_timezone()
    hourly: Dict[int, int] = {h: 0 for h in range(24)}
    agent_stats: Dict[str, Dict[str, Any]] = {}
    session_ids: set = set()
    message_count = 0
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0

    session_dir = _session_dir()
    if not session_dir.exists():
        return {"hourlyTokens": hourly, "agentStats": agent_stats, "sessionStats": {"sessions": 0, "messages": 0}}

    for agent_dir in session_dir.iterdir():
        sessions_dir = agent_dir / "sessions"
        if not sessions_dir.is_dir():
            continue
        agent_id = agent_dir.name
        agent_msg_count = 0
        agent_tokens = 0
        agent_last_ts = ""

        for fname in sessions_dir.iterdir():
            if ".jsonl" not in fname.name or "checkpoint" in fname.name:
                continue
            session_id = fname.name.split(".jsonl")[0]

            try:
                with open(fname) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            d = json.loads(line)
                            if d.get("type") != "message":
                                continue
                            msg = d.get("message", {})
                            ts = d.get("timestamp", "")
                            if len(ts) < 19:
                                continue

                            # UTC → HKT 转换（共享日界逻辑）
                            hkt_date, hkt_hour = utc_ts_to_hkt(ts, tz=local_tz)
                            if hkt_date is None or hkt_date != target:
                                continue

                            session_ids.add(session_id)
                            if ts > agent_last_ts:
                                agent_last_ts = ts

                            if msg.get("role") == "assistant":
                                u = msg.get("usage", {})
                                if u:
                                    inp = u.get("input", 0) or 0
                                    out = u.get("output", 0) or 0
                                    cr = u.get("cacheRead", 0) or 0
                                    cw = u.get("cacheWrite", 0) or 0
                                    token_total = inp + out + cr + cw
                                    hourly[hkt_hour] += token_total
                                    agent_tokens += token_total
                                    agent_msg_count += 1
                                    total_input += inp
                                    total_output += out
                                    total_cache_read += cr
                                    total_cache_write += cw
                            else:
                                message_count += 1
                                agent_msg_count += 1
                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue
            except Exception:
                pass

        if agent_msg_count > 0:
            agent_stats[agent_id] = {"messages": agent_msg_count, "tokens": agent_tokens, "lastActive": agent_last_ts[:16]}
    # ── 也扫描归档目录（日记已归档的 session）──
    # 跳过已在主 sessions 目录中存在的 session（去重）
    archive_date_dir = _diary_root() / "__diary_daily" / full_date
    if archive_date_dir.exists():
        for agent_dir in archive_date_dir.iterdir():
            if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
                continue
            agent_id = agent_dir.name
            agent_msg_count = 0
            agent_tokens = 0
            agent_last_ts = ""
            for fname in agent_dir.iterdir():
                if ".jsonl" not in fname.name or "checkpoint" in fname.name:
                    continue
                session_id = fname.name.split(".jsonl")[0]
                if session_id in session_ids:
                    continue
                try:
                    with open(fname) as f:
                        for line in f:
                            if not line.strip():
                                continue
                            try:
                                d = json.loads(line)
                                if d.get("type") != "message":
                                    continue
                                msg = d.get("message", {})
                                ts = d.get("timestamp", "")
                                if len(ts) < 19:
                                    continue
                                hkt_date, hkt_hour = utc_ts_to_hkt(ts, tz=local_tz)
                                if hkt_date is None or hkt_date != target:
                                    continue
                                session_ids.add(session_id)
                                if ts > agent_last_ts:
                                    agent_last_ts = ts
                                if msg.get("role") == "assistant":
                                    u = msg.get("usage", {})
                                    if u:
                                        inp = u.get("input", 0) or 0
                                        out = u.get("output", 0) or 0
                                        cr = u.get("cacheRead", 0) or 0
                                        cw = u.get("cacheWrite", 0) or 0
                                        token_total = inp + out + cr + cw
                                        hourly[hkt_hour] += token_total
                                        agent_tokens += token_total
                                        agent_msg_count += 1
                                        total_input += inp
                                        total_output += out
                                        total_cache_read += cr
                                        total_cache_write += cw
                                else:
                                    message_count += 1
                                    agent_msg_count += 1
                            except (json.JSONDecodeError, KeyError, ValueError):
                                continue
                except Exception:
                    pass
            if agent_msg_count > 0:
                if agent_id in agent_stats:
                    agent_stats[agent_id]["messages"] += agent_msg_count
                    agent_stats[agent_id]["tokens"] += agent_tokens
                    if agent_last_ts > agent_stats[agent_id].get("lastActive", ""):
                        agent_stats[agent_id]["lastActive"] = agent_last_ts[:16]
                else:
                    agent_stats[agent_id] = {"messages": agent_msg_count, "tokens": agent_tokens, "lastActive": agent_last_ts[:16]}

    # ── 合并多源 token 扫描 ──
    for h, v in _scan_claude_code_for_date(full_date).items():
        hourly[h] += v
    for h, v in _scan_gemini_cli_for_date(full_date).items():
        hourly[h] += v
    for h, v in _scan_hermes_for_date(full_date).items():
        hourly[h] += v

    cache_hit_rate = 0.0
    if total_input + total_cache_read > 0:
        cache_hit_rate = round(total_cache_read / (total_input + total_cache_read) * 100, 1)
    return {
        "hourlyTokens": hourly,
        "agentStats": agent_stats,
        "sessionStats": {"sessions": len(session_ids), "messages": message_count},
        "tokenUsage": {
            "input": total_input,
            "output": total_output,
            "cacheRead": total_cache_read,
            "cacheWrite": total_cache_write,
            "total": total_input + total_output + total_cache_read + total_cache_write,
            "cacheHitRate": cache_hit_rate,
        },
    }


def _get_jsonl_stats(full_date: str) -> Dict[str, Any]:
    """带缓存的 JSONL 统计（进程生命周期内有效）"""
    if full_date in _JSONL_CACHE:
        return _JSONL_CACHE[full_date]

    result = _scan_jsonl_for_date(full_date)
    _JSONL_CACHE[full_date] = result
    return result

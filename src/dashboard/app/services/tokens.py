#!/usr/bin/env python3
"""Token statistics service for Open Nova external-tool usage streams."""
import json
import os
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Any
from data_foundation.settings import default_external_tool_path, external_tool_path
from data_foundation.time import parse_timestamp, resolve_timezone
from data_foundation.token_semantics import (
    authoritative_semantics,
    cache_hit_rate,
    legacy_operational_total,
    prompt_total,
    protocol_total,
)

AGENTS_DIR = default_external_tool_path("openclaw", "agentsRoot")
_DEFAULT_AGENTS_DIR = AGENTS_DIR

LIVE_TOKEN_SEMANTICS = authoritative_semantics(
    scope="OpenClaw realtime operational status",
    day_boundary="configured business timezone 04:00-03:59 via app.services.tz",
    live=True,
)


def _is_session_file(fname: str) -> bool:
    """匹配所有可能包含对话数据的文件类型。
    *.jsonl              — 活跃 session
    *.jsonl.reset.*      — 压缩后备份
    *.jsonl.deleted.*    — 已删除 session 的备份
    排除: *.checkpoint.*  — 与 reset/jsonl 100% 重叠
    排除: *.jsonl.lock    — 文件锁
    排除: sessions.json   — session 索引
    """
    return ('.jsonl' in fname and
            '.checkpoint' not in fname and
            not fname.endswith('.lock') and
            fname != 'sessions.json')


def _agents_dir() -> Path:
    if AGENTS_DIR != _DEFAULT_AGENTS_DIR:
        return AGENTS_DIR
    try:
        return external_tool_path("openclaw", "agentsRoot")
    except Exception:
        return _DEFAULT_AGENTS_DIR


def _utc_to_hkt(utc_ts: str) -> str:
    """将 UTC ISO timestamp 转换为配置时区日期字符串（格式 YYYY-MM-DD）。"""
    parsed = parse_timestamp(utc_ts)
    return parsed.astimezone(resolve_timezone()).strftime("%Y-%m-%d") if parsed else ""


def parse_sessions(days: int = 1) -> Dict[str, Any]:
    """
    解析 session JSONL，返回 token 统计。
    按 HKT 天过滤：days=1 = 今日（从 HKT 00:00 到当前），
    days=2 = 今天+昨天，等等。
    """
    from .tz import utc_ts_to_hkt, hkt_today, hkt_cutoff_utc

    cutoff_str = hkt_cutoff_utc(days)
    today_str = hkt_today().isoformat()

    stats = defaultdict(
        lambda: {
            "input": 0,
            "output": 0,
            "cacheRead": 0,
            "cacheWrite": 0,
            "total": 0,
            "promptTotal": 0,
            "legacyOperationalTotal": 0,
            "count": 0,
        }
    )

    agents_dir = _agents_dir()
    if not agents_dir.exists():
        return {}
    for agent_id in os.listdir(agents_dir):
        sessions_dir = agents_dir / agent_id / "sessions"
        if not sessions_dir.is_dir():
            continue
        for fname in os.listdir(sessions_dir):
            if not _is_session_file(fname):
                continue
            fpath = sessions_dir / fname
            nonblank_lines = 0
            valid_json_lines = 0
            with open(fpath) as f:
                for line in f:
                    if not line.strip():
                        continue
                    nonblank_lines += 1
                    try:
                        d = json.loads(line)
                        valid_json_lines += 1
                        if d.get("type") != "message":
                            continue
                        msg = d.get("message", {})
                        if msg.get("role") != "assistant":
                            continue
                        u = msg.get("usage", {})
                        if not u:
                            continue
                        ts = d.get("timestamp", "")
                        if len(ts) < 19 or ts[:19] < cutoff_str:
                            continue
                        hkt_date, _ = utc_ts_to_hkt(ts)
                        if hkt_date is None:
                            continue
                        if days == 1 and str(hkt_date) != today_str:
                            continue
                        inp = u.get("input", 0) or 0
                        out = u.get("output", 0) or 0
                        cr = u.get("cacheRead", 0) or 0
                        cw = u.get("cacheWrite", 0) or 0
                        stats[agent_id]["input"] += inp
                        stats[agent_id]["output"] += out
                        stats[agent_id]["cacheRead"] += cr
                        stats[agent_id]["cacheWrite"] += cw
                        stats[agent_id]["total"] += protocol_total({"input": inp, "output": out, "cacheRead": cr})
                        stats[agent_id]["promptTotal"] += prompt_total({"input": inp, "cacheRead": cr, "cacheWrite": cw})
                        stats[agent_id]["legacyOperationalTotal"] += legacy_operational_total({"input": inp, "output": out, "cacheRead": cr, "cacheWrite": cw})
                        stats[agent_id]["count"] += 1
                    except (json.JSONDecodeError, KeyError):
                        continue
            if nonblank_lines and not valid_json_lines:
                raise ValueError("session file contains no valid JSON records")

    return dict(stats)


def parse_by_date(days: int = 7) -> Dict[str, Dict[str, int]]:
    """
    按 HKT 日期分组返回每日 total_tokens。
    UTC timestamp → HKT date → group by HKT date。
    """
    from .tz import utc_ts_to_hkt, hkt_cutoff_utc

    result = defaultdict(lambda: defaultdict(int))
    cutoff_str = hkt_cutoff_utc(days)

    agents_dir = _agents_dir()
    if not agents_dir.exists():
        return {}
    for agent_id in os.listdir(agents_dir):
        sessions_dir = agents_dir / agent_id / "sessions"
        if not sessions_dir.is_dir():
            continue
        for fname in os.listdir(sessions_dir):
            if not _is_session_file(fname):
                continue
            fpath = sessions_dir / fname
            nonblank_lines = 0
            valid_json_lines = 0
            with open(fpath) as f:
                for line in f:
                    if not line.strip():
                        continue
                    nonblank_lines += 1
                    try:
                        d = json.loads(line)
                        valid_json_lines += 1
                        if d.get("type") != "message":
                            continue
                        msg = d.get("message", {})
                        if msg.get("role") != "assistant":
                            continue
                        u = msg.get("usage", {})
                        if not u:
                            continue
                        ts = d.get("timestamp", "")
                        if len(ts) < 19 or ts[:19] < cutoff_str:
                            continue
                        hkt_date, _ = utc_ts_to_hkt(ts)
                        if not hkt_date:
                            continue
                        inp = u.get("input", 0) or 0
                        cr = u.get("cacheRead", 0) or 0
                        out = u.get("output", 0) or 0
                        total = protocol_total({"input": inp, "output": out, "cacheRead": cr})
                        result[hkt_date][agent_id] += total
                    except (json.JSONDecodeError, KeyError):
                        continue
            if nonblank_lines and not valid_json_lines:
                raise ValueError("session file contains no valid JSON records")

    # 转成 flat: {(date, agent): total}
    flat = {}
    for date, agents in sorted(result.items()):
        for agent, total in agents.items():
            flat[f"{date}:{agent}"] = total
    return flat


_CACHE = {}
_LAST_MTIME = 0.0

def _get_max_mtime() -> float:
    max_mtime = 0.0
    agents_dir = _agents_dir()
    if not agents_dir.exists():
        return 0.0
    for agent_id in os.listdir(agents_dir):
        sessions_dir = agents_dir / agent_id / "sessions"
        if not sessions_dir.is_dir():
            continue
        for fname in os.listdir(sessions_dir):
            if not _is_session_file(fname):
                continue
            mtime = (sessions_dir / fname).stat().st_mtime
            if mtime > max_mtime:
                max_mtime = mtime
    return max_mtime

def compute_summary() -> Dict[str, Any]:
    """综合统计：今日(HKT) + 7天趋势(HKT) + 缓存命中率 (带mtime缓存)"""
    global _LAST_MTIME, _CACHE
    current_mtime = _get_max_mtime()

    if current_mtime > 0 and current_mtime <= _LAST_MTIME and "data" in _CACHE:
        # Check if the cached date matches today to avoid midnight staleness
        cached_date = _CACHE["data"].get("updatedAt", "")[:10]
        from .tz import hkt_today
        today_str = hkt_today().isoformat()
        if cached_date == today_str:
            return _CACHE["data"]

    today = parse_sessions(days=1)
    week = parse_by_date(days=7)

    # 汇总今日
    total_input = sum(v["input"] for v in today.values())
    total_output = sum(v["output"] for v in today.values())
    total_cache_read = sum(v["cacheRead"] for v in today.values())
    total_cache_write = sum(v["cacheWrite"] for v in today.values())
    total_count = sum(v["count"] for v in today.values())

    cache_rate = cache_hit_rate({"input": total_input, "cacheRead": total_cache_read})
    total_tokens = protocol_total({"input": total_input, "output": total_output, "cacheRead": total_cache_read})

    res = {
        "today": today,
        "week": week,
        "semantics": dict(LIVE_TOKEN_SEMANTICS),
        "summary": {
            "input": total_input,
            "output": total_output,
            "cacheRead": total_cache_read,
            "cacheWrite": total_cache_write,
            "total": total_tokens,
            "promptTotal": prompt_total({"input": total_input, "cacheRead": total_cache_read, "cacheWrite": total_cache_write}),
            "legacyOperationalTotal": legacy_operational_total({"input": total_input, "output": total_output, "cacheRead": total_cache_read, "cacheWrite": total_cache_write}),
            "cacheHitRate": cache_rate,
            "count": total_count,
        },
        "updatedAt": datetime.now(resolve_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
    }
    _CACHE["data"] = res
    _LAST_MTIME = current_mtime
    return res


def get_session_stats(date: str) -> Dict[str, int]:
    """
    返回指定 HKT 日期的 session 数量和消息总数。
    date 参数应为 HKT 日期（YYYY-MM-DD）。
    """
    session_ids = set()
    message_count = 0

    agents_dir = _agents_dir()
    if not agents_dir.exists():
        return {"sessions": 0, "messages": 0}
    for agent_id in os.listdir(agents_dir):
        sessions_dir = agents_dir / agent_id / "sessions"
        if not sessions_dir.is_dir():
            continue
        for fname in os.listdir(sessions_dir):
            if not _is_session_file(fname):
                continue
            fpath = sessions_dir / fname
            session_id = fname.replace(".jsonl", "").split(".checkpoint.")[0]
            try:
                with open(fpath) as f:
                    for line in f:
                        try:
                            d = json.loads(line)
                            if d.get("type") != "message":
                                continue
                            ts = d.get("timestamp", "")
                            hkt_date = _utc_to_hkt(ts)
                            if hkt_date == date:
                                session_ids.add(session_id)
                                message_count += 1
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue

    return {"sessions": len(session_ids), "messages": message_count}

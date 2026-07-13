#!/usr/bin/env python3
"""Agent 状态服务 — 扫描 agents/ sessions 目录获取活跃状态"""
import json
import os
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from data_foundation.settings import default_external_tool_path, external_tool_path
from data_foundation.time import resolve_timezone

AGENTS_DIR: Path | None = None


def _agents_dir() -> Path:
    if AGENTS_DIR is not None:
        return AGENTS_DIR
    try:
        return external_tool_path("openclaw", "agentsRoot")
    except Exception:
        return default_external_tool_path("openclaw", "agentsRoot")


def get_agent_list() -> list:
    """返回所有 agent 的基本信息"""
    agents = []
    agents_dir = _agents_dir()
    if not agents_dir.exists():
        return agents

    for agent_id in os.listdir(agents_dir):
        sessions_dir = agents_dir / agent_id / "sessions"
        if not sessions_dir.is_dir():
            continue

        sessions = [f for f in os.listdir(sessions_dir) if f.endswith(".jsonl")]
        if not sessions:
            continue

        # 找最新 session
        latest_mtime = 0
        latest_session = None
        session_count = 0
        total_messages = 0

        for sname in sessions:
            spath = sessions_dir / sname
            mtime = spath.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_session = sname
            session_count += 1
            # 统计消息数
            try:
                with open(spath) as f:
                    msgs = sum(1 for line in f if json.loads(line).get("type") == "message")
                total_messages += msgs
            except Exception:
                pass

        timezone = resolve_timezone()
        last_active = datetime.fromtimestamp(latest_mtime, tz=timezone).strftime("%Y-%m-%d %H:%M") if latest_mtime else None
        minutes_ago = int((datetime.now(timezone).timestamp() - latest_mtime) / 60) if latest_mtime else None

        agents.append({
            "id": agent_id,
            "sessionCount": session_count,
            "totalMessages": total_messages,
            "lastActive": last_active,
            "minutesAgo": minutes_ago,
            "online": minutes_ago < 30 if minutes_ago is not None else False,
        })

    return sorted(agents, key=lambda x: x.get("lastActive") or "")


_CACHE = {}
_LAST_MTIME = 0.0

def _get_max_mtime() -> float:
    max_mtime = 0.0
    agents_dir = _agents_dir()
    if not agents_dir.exists():
        return 0.0
    try:
        for agent_id in os.listdir(agents_dir):
            sessions_dir = agents_dir / agent_id / "sessions"
            if not sessions_dir.is_dir():
                continue
            for fname in os.listdir(sessions_dir):
                if not fname.endswith(".jsonl"):
                    continue
                fpath = sessions_dir / fname
                try:
                    mtime = fpath.stat().st_mtime
                    if mtime > max_mtime:
                        max_mtime = mtime
                except OSError:
                    pass
    except Exception:
        pass
    return max_mtime

def get_summary() -> dict:
    global _LAST_MTIME, _CACHE
    current_mtime = _get_max_mtime()

    if current_mtime > 0 and current_mtime <= _LAST_MTIME and "data" in _CACHE:
        return _CACHE["data"]

    agents = get_agent_list()
    total = len(agents)
    online = sum(1 for a in agents if a["online"])
    res = {
        "total": total,
        "online": online,
        "agents": agents,
        "updatedAt": datetime.now(resolve_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
    }
    _CACHE["data"] = res
    _LAST_MTIME = current_mtime
    return res

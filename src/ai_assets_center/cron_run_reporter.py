import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import config

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from data_foundation.settings import default_external_tool_path, external_tool_path
from data_foundation.time import business_today, business_window, resolve_timezone

CRON_RUNS_DIR = default_external_tool_path("openclaw", "cronRunsRoot")
_DEFAULT_CRON_RUNS_DIR = CRON_RUNS_DIR


def _cron_runs_dir() -> Path:
    if CRON_RUNS_DIR != _DEFAULT_CRON_RUNS_DIR:
        return CRON_RUNS_DIR
    try:
        return external_tool_path("openclaw", "cronRunsRoot")
    except Exception:
        return _DEFAULT_CRON_RUNS_DIR

def get_hkt_dt(ts_ms):
    """Unix ms 转配置业务时区 datetime。"""
    dt_utc = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt_utc.astimezone(resolve_timezone())

def generate_cron_report(target_date):
    print(f"🔍 Auditing physical cron runs for {target_date}...")

    # 窗口定义
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    start_dt, end_dt = business_window(target)

    runs = []
    cron_runs_dir = _cron_runs_dir()
    if not cron_runs_dir.exists():
        return "无"

    for fpath in _iter_cron_run_files(cron_runs_dir):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    d = json.loads(line)
                    # 我们只统计 finished 状态
                    if d.get("action") == "finished":
                        ts_ms = d.get("ts")
                        local_dt = get_hkt_dt(ts_ms)

                        # 时间窗口校验
                        if start_dt.timestamp() <= local_dt.timestamp() < end_dt.timestamp():
                            job_id = d.get("jobId", "unknown")
                            status = d.get("status", "unknown")
                            duration = d.get("durationMs", 0) / 1000.0
                            summary = d.get("summary", "").strip()

                            # 提取核心结论 (第一行或前50字)
                            short_note = summary.split('\n')[0][:50]
                            if status == "error":
                                short_note = f"❌ {d.get('error', 'Unknown error')}"
                            elif not short_note:
                                short_note = "执行完成 (无摘要)"

                            runs.append({
                                "time": local_dt.strftime("%H:%M"),
                                "job": job_id[:8], # 取前8位作为 ID
                                "status": "✅ OK" if status == "ok" else f"❌ ERR",
                                "duration": f"{duration:.1f}s",
                                "note": short_note
                            })
        except: continue

    if not runs:
        return "无定时任务执行记录。"

    # 按时间排序
    runs.sort(key=lambda x: x['time'])

    # 构建表格
    table = "| 时间 | 任务ID | 状态 | 耗时 | 执行结论 |\n"
    table += "| :--- | :--- | :--- | :--- | :--- |\n"
    for r in runs:
        table += f"| {r['time']} | `{r['job']}` | {r['status']} | {r['duration']} | {r['note']} |\n"

    return table


def _iter_cron_run_files(root):
    seen = set()
    for pattern in ("*.jsonl", "*.jsonl.migrated"):
        for path in sorted(root.glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            yield path

if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else business_today().isoformat()
    print(generate_cron_report(d))

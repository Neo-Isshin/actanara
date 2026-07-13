#!/usr/bin/env python3
"""Parse session JSONL files to extract token usage statistics - JSON output for Dashboard."""
import json, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_foundation.settings import external_tool_path


# 文件匹配规则：覆盖所有可能包含对话数据的文件类型
# *.jsonl              — 活跃 session
# *.jsonl.reset.*      — 压缩后备份（原始数据完整保留）
# *.jsonl.deleted.*    — 已删除 session 的备份（数据不存在于其他文件）
# 不包含 *.checkpoint.*  — 与 reset/jsonl 100% 重叠，扫描会重复计算
def is_session_file(fname):
    """匹配所有可能包含对话数据的文件类型。
    *.jsonl              — 活跃 session
    *.jsonl.reset.*      — 压缩后备份（原始数据完整保留）
    *.jsonl.deleted.*    — 已删除 session 的备份（数据不存在于其他文件）
    排除: *.checkpoint.*  — 与 reset/jsonl 100% 重叠
    排除: *.jsonl.lock    — 文件锁
    排除: sessions.json   — session 索引
    """
    return ('.jsonl' in fname and
            '.checkpoint' not in fname and
            not fname.endswith('.lock') and
            fname != 'sessions.json')


def parse_sessions(agents_dir, days=1):
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff.strftime('%Y-%m-%d')

    stats = defaultdict(lambda: {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0, 'total': 0, 'count': 0})

    for agent_id in os.listdir(agents_dir):
        sessions_dir = os.path.join(agents_dir, agent_id, 'sessions')
        if not os.path.isdir(sessions_dir):
            continue
        for fname in os.listdir(sessions_dir):
            if not is_session_file(fname):
                continue
            fpath = os.path.join(sessions_dir, fname)
            try:
                with open(fpath) as f:
                    for line in f:
                        try:
                            d = json.loads(line)
                            if d.get('type') != 'message':
                                continue
                            msg = d.get('message', {})
                            if msg.get('role') != 'assistant':
                                continue
                            u = msg.get('usage', {})
                            if not u:
                                continue
                            ts = d.get('timestamp', '')[:10]
                            if ts < cutoff_str:
                                continue
                            inp = u.get('input', 0) or 0
                            out = u.get('output', 0) or 0
                            cr = u.get('cacheRead', 0) or 0
                            cw = u.get('cacheWrite', 0) or 0
                            prompt_tokens = inp + cr + cw
                            stats[agent_id]['input'] += inp
                            stats[agent_id]['output'] += out
                            stats[agent_id]['cacheRead'] += cr
                            stats[agent_id]['cacheWrite'] += cw
                            stats[agent_id]['total'] += prompt_tokens + out
                            stats[agent_id]['count'] += 1
                        except (json.JSONDecodeError, KeyError):
                            continue
            except Exception as e:
                pass

    return stats


def parse_by_date(agents_dir, days=7):
    """Parse sessions grouped by date."""
    result = defaultdict(lambda: defaultdict(int))
    today = datetime.now().strftime('%Y-%m-%d')
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    for agent_id in os.listdir(agents_dir):
        sessions_dir = os.path.join(agents_dir, agent_id, 'sessions')
        if not os.path.isdir(sessions_dir):
            continue
        for fname in os.listdir(sessions_dir):
            if not is_session_file(fname):
                continue
            fpath = os.path.join(sessions_dir, fname)
            try:
                with open(fpath) as f:
                    for line in f:
                        try:
                            d = json.loads(line)
                            if d.get('type') != 'message':
                                continue
                            msg = d.get('message', {})
                            if msg.get('role') != 'assistant':
                                continue
                            u = msg.get('usage', {})
                            if not u:
                                continue
                            ts = d.get('timestamp', '')[:10]
                            if ts < cutoff or ts > today:
                                continue
                            inp = u.get('input', 0) or 0
                            cr = u.get('cacheRead', 0) or 0
                            cw = u.get('cacheWrite', 0) or 0
                            result[ts]['total'] += inp + cr + cw
                        except:
                            continue
            except:
                pass

    return result


if __name__ == '__main__':
    agents_dir = str(external_tool_path("openclaw", "agentsRoot"))
    mode = sys.argv[1] if len(sys.argv) > 1 else 'summary'

    if mode == 'summary':
        stats = parse_sessions(agents_dir, 1)
        total_input = sum(v['input'] for v in stats.values())
        total_cr = sum(v['cacheRead'] for v in stats.values())
        total_cw = sum(v['cacheWrite'] for v in stats.values())
        total_all = sum(v['total'] for v in stats.values())

        result = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'period': '24h',
            'total': total_all,
            'input': total_input,
            'cacheRead': total_cr,
            'cacheWrite': total_cw,
            'cacheHitRate': round(total_cr / (total_input + total_cr) * 100, 1) if total_input + total_cr > 0 else 0,
            'byAgent': {k: v for k, v in sorted(stats.items(), key=lambda x: -x[1]['total'])}
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif mode == 'daily':
        result = parse_by_date(agents_dir, 7)
        dates = sorted(result.keys())
        print(json.dumps({'dates': dates, 'byDate': {k: dict(v) for k, v in result.items()}}, indent=2, ensure_ascii=False))

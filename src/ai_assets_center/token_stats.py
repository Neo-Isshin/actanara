import json, os
from collections import defaultdict
from pathlib import Path
import sys

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_foundation.settings import external_tool_path

agents_dir = str(external_tool_path("openclaw", "agentsRoot"))
today_stats = defaultdict(lambda: {'input': 0, 'cacheRead': 0, 'count': 0, 'promptTokens': 0})
recent_stats = defaultdict(lambda: {'promptTokens': 0, 'input': 0, 'cacheRead': 0})

for agent_id in os.listdir(agents_dir):
    sessions_dir = os.path.join(agents_dir, agent_id, 'sessions')
    if not os.path.isdir(sessions_dir): continue
    for fname in os.listdir(sessions_dir):
        if not fname.endswith('.jsonl'): continue
        fpath = os.path.join(sessions_dir, fname)
        try:
            with open(fpath) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if d.get('type') != 'message': continue
                        msg = d.get('message', {})
                        if msg.get('role') != 'assistant': continue
                        u = msg.get('usage', {})
                        if not u: continue
                        ts = d.get('timestamp', '')[:10]
                        inp = u.get('input', 0) or 0
                        cr = u.get('cacheRead', 0) or 0
                        cw = u.get('cacheWrite', 0) or 0
                        pt = inp + cr + cw
                        if ts == '2026-04-10':
                            today_stats[agent_id]['input'] += inp
                            today_stats[agent_id]['cacheRead'] += cr
                            today_stats[agent_id]['promptTokens'] += pt
                            today_stats[agent_id]['count'] += 1
                        if ts >= '2026-04-04' and ts <= '2026-04-10':
                            recent_stats[ts]['promptTokens'] += pt
                            recent_stats[ts]['input'] += inp
                            recent_stats[ts]['cacheRead'] += cr
                    except: continue
        except: pass

total = sum(v['promptTokens'] for v in today_stats.values())
total_inp = sum(v['input'] for v in today_stats.values())
total_cr = sum(v['cacheRead'] for v in today_stats.values())
rate = round(total_cr/(total_inp+total_cr)*100, 1) if total_inp+total_cr > 0 else 0
print(f'TODAY_TOTAL={total}')
print(f'TODAY_RATE={rate}%')
print('BY_AGENT:')
for agent, v in sorted(today_stats.items(), key=lambda x: -x[1]['promptTokens']):
    if v['promptTokens'] > 0:
        r = round(v['cacheRead']/(v['input']+v['cacheRead'])*100, 1) if v['input']+v['cacheRead'] > 0 else 0
        print(f'  {agent}:{v["promptTokens"]}:{v["input"]}:{v["count"]}:{r}')

print('LAST_7_DAYS:')
for d in sorted(recent_stats.keys()):
    v = recent_stats[d]
    r = round(v['cacheRead']/(v['input']+v['cacheRead'])*100, 1) if v['input']+v['cacheRead'] > 0 else 0
    print(f'{d}:{v["promptTokens"]}:{r}')

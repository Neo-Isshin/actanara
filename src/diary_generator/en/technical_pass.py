#!/usr/bin/env python3
"""English technical diary pass placeholder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EN_DIR = Path(__file__).resolve().parent
SRC_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = Path(__file__).resolve().parents[3]
if str(EN_DIR) not in sys.path:
    sys.path.insert(0, str(EN_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from _not_enabled import main as contract_main
from data_foundation.diary_paths import diary_technical_report_path
from data_foundation.nova_task import render_task_graph_context
from data_foundation.paths import load_paths
from data_foundation.settings import is_nova_task_enabled
from data_foundation.time import business_today
from technical_payload import generate_from_entries


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="English technical diary pass.")
    parser.add_argument("date", nargs="?", help="Business date, YYYY-MM-DD.")
    parser.add_argument("--contract", action="store_true", help="Print the pass contract and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print the pass contract skeleton without generating artifacts.")
    parser.add_argument("--fixture-json", type=Path, help="Run the English technical LLM payload against a fixture archive.")
    return parser


def cli(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.contract or args.dry_run:
        passthrough = []
        if args.contract:
            passthrough.append("--contract")
        if args.dry_run:
            passthrough.append("--dry-run")
        if args.date:
            passthrough.append(args.date)
        return contract_main("technical", passthrough)
    if args.fixture_json is None:
        target_date = args.date or business_today().isoformat()
        out_file = write_technical_report(target_date)
        print("✅ English Technical Pass Complete: " + str(out_file))
        return 0
    from technical_payload import load_fixture

    entries_by_source, task_graph_context = load_fixture(args.fixture_json)
    payload = generate_from_entries(args.date or "", entries_by_source, task_graph_context)
    payload["businessDate"] = args.date
    payload["fixture"] = str(args.fixture_json)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def load_agent_entries(agent_dir: Path) -> list[dict]:
    entries: list[dict] = []
    for jsonl_path in sorted(agent_dir.glob("*.jsonl")):
        with jsonl_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    entries.append(payload)
    entries.sort(key=lambda item: str(item.get("time") or ""))
    return entries


def load_unified_source_entries(date_str: str, diary_root: Path | None = None) -> dict[str, list[dict]]:
    root = diary_root or load_paths().diary_dir
    base_dir = root / "__diary_daily" / date_str / "_filtered"
    if not base_dir.exists():
        return {}
    source_entries: dict[str, list[dict]] = {}
    for source_dir in sorted(path for path in base_dir.iterdir() if path.is_dir()):
        normalized = []
        for entry in load_agent_entries(source_dir):
            copied = dict(entry)
            copied["source"] = source_dir.name
            normalized.append(copied)
        if normalized:
            source_entries[source_dir.name] = normalized
    return source_entries


def load_task_graph_context() -> str:
    try:
        paths = load_paths()
        if not is_nova_task_enabled(paths):
            return "Nova-Task v2 active graph disabled by settings."
        return render_task_graph_context(paths)
    except Exception:
        return "Nova-Task v2 active graph unavailable."


def write_technical_report(date_str: str, diary_root: Path | None = None) -> Path:
    root = diary_root or load_paths().diary_dir
    result = generate_from_entries(date_str, load_unified_source_entries(date_str, root), load_task_graph_context())
    markdown = str(result.get("markdown") or "").strip()
    if not markdown:
        raise RuntimeError("English technical generation returned empty markdown.")
    out_file = diary_technical_report_path(root, date_str, language_profile="en")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return out_file


if __name__ == "__main__":
    raise SystemExit(cli())

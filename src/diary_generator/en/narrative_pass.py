#!/usr/bin/env python3
"""English narrative diary pass placeholder."""

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
from data_foundation.diary_paths import diary_narrative_report_path, diary_no_activity_report_path
from data_foundation.paths import load_paths
from data_foundation.time import business_today
from data_foundation.weather import fetch_weather_for_date
from narrative_payload import generate_from_entries


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="English narrative diary pass.")
    parser.add_argument("date", nargs="?", help="Business date, YYYY-MM-DD.")
    parser.add_argument("--contract", action="store_true", help="Print the pass contract and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print the pass contract skeleton without generating artifacts.")
    parser.add_argument("--fixture-json", type=Path, help="Run the English LLM payload against a fixture archive.")
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
        return contract_main("narrative", passthrough)
    if args.fixture_json is None:
        target_date = args.date or business_today().isoformat()
        out_file = write_narrative_report(target_date)
        print("✅ English Narrative Pass Complete: " + str(out_file))
        return 0
    from narrative_payload import load_fixture

    payload = generate_from_entries(load_fixture(args.fixture_json))
    payload["businessDate"] = args.date
    payload["fixture"] = str(args.fixture_json)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def load_filtered_entries(date_str: str, diary_root: Path | None = None) -> dict[str, list[dict]]:
    root = diary_root or load_paths().diary_dir
    base_dir = root / "__diary_daily" / date_str / "_filtered"
    all_entries: dict[str, list[dict]] = {}
    if not base_dir.exists():
        return all_entries
    for agent_dir in sorted(path for path in base_dir.iterdir() if path.is_dir()):
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
        if entries:
            all_entries[agent_dir.name] = entries
    return all_entries


def _ensure_weather_section(date_str: str, markdown: str) -> str:
    if "## Weather" in markdown or "## 天气" in markdown:
        return markdown
    lines = markdown.splitlines()
    insert_at = 1 if lines and lines[0].startswith("# ") else 0
    lines[insert_at:insert_at] = ["", "## Weather", fetch_weather_for_date(date_str)]
    return "\n".join(lines).strip()


def _blank_day_markdown(date_str: str) -> str:
    weather = fetch_weather_for_date(date_str)
    return "\n".join(
        [
            f"# {date_str} Diary",
            "",
            "## Weather",
            weather,
            "",
            "## Daily Overview",
            "No activity today.",
            "",
            "## Scheduled Jobs",
            "None",
            "",
            "```json",
            json.dumps({"date": date_str, "activityState": "empty", "metrics": {}, "cronTasks": []}, ensure_ascii=False),
            "```",
        ]
    )


def write_narrative_report(date_str: str, diary_root: Path | None = None) -> Path:
    root = diary_root or load_paths().diary_dir
    entries = load_filtered_entries(date_str, root)
    if entries:
        result = generate_from_entries(entries)
        markdown = str(result.get("markdown") or "").strip()
        if not markdown:
            raise RuntimeError("English narrative generation returned empty markdown.")
        markdown = _ensure_weather_section(date_str, markdown)
        out_file = diary_narrative_report_path(root, date_str, language_profile="en")
    else:
        markdown = _blank_day_markdown(date_str)
        out_file = diary_no_activity_report_path(root, date_str, language_profile="en")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return out_file


def write_blank_day_report(date_str: str, diary_root: Path | None = None) -> Path:
    root = diary_root or load_paths().diary_dir
    out_file = diary_no_activity_report_path(root, date_str, language_profile="en")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(_blank_day_markdown(date_str).rstrip() + "\n", encoding="utf-8")
    return out_file


if __name__ == "__main__":
    raise SystemExit(cli())

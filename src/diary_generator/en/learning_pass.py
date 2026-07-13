#!/usr/bin/env python3
"""English learning diary pass placeholder."""

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
from data_foundation.diary_paths import diary_learning_report_path, diary_narrative_report_path, diary_report_paths
from data_foundation.paths import load_paths
from data_foundation.time import business_today
from learning_payload import generate_from_summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="English learning diary pass.")
    parser.add_argument("date", nargs="?", help="Business date, YYYY-MM-DD.")
    parser.add_argument("--contract", action="store_true", help="Print the pass contract and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print the pass contract skeleton without generating artifacts.")
    parser.add_argument("--fixture-json", type=Path, help="Run the English learning LLM payload against a fixture summary.")
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
        return contract_main("learning", passthrough)
    if args.fixture_json is None:
        target_date = args.date or business_today().isoformat()
        out_file = write_learning_report(target_date)
        print("✅ English Learning Pass Complete: " + str(out_file))
        return 0
    from learning_payload import load_fixture

    fixture_date, summary = load_fixture(args.fixture_json)
    date_str = args.date or fixture_date
    payload = generate_from_summary(date_str, summary)
    payload["businessDate"] = date_str
    payload["fixture"] = str(args.fixture_json)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def narrative_input_path(date_str: str, diary_root: Path | None = None) -> Path:
    root = diary_root or load_paths().diary_dir
    existing = diary_report_paths(root, date_str, "narrative", language_profile="en")
    return existing[0] if existing else diary_narrative_report_path(root, date_str, language_profile="en")


def write_learning_report(date_str: str, diary_root: Path | None = None) -> Path:
    root = diary_root or load_paths().diary_dir
    narrative_path = narrative_input_path(date_str, root)
    if not narrative_path.exists():
        raise RuntimeError(f"English narrative diary missing for learning pass: {narrative_path}")
    result = generate_from_summary(date_str, narrative_path.read_text(encoding="utf-8"))
    markdown = str(result.get("markdown") or "").strip()
    if not markdown:
        raise RuntimeError("English learning generation returned empty markdown.")
    out_file = diary_learning_report_path(root, date_str, language_profile="en")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return out_file


if __name__ == "__main__":
    raise SystemExit(cli())

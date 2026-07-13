#!/usr/bin/env python3
"""Run standalone Nova-Task work-graph reconciliation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from data_foundation.nova_task_work_graph_reconciliation import run_work_graph_reconciliation
from data_foundation.paths import load_paths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile Nova-Task evidence into project graph, evidence ledger, and planning overlay.")
    parser.add_argument("--date", help="Business date for reconciliation evidence, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--limit", type=int, default=120, help="Maximum evidence items to send after noise filtering.")
    parser.add_argument("--technical-report", type=Path, help="Technical report markdown to use as primary evidence.")
    parser.add_argument("--apply", action="store_true", help="Apply deterministic Nova-Task graph/ledger/planning-overlay writes.")
    parser.add_argument(
        "--actions-only",
        action="store_true",
        help="When applying, only execute legacy candidate_actions cleanup; skip graph/ledger/planning writes.",
    )
    parser.add_argument(
        "--auto-confirm-non-l1",
        action="store_true",
        help="Legacy compatibility flag. Non-Level-1 graph changes are direct by default and no longer need candidate confirmation.",
    )
    parser.add_argument(
        "--include-reconciled-test-set",
        action="store_true",
        help="Use pending/deferred plus previously reconciled candidates as evidence input for graph materialization tests.",
    )
    parser.add_argument(
        "--legacy-candidate-review-apply",
        "--candidate-review-apply",
        dest="legacy_candidate_review_apply",
        action="store_true",
        help="Legacy compatibility mode. Default production mode writes validated non-Level-1 graph changes directly.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    target = date.fromisoformat(args.date) if args.date else None
    paths = load_paths()
    result = run_work_graph_reconciliation(
        paths,
        business_date=target,
        limit=args.limit,
        apply=args.apply,
        auto_confirm_non_l1=args.auto_confirm_non_l1,
        actions_only=args.actions_only,
        technical_report_path=args.technical_report,
        include_reconciled_test_set=args.include_reconciled_test_set,
        direct_graph_apply=not args.legacy_candidate_review_apply,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("LLM_THINKING_MODE", "off")
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail-fast helper for English diary pipeline stubs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from diary_generator.language_profile import current_language_profile


PASS_CONTRACTS = {
    "narrative": {
        "expectedInputs": [
            "filtered dialogue archive for businessDate",
            "Foundation diary metrics/memory/tasks snapshots",
        ],
        "expectedOutputs": [
            "English narrative diary markdown",
            "machine-readable embedded daily summary blocks",
        ],
    },
    "technical": {
        "expectedInputs": [
            "English narrative diary markdown",
            "Foundation workspace attribution catalog",
            "Foundation diary tasks snapshot",
        ],
        "expectedOutputs": [
            "English technical progress markdown",
            "Nova-Task compatible technical evidence blocks",
        ],
    },
    "learning": {
        "expectedInputs": [
            "English narrative diary markdown",
            "English technical progress markdown",
            "Foundation diary memory snapshot",
        ],
        "expectedOutputs": [
            "English lessons learned markdown",
            "machine-readable lessons blocks",
        ],
    },
}


def _parser(pass_name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"English {pass_name} diary pass contract stub.")
    parser.add_argument("date", nargs="?", help="Business date, YYYY-MM-DD.")
    parser.add_argument("--contract", action="store_true", help="Print the pass contract and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print the contract skeleton without generating artifacts.")
    return parser


def main(pass_name: str, argv: list[str] | None = None) -> int:
    args = _parser(pass_name).parse_args(sys.argv[1:] if argv is None else argv)
    env = {
        "ACTANARA_PIPELINE_LANGUAGE_PROFILE": "en",
        "ACTANARA_DIARY_SCHEMA_VERSION": "diary-v1-en",
        "ACTANARA_PROMPT_PAYLOAD_PROFILE": "en-US",
        "ACTANARA_DISPLAY_LOCALE": "en-US",
        "NOVA_RAG_LANGUAGE_PROFILE": "en",
    }
    env.update(os.environ)
    profile = current_language_profile(env)
    contract = PASS_CONTRACTS.get(pass_name, {"expectedInputs": [], "expectedOutputs": []})
    mode = "contract" if args.contract else "dry-run" if args.dry_run else "not-enabled"
    payload = {
        "status": mode,
        "pipelineLanguageProfile": profile.pipeline_language_profile,
        "diarySchemaVersion": profile.diary_schema_version,
        "promptPayloadProfile": profile.prompt_payload_profile,
        "displayLocale": profile.display_locale,
        "ragLanguageProfile": profile.rag_language_profile,
        "pass": pass_name,
        "businessDate": args.date,
        "mode": mode,
        "expectedInputs": contract["expectedInputs"],
        "expectedOutputs": contract["expectedOutputs"],
        "reason": "English diary pipeline pass contract query does not generate artifacts.",
        "machineContractsUnchanged": True,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if args.contract or args.dry_run else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main("unknown"))

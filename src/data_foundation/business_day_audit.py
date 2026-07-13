"""Inventory business-day boundary hardcodes for compatibility migration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HARDCODE_RE = re.compile(
    r"Asia/Hong_Kong|timezone\(timedelta\(hours=8\)|\+\s*timedelta\(hours=8\)|HKT|04:00|03:59|00-03"
)
IGNORED_PARTS = {".git", "__pycache__", ".venv", "node_modules"}
SOURCE_SUFFIXES = {".py", ".js", ".md", ".txt"}
COMPATIBILITY_ALLOWLIST = {
    "src/data_foundation/time.py": "authority",
    "src/dashboard/app/services/tz.py": "compatibility-wrapper",
    "src/dashboard/app/services/tokens.py": "dashboard-business-day-consumer",
    "src/dashboard/app/services/token_clock.py": "calendar-day-live-token-compatibility",
    "src/data_foundation/settings.py": "default-settings",
    "src/data_foundation/scheduler_preview.py": "scheduler-default-preview",
    "src/ai_assets_center/unified_source_collector.py": "production-source-collector",
}
LEGACY_MIGRATION_HINTS = {
    "src/dashboard/app/services/diary.py": "legacy-dashboard-scanner",
    "src/dashboard/app/services/ai_assets.py": "legacy-dashboard-scanner",
    "src/ai_assets_center/token_engine.py": "legacy-ai-assets-center",
    "src/ai_assets_center/cron_run_reporter.py": "legacy-ai-assets-center",
    "src/agentic_rag/rag_v2_indexer.py": "rag-v2-source-timestamp-normalizer",
    "src/diary_generator/narrative_pass.py": "legacy-diary-prompt-contract",
}


def business_day_hardcode_inventory(root: Path | None = None) -> dict[str, Any]:
    base = (root or ROOT).resolve()
    findings: list[dict[str, Any]] = []
    for path in _iter_files(base):
        rel = path.relative_to(base).as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(content.splitlines(), start=1):
            match = HARDCODE_RE.search(line)
            if not match:
                continue
            category = _category(rel)
            findings.append(
                {
                    "path": rel,
                    "line": line_no,
                    "token": match.group(0),
                    "category": category,
                    "migrationHint": _migration_hint(rel, category),
                }
            )
    counts: dict[str, int] = {}
    for item in findings:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return {
        "status": "inventory-only",
        "authority": "data_foundation.time",
        "defaultTimezone": "Asia/Hong_Kong",
        "businessDayStartHour": 4,
        "counts": counts,
        "findings": findings,
        "policy": {
            "newCode": "Use data_foundation.time business_date_for/business_window/resolve_timezone.",
            "compatibility": "Existing legacy scanners are tracked here until migrated behind the shared authority.",
        },
    }


def _iter_files(base: Path):
    for path in base.rglob("*"):
        if any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.is_file() and path.suffix in SOURCE_SUFFIXES:
            yield path


def _category(rel: str) -> str:
    if rel in COMPATIBILITY_ALLOWLIST:
        return COMPATIBILITY_ALLOWLIST[rel]
    if rel in LEGACY_MIGRATION_HINTS:
        return "legacy-hardcode"
    if rel.startswith("tests/") or rel.startswith("docs/"):
        return "documentation-or-test-fixture"
    return "needs-review"


def _migration_hint(rel: str, category: str) -> str:
    if category in {"authority", "compatibility-wrapper", "default-settings", "scheduler-default-preview"}:
        return "Keep as compatibility/default surface."
    if category == "documentation-or-test-fixture":
        return "No runtime migration required."
    return LEGACY_MIGRATION_HINTS.get(rel) or "Route runtime date/window logic through data_foundation.time."

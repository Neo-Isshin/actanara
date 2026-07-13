"""Read-only RAG v2 source coverage reporting."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .rag_memory_governance import governance_for_source
from .rag_settings import DEFAULT_INDEXING_SOURCE_SETS, RagSettings, resolve_rag_settings

try:
    from data_foundation.diary_paths import diary_report_prefix, diary_report_type_for_filename, iter_diary_markdown_files
except ImportError:  # pragma: no cover - direct script fallback
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from data_foundation.diary_paths import diary_report_prefix, diary_report_type_for_filename, iter_diary_markdown_files  # type: ignore


def read_v2_coverage(settings: RagSettings | None = None) -> dict[str, Any]:
    resolved = settings or resolve_rag_settings()
    diary_root = resolved.diary_source_root
    narrative_prefix = diary_report_prefix("narrative", resolved.language_profile)
    manifest = _read_json(resolved.v2_store_path / "manifest.json")
    active_sources = _read_sources(_sources_path_from_manifest(manifest))
    indexed_by_date = _read_index_date_counts(_index_path_from_manifest(manifest))
    by_source_set: dict[str, list[dict[str, Any]]] = {}
    for source in active_sources:
        by_source_set.setdefault(str(source.get("sourceSet") or ""), []).append(source)

    configured = list(resolved.indexing_source_sets)
    source_sets = []
    for source_set in sorted(set(configured).union(DEFAULT_INDEXING_SOURCE_SETS).union(by_source_set.keys())):
        expected = _expected_source(source_set, settings=resolved)
        indexed = by_source_set.get(source_set, [])
        source_sets.append(
            {
                "sourceSet": source_set,
                "configured": source_set in configured,
                "defaultConfigured": source_set in DEFAULT_INDEXING_SOURCE_SETS,
                "governance": governance_for_source(source_set),
                "expected": expected,
                "discoveredSourceCount": expected["discoveredSourceCount"],
                "indexedSourceCount": len(indexed),
                "indexedChunkCount": sum(_int(item.get("chunkCount")) for item in indexed),
                "indexedPaths": [item.get("path") for item in indexed if item.get("path")],
                "coverageStatus": _coverage_status(source_set, expected, indexed),
            }
        )

    return {
        "schemaVersion": 1,
        "status": "ready",
        "readOnly": True,
        "checkedAt": datetime.now().astimezone().isoformat(),
        "activeRunId": manifest.get("activeRunId") or manifest.get("indexVersion"),
        "activeIndexPath": manifest.get("activeIndexPath"),
        "activeSourcesPath": str(_sources_path_from_manifest(manifest)) if _sources_path_from_manifest(manifest) else None,
        "paths": {
            "diaryRoot": str(diary_root),
            "filteredDialoguePattern": str(diary_root / "__diary_daily" / "*" / "_filtered" / "*" / "*.jsonl"),
            "diaryMarkdownPattern": str(diary_root / "diary-????" / "diary-????-??" / "??-??" / f"{narrative_prefix}-*.md"),
            "taskBoardPath": str(resolved.task_board_path),
            "lessonsPath": str(resolved.lessons_path),
            "foundationDbPath": str(resolved.foundation_db_path),
            "v2StorePath": str(resolved.v2_store_path),
        },
        "sourceSets": source_sets,
        "dateCoverage": _date_coverage(settings=resolved, active_sources=active_sources, indexed_by_date=indexed_by_date),
        "summary": {
            "configuredSourceSetCount": len(configured),
            "indexedSourceSetCount": len([item for item in source_sets if item["indexedSourceCount"] > 0]),
            "indexedSourceCount": len(active_sources),
            "indexedChunkCount": sum(item["indexedChunkCount"] for item in source_sets),
            "missingConfiguredSourceSets": [
                item["sourceSet"]
                for item in source_sets
                if item["configured"] and item["coverageStatus"] in {"missing-source", "not-indexed"}
            ],
        },
        "mutationPolicy": {
            "readOnly": True,
            "legacyMutated": False,
            "v2StoreMutated": False,
            "settingsMutated": False,
        },
}


def _date_coverage(
    *,
    settings: RagSettings,
    active_sources: list[dict[str, Any]],
    indexed_by_date: dict[str, dict[str, int]],
) -> dict[str, Any]:
    expected = _expected_dates(settings)
    if not indexed_by_date:
        indexed_by_date = _indexed_dates_from_sources(active_sources)

    all_dates = sorted(set(expected).union(indexed_by_date))
    rows: list[dict[str, Any]] = []
    for date_value in all_dates:
        upstream = expected.get(date_value, _empty_expected_date())
        indexed_source_sets = indexed_by_date.get(date_value, {})
        missing_upstream = [
            key
            for key in ("filteredDialogue", "diaryMarkdown", "foundationProjection")
            if upstream.get(key, 0) == 0
        ]
        indexed_chunk_count = sum(indexed_source_sets.values())
        rows.append(
            {
                "date": date_value,
                "filteredDialogueCount": upstream.get("filteredDialogue", 0),
                "diaryMarkdownCount": upstream.get("diaryMarkdown", 0),
                "foundationProjectionCount": upstream.get("foundationProjection", 0),
                "indexedChunkCount": indexed_chunk_count,
                "indexedSourceSets": sorted(indexed_source_sets),
                "missingUpstream": missing_upstream,
                "upstreamStatus": "complete" if not missing_upstream else "missing" if len(missing_upstream) == 3 else "partial",
                "ragIndexStatus": "indexed" if indexed_chunk_count > 0 else "missing",
                "onlyMissingRagIndex": not missing_upstream and indexed_chunk_count == 0,
                "recommendedAction": "run-rag-sync" if not missing_upstream and indexed_chunk_count == 0 else "run-daily-pipeline-or-foundation-materialization" if missing_upstream else "none",
            }
        )

    missing_upstream_rows = [row for row in rows if row["missingUpstream"]]
    only_missing_rag_rows = [row for row in rows if row["onlyMissingRagIndex"]]
    indexed_missing_upstream_rows = [
        row for row in rows if row["indexedChunkCount"] > 0 and row["missingUpstream"]
    ]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "dateCount": len(rows),
        "dates": rows[-120:],
        "truncated": len(rows) > 120,
        "summary": {
            "completeUpstreamDateCount": len([row for row in rows if row["upstreamStatus"] == "complete"]),
            "missingUpstreamDateCount": len(missing_upstream_rows),
            "missingRagIndexDateCount": len([row for row in rows if row["ragIndexStatus"] == "missing"]),
            "onlyMissingRagIndexDateCount": len(only_missing_rag_rows),
            "indexedButUpstreamMissingDateCount": len(indexed_missing_upstream_rows),
            "recommendRagSync": bool(only_missing_rag_rows),
            "recommendDailyPipelineOrFoundationMaterialization": bool(missing_upstream_rows),
        },
        "onlyMissingRagIndexDates": [row["date"] for row in only_missing_rag_rows[:50]],
        "missingUpstreamDates": [
            {"date": row["date"], "missingUpstream": row["missingUpstream"]}
            for row in missing_upstream_rows[:50]
        ],
    }


def _expected_dates(settings: RagSettings) -> dict[str, dict[str, int]]:
    dates: dict[str, dict[str, int]] = {}
    for path in _filtered_dialogue_paths(settings):
        date_value = _business_date_from_filtered_path(path)
        if date_value:
            dates.setdefault(date_value, _empty_expected_date())["filteredDialogue"] += 1
    for path in _diary_markdown_paths(settings):
        date_value = _business_date_from_diary_path(path)
        if date_value:
            dates.setdefault(date_value, _empty_expected_date())["diaryMarkdown"] += 1
    for date_value, count in _foundation_projection_dates(settings).items():
        dates.setdefault(date_value, _empty_expected_date())["foundationProjection"] += count
    return dates


def _empty_expected_date() -> dict[str, int]:
    return {"filteredDialogue": 0, "diaryMarkdown": 0, "foundationProjection": 0}


def _filtered_dialogue_paths(settings: RagSettings) -> list[Path]:
    daily_root = Path(settings.diary_source_root).expanduser() / "__diary_daily"
    return sorted(daily_root.glob("*/_filtered/*/*.jsonl")) if daily_root.exists() else []


def _diary_markdown_paths(settings: RagSettings) -> list[Path]:
    root = Path(settings.diary_source_root).expanduser()
    if not root.exists():
        return []
    return [
        path
        for path in iter_diary_markdown_files(root)
        if diary_report_type_for_filename(path.name, language_profile=settings.language_profile)
        in {"narrative", "technical", "learning"}
    ]


def _foundation_projection_dates(settings: RagSettings) -> dict[str, int]:
    db_path = Path(settings.foundation_db_path).expanduser()
    if not db_path.exists():
        return {}
    dates: dict[str, int] = {}
    for table, column in (
        ("daily_tool_usage", "business_date"),
        ("daily_model_usage", "business_date"),
        ("daily_project_usage", "business_date"),
    ):
        for row in _sqlite_date_counts(db_path, table, column):
            dates[row["date"]] = dates.get(row["date"], 0) + row["count"]
    for row in _sqlite_snapshot_date_counts(db_path):
        dates[row["date"]] = dates.get(row["date"], 0) + row["count"]
    for row in _sqlite_date_counts(db_path, "period_reports", "end_date"):
        dates[row["date"]] = dates.get(row["date"], 0) + row["count"]
    return dates


def _sqlite_date_counts(db_path: Path, table: str, column: str) -> list[dict[str, Any]]:
    if not _sqlite_table_exists(db_path, table):
        return []
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                f"SELECT {column} AS date_value, COUNT(*) FROM {table} WHERE status = 'ready' GROUP BY {column}"
                if table == "period_reports"
                else f"SELECT {column} AS date_value, COUNT(*) FROM {table} GROUP BY {column}"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    return [{"date": str(row[0]), "count": int(row[1] or 0)} for row in rows if _date_text(row[0])]


def _sqlite_snapshot_date_counts(db_path: Path) -> list[dict[str, Any]]:
    if not _sqlite_table_exists(db_path, "dashboard_snapshots"):
        return []
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                """
                SELECT snapshot_key, generated_at
                FROM dashboard_snapshots
                WHERE status = 'ready'
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    counts: dict[str, int] = {}
    for snapshot_key, generated_at in rows:
        date_value = _date_from_text(snapshot_key) or _date_text(generated_at)
        if date_value:
            counts[date_value] = counts.get(date_value, 0) + 1
    return [{"date": date_value, "count": count} for date_value, count in counts.items()]


def _sqlite_table_exists(db_path: Path, table: str) -> bool:
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            return row is not None
        finally:
            connection.close()
    except sqlite3.Error:
        return False


def _indexed_dates_from_sources(active_sources: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    dates: dict[str, dict[str, int]] = {}
    for source in active_sources:
        source_set = str(source.get("sourceSet") or "")
        path = Path(str(source.get("path") or ""))
        date_value = _business_date_from_filtered_path(path) or _business_date_from_diary_path(path)
        if not date_value or not source_set:
            continue
        dates.setdefault(date_value, {})
        dates[date_value][source_set] = dates[date_value].get(source_set, 0) + _int(source.get("chunkCount"))
    return dates


def _read_index_date_counts(path: Path | None) -> dict[str, dict[str, int]]:
    if not path or not path.exists():
        return {}
    dates: dict[str, dict[str, int]] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                date_value = _date_text(payload.get("date"))
                source_set = str(payload.get("sourceSet") or "")
                if not date_value or not source_set:
                    continue
                dates.setdefault(date_value, {})
                dates[date_value][source_set] = dates[date_value].get(source_set, 0) + 1
    except OSError:
        return {}
    return dates


def _business_date_from_filtered_path(path: Path) -> str | None:
    parts = list(path.parts)
    try:
        index = parts.index("__diary_daily")
    except ValueError:
        return None
    if index + 1 >= len(parts):
        return None
    return _date_text(parts[index + 1])


def _business_date_from_diary_path(path: Path) -> str | None:
    parts = list(path.parts)
    for index in range(0, len(parts) - 2):
        if re.match(r"diary-\d{4}$", parts[index]) and re.match(r"diary-\d{4}-\d{2}$", parts[index + 1]) and re.match(r"\d{2}-\d{2}$", parts[index + 2]):
            year = parts[index].removeprefix("diary-")
            month = parts[index + 1].rsplit("-", 1)[-1]
            day = parts[index + 2].split("-", 1)[-1]
            return f"{year}-{month}-{day}"
    for part in parts:
        match = re.match(r"diary-(\d{4}-\d{2}-\d{2})$", part)
        if match:
            return match.group(1)
    for index in range(0, len(parts) - 2):
        candidate = "/".join(parts[index : index + 3])
        if re.match(r"\d{4}/\d{2}/\d{2}$", candidate):
            return candidate.replace("/", "-")
    return _date_from_text(path.name)


def _date_from_text(value: Any) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(value or ""))
    return match.group(1) if match else None


def _date_text(value: Any) -> str | None:
    text = str(value or "")[:10]
    return text if re.match(r"\d{4}-\d{2}-\d{2}$", text) else None


def _expected_source(source_set: str, *, settings: RagSettings) -> dict[str, Any]:
    diary_root = settings.diary_source_root
    narrative_prefix = diary_report_prefix("narrative", settings.language_profile)
    technical_prefix = diary_report_prefix("technical", settings.language_profile)
    specs = {
        "filtered-dialogue-daily": {
            "kind": "glob",
            "pattern": diary_root / "__diary_daily" / "*" / "_filtered" / "*" / "*.jsonl",
            "required": True,
            "authority": "Open Nova cleaned dialogue pipeline output.",
        },
        "lessons": {
            "kind": "file",
            "path": settings.lessons_path,
            "required": False,
            "authority": "Curated long-term lessons.",
        },
        "diary-markdown-sections": {
            "kind": "diary-markdown",
            "pattern": diary_root / "diary-????" / "diary-????-??" / "??-??" / f"{narrative_prefix}-*.md",
            "fallbackPatterns": [diary_root / "????" / "??" / "??" / f"{narrative_prefix}-*.md", diary_root / "diary-????-??-??" / f"{narrative_prefix}-*.md"],
            "required": True,
            "authority": "Generated diary markdown reports.",
            "reportTypes": {"narrative", "technical", "learning"},
        },
        "diary-markdown-embedded-json": {
            "kind": "diary-markdown",
            "pattern": diary_root / "diary-????" / "diary-????-??" / "??-??" / f"{narrative_prefix}-*.md",
            "fallbackPatterns": [diary_root / "????" / "??" / "??" / f"{narrative_prefix}-*.md", diary_root / "diary-????-??-??" / f"{narrative_prefix}-*.md"],
            "required": False,
            "authority": "Embedded JSON parsed from generated diary markdown.",
            "reportTypes": {"narrative", "technical", "learning"},
        },
        "technical-report-task-events": {
            "kind": "diary-markdown",
            "pattern": diary_root / "**" / f"{technical_prefix}-*.md",
            "required": False,
            "authority": "Legacy generated technical report task history; historical observations only.",
            "reportTypes": {"technical"},
        },
        "nova-task-work-graph-events": {
            "kind": "glob",
            "pattern": settings.v2_store_path.parents[2] / "state" / "nova-task" / "work-graph" / "*.md",
            "required": False,
            "authority": "Nova-Task work-graph artifacts with project graph, evidence ledger, and planning overlay writes.",
        },
        "nova-task-reconciliation-events": {
            "kind": "glob",
            "pattern": settings.v2_store_path.parents[2] / "state" / "nova-task" / "candidate-reconciliation" / "*.md",
            "required": False,
            "authority": "Legacy Nova-Task reconciliation artifacts; retained for historical compatibility.",
        },
        "task-board-snapshot": {
            "kind": "file",
            "path": settings.task_board_path,
            "required": False,
            "authority": "Current task-board state.",
        },
        "foundation-usage-rollups": {
            "kind": "sqlite",
            "path": settings.foundation_db_path,
            "required": False,
            "authority": "Foundation normalized usage rollups.",
        },
        "foundation-dashboard-snapshots": {
            "kind": "sqlite",
            "path": settings.foundation_db_path,
            "required": False,
            "authority": "Foundation materialized dashboard snapshots.",
        },
        "foundation-period-projections": {
            "kind": "sqlite",
            "path": settings.foundation_db_path,
            "required": False,
            "authority": "Foundation materialized period projections.",
        },
    }
    spec = specs.get(source_set)
    if not spec:
        return {
            "kind": "unknown",
            "required": False,
            "pathAlignedWithPipeline": False,
            "discoveredSourceCount": 0,
            "paths": [],
        }
    if spec["kind"] == "diary-markdown":
        paths = []
        if diary_root.exists():
            report_types = set(spec.get("reportTypes") or ())
            paths = [
                path
                for path in iter_diary_markdown_files(diary_root)
                if diary_report_type_for_filename(path.name, language_profile=settings.language_profile) in report_types
            ]
        return _expected_payload(spec, paths=paths)
    if spec["kind"] == "glob":
        patterns = [spec["pattern"], *(spec.get("fallbackPatterns") or [])]
        paths = []
        seen = set()
        for pattern in patterns:
            base = diary_root
            try:
                glob_pattern = str(pattern.relative_to(diary_root))
            except ValueError:
                base = pattern.parent
                glob_pattern = pattern.name
            if base.exists():
                for path in sorted(Path(base).glob(glob_pattern)):
                    if path not in seen:
                        paths.append(path)
                        seen.add(path)
        return _expected_payload(spec, paths=paths)
    path = Path(spec["path"])
    paths = [path] if path.exists() else []
    return _expected_payload(spec, paths=paths)


def _expected_payload(spec: dict[str, Any], *, paths: list[Path]) -> dict[str, Any]:
    pattern = spec.get("pattern")
    fallback_patterns = spec.get("fallbackPatterns") or []
    path = spec.get("path")
    return {
        "kind": spec["kind"],
        "required": bool(spec.get("required")),
        "authority": spec.get("authority"),
        "pattern": str(pattern) if pattern else None,
        "fallbackPatterns": [str(item) for item in fallback_patterns],
        "path": str(path) if path else None,
        "pathAlignedWithPipeline": True,
        "exists": bool(paths),
        "discoveredSourceCount": len(paths),
        "paths": [str(item) for item in paths[:50]],
        "truncated": len(paths) > 50,
    }


def _coverage_status(source_set: str, expected: dict[str, Any], indexed: list[dict[str, Any]]) -> str:
    if expected.get("kind") == "unknown":
        return "unknown-source-set"
    if expected.get("discoveredSourceCount", 0) == 0:
        return "missing-source" if expected.get("required") else "optional-missing"
    if not indexed:
        return "not-indexed"
    indexed_chunks = sum(_int(item.get("chunkCount")) for item in indexed)
    return "indexed-empty" if indexed_chunks == 0 else "covered"


def _read_sources(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    rows = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except OSError:
        return []
    return rows


def _sources_path_from_manifest(manifest: dict[str, Any]) -> Path | None:
    value = manifest.get("sourcesPath")
    return Path(str(value)).expanduser() if value else None


def _index_path_from_manifest(manifest: dict[str, Any]) -> Path | None:
    value = manifest.get("activeIndexPath") or manifest.get("indexPath")
    if not value:
        return None
    path = Path(str(value)).expanduser()
    nested = path / "index.jsonl"
    return nested if path.is_dir() and nested.exists() else path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

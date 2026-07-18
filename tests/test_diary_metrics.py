import json
import io
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.adapters.base import NormalizedEvent, SourceArtifact
from data_foundation.aggregate import daily_diary_usage_metrics
from data_foundation.diary_metrics import (
    _approved_codex_cache_input_normalization,
    diary_memory_readiness,
    diary_metrics_readiness,
    diary_tasks_readiness,
    write_diary_metrics_readiness_report,
    write_diary_metrics_table_mismatch_approval,
)
from data_foundation.ingest import run_shadow_ingestion
from data_foundation.nova_task import create_task_node
from data_foundation.paths import initialize_home
from data_foundation.snapshots import (
    materialize_diary_memory_snapshot,
    materialize_diary_tasks_snapshot,
    read_diary_memory_snapshot,
    read_diary_tasks_snapshot,
)
from data_foundation import weather as weather_service
from ai_assets_center import cron_run_reporter
from diary_generator import narrative_pass


class _FixtureAdapter:
    tool_key = "codex"
    adapter_version = "fixture-v1"
    capabilities = {"usage_events"}

    def __init__(self, artifact_path: Path):
        self.artifact_path = artifact_path

    def discover_sources(self):
        return (SourceArtifact(self.tool_key, self.artifact_path, "fixture"),)

    def fingerprint(self, artifact):
        return "fixture"

    def read_incremental(self, artifact, cursor):
        return (
            NormalizedEvent(
                tool_key="codex",
                external_session_key="session-1",
                external_event_key="event-1",
                occurred_at=datetime(2026, 5, 19, 2, 0, tzinfo=timezone.utc),
                event_type="usage",
                payload={
                    "model_key": "gpt-fixture",
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "cache_read_tokens": 3,
                    "cache_write_tokens": 99,
                    "reasoning_tokens": 0,
                    "message_count": 1,
                    "raw_locator": {},
                    "metadata": {},
                },
            ),
        )


class _EmptyFixtureAdapter(_FixtureAdapter):
    def read_incremental(self, artifact, cursor):
        return ()


def _fixture_metrics() -> dict:
    metrics = {}
    for source in ("openclaw", "gemini-cli", "claude-code", "hermes", "codex", "cron"):
        metrics[source] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read": 0,
            "total_tokens": 0,
            "api_calls": 0,
            "messages_count": 0,
            "active_sessions": 0,
            "sessions_total": 0,
        }
    metrics["codex"].update(
        {"input_tokens": 10, "output_tokens": 2, "cache_read": 3, "total_tokens": 15, "api_calls": 1, "messages_count": 1, "active_sessions": 1, "sessions_total": 1}
    )
    metrics["total"] = dict(metrics["codex"])
    metrics["model_usage_list"] = [{"model": "gpt-fixture", "calls": 1, "tokens": 15}]
    return metrics


class DiaryMetricsReaderTests(unittest.TestCase):
    def _materialized_paths(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        artifact = root / "fixture.jsonl"
        artifact.write_text("{}\n", encoding="utf-8")
        paths = initialize_home(root / "Actanara")
        with patch(
            "data_foundation.ingest.business_date_for",
            return_value=date(2026, 5, 19),
        ):
            run_shadow_ingestion(
                paths,
                date(2026, 5, 19),
                adapters=(_FixtureAdapter(artifact),),
                observe_assets=False,
            )
        return paths

    def test_foundation_daily_metrics_keep_existing_token_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._materialized_paths(Path(tmp))
            metrics = daily_diary_usage_metrics(paths, date(2026, 5, 19))
            self.assertEqual(metrics, _fixture_metrics())

    def test_foundation_daily_metrics_return_zero_shape_for_materialized_empty_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "empty.jsonl"
            artifact.write_text("", encoding="utf-8")
            paths = initialize_home(root / "Actanara")
            with patch(
                "data_foundation.ingest.business_date_for",
                return_value=date(2026, 5, 19),
            ):
                run_shadow_ingestion(
                    paths,
                    date(2026, 5, 19),
                    adapters=(_EmptyFixtureAdapter(artifact),),
                    observe_assets=False,
                )
            metrics = daily_diary_usage_metrics(paths, date(2026, 5, 19))

        expected = _fixture_metrics()
        for source in ("openclaw", "gemini-cli", "claude-code", "hermes", "codex", "cron", "total"):
            expected[source] = {key: 0 for key in expected[source]}
        expected["model_usage_list"] = []
        self.assertEqual(metrics, expected)

    def test_readiness_blocks_missing_or_visible_model_usage_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = initialize_home(Path(tmp) / "empty")
            missing = diary_metrics_readiness(empty, date(2026, 5, 19))
            self.assertEqual(missing["status"], "unavailable")
            self.assertFalse(missing["canEnable"]["diaryMetricsSourceFoundation"])

            paths = self._materialized_paths(Path(tmp) / "ready")
            legacy = _fixture_metrics()
            legacy["model_usage_list"] = []
            changed = diary_metrics_readiness(paths, date(2026, 5, 19), legacy_builder=lambda selected: legacy)
            self.assertEqual(changed["status"], "model_usage_change_requires_approval")
            self.assertTrue(changed["canEnable"]["tokenTableFoundation"])
            self.assertFalse(changed["canEnable"]["diaryMetricsSourceFoundation"])
            self.assertTrue(changed["modelUsage"]["requiresApproval"])
            self.assertFalse(changed["modelUsage"]["approvedNormalization"])

            approved = diary_metrics_readiness(
                paths,
                date(2026, 5, 19),
                legacy_builder=lambda selected: legacy,
                approve_model_usage_normalization=True,
            )
            self.assertEqual(approved["status"], "ready_with_approved_model_usage_change")
            self.assertTrue(approved["canEnable"]["diaryMetricsSourceFoundation"])
            self.assertFalse(approved["modelUsage"]["requiresApproval"])
            self.assertTrue(approved["modelUsage"]["approvedNormalization"])

    def test_readiness_allows_approved_session_count_normalization_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._materialized_paths(Path(tmp))
            legacy = _fixture_metrics()
            legacy["claude-code"] = dict(legacy["claude-code"])
            legacy["total"] = dict(legacy["total"])
            legacy["claude-code"]["active_sessions"] -= 1
            legacy["claude-code"]["sessions_total"] -= 1
            legacy["total"]["active_sessions"] -= 1
            legacy["total"]["sessions_total"] -= 1

            blocked = diary_metrics_readiness(paths, date(2026, 5, 19), legacy_builder=lambda selected: legacy)
            self.assertEqual(blocked["status"], "table_metrics_mismatch")
            self.assertTrue(blocked["tableMetrics"]["requiresApproval"])
            self.assertFalse(blocked["canEnable"]["diaryMetricsSourceFoundation"])

            approved = diary_metrics_readiness(
                paths,
                date(2026, 5, 19),
                legacy_builder=lambda selected: legacy,
                approve_session_count_normalization=True,
            )
            self.assertEqual(approved["status"], "ready_with_approved_session_count_change")
            self.assertTrue(approved["tableMetrics"]["approvedSessionCountNormalization"])
            self.assertTrue(approved["canEnable"]["tokenTableFoundation"])
            self.assertTrue(approved["canEnable"]["diaryMetricsSourceFoundation"])

            token_changed = _fixture_metrics()
            token_changed["claude-code"] = dict(token_changed["claude-code"])
            token_changed["claude-code"]["total_tokens"] -= 1
            still_blocked = diary_metrics_readiness(
                paths,
                date(2026, 5, 19),
                legacy_builder=lambda selected: token_changed,
                approve_session_count_normalization=True,
            )
            self.assertEqual(still_blocked["status"], "table_metrics_mismatch")
            self.assertFalse(still_blocked["canEnable"]["diaryMetricsSourceFoundation"])

    def test_readiness_allows_approved_codex_cached_input_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._materialized_paths(Path(tmp))
            legacy = _fixture_metrics()
            legacy["codex"] = dict(legacy["codex"])
            legacy["total"] = dict(legacy["total"])
            for bucket in ("codex", "total"):
                legacy[bucket]["input_tokens"] += legacy[bucket]["cache_read"]
                legacy[bucket]["total_tokens"] += legacy[bucket]["cache_read"]
                legacy[bucket]["active_sessions"] += 1
                legacy[bucket]["sessions_total"] += 1
            legacy["model_usage_list"] = [{"model": "gpt-fixture", "calls": 1, "tokens": 18}]

            blocked = diary_metrics_readiness(
                paths,
                date(2026, 5, 19),
                legacy_builder=lambda selected: legacy,
                approve_model_usage_normalization=True,
            )
            self.assertEqual(blocked["status"], "table_metrics_mismatch")
            self.assertFalse(blocked["canEnable"]["diaryMetricsSourceFoundation"])
            self.assertTrue(blocked["tableMetrics"]["approvedCodexCacheInputNormalization"])
            self.assertTrue(blocked["tableMetrics"]["requiresApproval"])

            approved = diary_metrics_readiness(
                paths,
                date(2026, 5, 19),
                legacy_builder=lambda selected: legacy,
                approve_model_usage_normalization=True,
                approve_session_count_normalization=True,
            )
            self.assertEqual(approved["status"], "ready_with_approved_metric_normalizations")
            self.assertTrue(approved["tableMetrics"]["approvedCodexCacheInputNormalization"])
            self.assertTrue(approved["tableMetrics"]["approvedSessionCountNormalization"])
            self.assertTrue(approved["canEnable"]["diaryMetricsSourceFoundation"])

    def test_codex_cached_input_approval_uses_codex_delta_for_total_row(self):
        foundation = _fixture_metrics()
        legacy = _fixture_metrics()
        for metrics in (foundation, legacy):
            metrics["claude-code"] = dict(metrics["claude-code"])
            metrics["total"] = dict(metrics["total"])
            metrics["claude-code"]["cache_read"] = 5
            metrics["total"]["cache_read"] += 5

        codex_cache_read = foundation["codex"]["cache_read"]
        legacy["claude-code"]["active_sessions"] -= 1
        legacy["claude-code"]["sessions_total"] -= 1
        legacy["codex"] = dict(legacy["codex"])
        legacy["total"] = dict(legacy["total"])
        legacy["codex"]["input_tokens"] += codex_cache_read
        legacy["codex"]["total_tokens"] += codex_cache_read
        legacy["total"]["input_tokens"] += codex_cache_read
        legacy["total"]["total_tokens"] += codex_cache_read
        legacy["total"]["active_sessions"] -= 1
        legacy["total"]["sessions_total"] -= 1
        differences = {
            "claude-code": {"active_sessions": 1, "sessions_total": 1},
            "codex": {"input_tokens": -codex_cache_read, "total_tokens": -codex_cache_read},
            "total": {
                "input_tokens": -codex_cache_read,
                "total_tokens": -codex_cache_read,
                "active_sessions": 1,
                "sessions_total": 1,
            },
        }

        self.assertTrue(_approved_codex_cache_input_normalization(legacy, foundation, differences))

    def test_operator_table_metrics_approval_is_bound_to_current_differences(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._materialized_paths(Path(tmp))
            legacy = _fixture_metrics()
            legacy["openclaw"] = dict(legacy["openclaw"])
            legacy["total"] = dict(legacy["total"])
            legacy["openclaw"]["total_tokens"] -= 10
            legacy["total"]["total_tokens"] -= 10

            with patch("data_foundation.diary_metrics._legacy_diary_metrics", return_value=legacy):
                blocked = write_diary_metrics_readiness_report(
                    paths,
                    date(2026, 5, 19),
                    approve_model_usage_normalization=True,
                    approve_session_count_normalization=True,
                )
            self.assertEqual(blocked["status"], "table_metrics_mismatch")
            approval = write_diary_metrics_table_mismatch_approval(
                paths,
                date(2026, 5, 19),
                operator="release-gate",
                note="Approve frozen Foundation table metrics for release readiness.",
            )
            self.assertIn("differencesDigest", approval)

            approved = diary_metrics_readiness(
                paths,
                date(2026, 5, 19),
                legacy_builder=lambda selected: legacy,
                approve_model_usage_normalization=True,
                approve_session_count_normalization=True,
            )
            self.assertEqual(approved["status"], "ready_with_operator_approved_table_metrics_change")
            self.assertTrue(approved["tableMetrics"]["operatorApprovedTableMetricsMismatch"])
            self.assertFalse(approved["tableMetrics"]["requiresApproval"])
            self.assertTrue(approved["canEnable"]["diaryMetricsSourceFoundation"])

            changed_legacy = _fixture_metrics()
            changed_legacy["openclaw"] = dict(changed_legacy["openclaw"])
            changed_legacy["total"] = dict(changed_legacy["total"])
            changed_legacy["openclaw"]["total_tokens"] -= 11
            changed_legacy["total"]["total_tokens"] -= 11
            stale_approval = diary_metrics_readiness(
                paths,
                date(2026, 5, 19),
                legacy_builder=lambda selected: changed_legacy,
                approve_model_usage_normalization=True,
                approve_session_count_normalization=True,
            )
            self.assertEqual(stale_approval["status"], "table_metrics_mismatch")
            self.assertFalse(stale_approval["canEnable"]["diaryMetricsSourceFoundation"])

    def test_readiness_allows_fully_compatible_diary_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._materialized_paths(Path(tmp))
            report = diary_metrics_readiness(
                paths,
                date(2026, 5, 19),
                legacy_builder=lambda selected: _fixture_metrics(),
            )
            self.assertEqual(report["status"], "ready")
            self.assertTrue(report["canEnable"]["diaryMetricsSourceFoundation"])
            self.assertEqual(report["preservedSources"]["rag"], "v2")
            self.assertEqual(report["preservedSources"]["memory"], "separately_guarded")

    def test_diary_memory_snapshot_matches_existing_contract_and_readiness(self):
        expected = {"sessionFiles": 2, "totalSizeMB": 3.25, "diaryCount": 4}
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._materialized_paths(Path(tmp))
            with (
                patch.object(narrative_pass, "_get_memory_stats_legacy", return_value=expected) as legacy_builder,
                patch.object(narrative_pass, "get_rag_memory_stats", side_effect=AssertionError("RAG scan called")),
            ):
                materialize_diary_memory_snapshot(paths, date(2026, 5, 19), 1)
            legacy_builder.assert_called_once_with()
            self.assertEqual(read_diary_memory_snapshot(paths, date(2026, 5, 19))["payload"], expected)
            self.assertIsNone(read_diary_memory_snapshot(paths, date(2026, 5, 20)))
            ready = diary_memory_readiness(paths, date(2026, 5, 19), legacy_builder=lambda: expected)
            self.assertEqual(ready["status"], "ready")
            self.assertTrue(ready["canEnable"]["diaryMemorySourceFoundation"])
            mismatch = diary_memory_readiness(
                paths,
                date(2026, 5, 19),
                legacy_builder=lambda: {"sessionFiles": 3, "totalSizeMB": 3.25, "diaryCount": 4},
            )
            self.assertEqual(mismatch["status"], "memory_stats_mismatch")
            self.assertFalse(mismatch["canEnable"]["diaryMemorySourceFoundation"])

    def test_narrative_source_flag_uses_foundation_without_legacy_fallback(self):
        expected = _fixture_metrics()
        with (
            patch.object(narrative_pass, "resolve_runtime_source", return_value="legacy"),
            patch.object(narrative_pass, "_calculate_stats_foundation", side_effect=AssertionError("foundation called")),
            patch.object(narrative_pass, "_calculate_stats_legacy", return_value=expected) as default_legacy,
        ):
            self.assertEqual(narrative_pass.calculate_stats_raw("2026-05-19"), expected)
        default_legacy.assert_called_once_with("2026-05-19")

        with (
            patch.object(narrative_pass, "resolve_runtime_source", return_value="foundation"),
            patch.object(narrative_pass, "_calculate_stats_foundation", return_value=expected) as foundation,
            patch.object(narrative_pass, "_calculate_stats_legacy", side_effect=AssertionError("legacy called")),
        ):
            self.assertEqual(narrative_pass.calculate_stats_raw("2026-05-19"), expected)
        foundation.assert_called_once_with("2026-05-19")

        with (
            patch.object(narrative_pass, "resolve_runtime_source", return_value="foundation"),
            patch.object(narrative_pass, "_calculate_stats_foundation", return_value=None),
            patch.object(narrative_pass, "_calculate_stats_legacy", side_effect=AssertionError("legacy called")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Foundation diary metrics missing"):
                narrative_pass.calculate_stats_raw("2026-05-19")

    def test_narrative_memory_source_flag_uses_snapshot_without_legacy_fallback(self):
        expected = {"sessionFiles": 2, "totalSizeMB": 3.25, "diaryCount": 4}
        with (
            patch.object(narrative_pass, "resolve_runtime_source", return_value="legacy"),
            patch.object(narrative_pass, "_get_memory_stats_foundation", side_effect=AssertionError("foundation called")),
            patch.object(narrative_pass, "_get_memory_stats_legacy", return_value=expected) as default_legacy,
        ):
            self.assertEqual(narrative_pass.get_memory_stats("2026-05-19"), expected)
        default_legacy.assert_called_once_with()

        with (
            patch.object(narrative_pass, "resolve_runtime_source", return_value="foundation"),
            patch.object(narrative_pass, "_get_memory_stats_foundation", return_value=expected) as foundation,
            patch.object(narrative_pass, "_get_memory_stats_legacy", side_effect=AssertionError("legacy called")),
        ):
            self.assertEqual(narrative_pass.get_memory_stats("2026-05-19"), expected)
        foundation.assert_called_once_with("2026-05-19")

        with (
            patch.object(narrative_pass, "resolve_runtime_source", return_value="foundation"),
            patch.object(narrative_pass, "_get_memory_stats_foundation", return_value=None),
            patch.object(narrative_pass, "_get_memory_stats_legacy", side_effect=AssertionError("legacy called")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Foundation diary memory snapshot missing"):
                narrative_pass.get_memory_stats("2026-05-19")

    def test_narrative_rag_stats_use_active_v2_without_legacy_index_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_index = root / "__diary_rag" / "index.jsonl"
            legacy_index.parent.mkdir(parents=True)
            legacy_index.write_text("{}\n{}\n{}\n", encoding="utf-8")
            active_index = root / "reserved" / "rag" / "v2" / "indexes" / "active" / "run-1" / "index.jsonl"
            active_index.parent.mkdir(parents=True)
            active_index.write_text("{}\n{}\n", encoding="utf-8")
            status = {
                "v2": {
                    "ready": True,
                    "activeIndexPath": str(active_index),
                    "chunkCount": 2,
                    "updatedAt": "2026-06-25T15:53:40+08:00",
                },
                "activeIndex": {"indexPath": str(active_index)},
            }

            with (
                patch.object(narrative_pass, "_runtime_diary_root", return_value=root),
                patch("agentic_rag.rag_status.read_rag_status", return_value=status),
            ):
                rag = narrative_pass._get_active_rag_stats()

            with (
                patch.object(narrative_pass, "_runtime_diary_root", return_value=root),
                patch("agentic_rag.rag_status.read_rag_status", side_effect=RuntimeError("v2 unavailable")),
            ):
                unavailable = narrative_pass._get_active_rag_stats()

        self.assertEqual(rag["entries"], 2)
        self.assertEqual(rag["source"], "rag-v2-active")
        self.assertEqual(rag["indexPath"], str(active_index))
        self.assertEqual(unavailable["entries"], 0)
        self.assertEqual(unavailable["source"], "rag-v2-unavailable")
        self.assertNotIn("indexPath", unavailable)

    def test_diary_tasks_snapshot_uses_nova_task_sqlite_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._materialized_paths(Path(tmp))
            create_task_node(paths, node_id="NT-ACTIVE", title="Active", actor="test")
            create_task_node(paths, node_id="NT-BLOCKED", title="Blocked", status="blocked", actor="test")
            create_task_node(paths, node_id="NT-DONE", title="Done", status="completed", actor="test")
            create_task_node(paths, node_id="NT-ARCHIVED", title="Archived", status="archived", actor="test")
            materialize_diary_tasks_snapshot(paths, date(2026, 5, 19), 1)

            self.assertEqual(
                read_diary_tasks_snapshot(paths, date(2026, 5, 19))["payload"],
                {"InProgress": 2, "Completed": 1},
            )
            ready = diary_tasks_readiness(
                paths,
                date(2026, 5, 19),
                legacy_builder=lambda: {"InProgress": 99, "Completed": 99},
            )
            self.assertEqual(ready["status"], "ready")
            self.assertTrue(ready["canEnable"]["diaryTasksSourceFoundation"])
            self.assertFalse(ready["tasks"]["requiresApproval"])
            self.assertEqual(ready["tasks"]["comparison"]["legacy"], {"InProgress": 99, "Completed": 99})
            self.assertTrue(ready["tasks"]["comparison"]["changed"])
            self.assertEqual(ready["preservedSources"]["taskAuthority"], "Nova-Task v2 SQLite")
            self.assertEqual(ready["preservedSources"]["taskBoard"], "projection")

    def test_narrative_tasks_source_flag_uses_checkbox_projection_without_legacy_fallback(self):
        expected = {"InProgress": 1, "Completed": 1}
        inflated = {"InProgress": 2, "Completed": 1}
        with (
            patch.object(narrative_pass, "resolve_runtime_source", return_value="legacy"),
            patch.object(narrative_pass, "_get_task_board_snapshot_foundation", side_effect=AssertionError("foundation called")),
            patch.object(narrative_pass, "_get_task_board_snapshot_legacy", return_value=inflated) as default_legacy,
        ):
            self.assertEqual(narrative_pass.get_task_board_snapshot("2026-05-19"), inflated)
        default_legacy.assert_called_once_with()

        with (
            patch.object(narrative_pass, "resolve_runtime_source", return_value="foundation"),
            patch.object(narrative_pass, "_get_task_board_snapshot_foundation", return_value=expected) as foundation,
            patch.object(narrative_pass, "_get_task_board_snapshot_legacy", side_effect=AssertionError("legacy called")),
        ):
            self.assertEqual(narrative_pass.get_task_board_snapshot("2026-05-19"), expected)
        foundation.assert_called_once_with("2026-05-19")

        with (
            patch.object(narrative_pass, "resolve_runtime_source", return_value="foundation"),
            patch.object(narrative_pass, "_get_task_board_snapshot_foundation", return_value=None),
            patch.object(narrative_pass, "_get_task_board_snapshot_legacy", side_effect=AssertionError("legacy called")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Foundation diary task snapshot missing"):
                narrative_pass.get_task_board_snapshot("2026-05-19")

    def test_approved_tasks_normalization_changes_values_without_changing_diary_shape(self):
        llm_content = "\n".join(
            [
                "## 今日概要",
                "summary",
                "## Agent工作",
                "work",
                "## 重要提醒",
                "none",
                "## 定时任务情况",
                "none",
                "## 备注",
                "note",
            ]
        )
        metrics = _fixture_metrics()
        with (
            patch.object(narrative_pass, "_get_weather_for_date", return_value="weather"),
            patch.object(narrative_pass, "get_lessons_structured", return_value=[]),
            patch.object(narrative_pass, "get_new_skills_structured", return_value=[]),
            patch.object(narrative_pass, "get_cron_structured", return_value=[]),
            patch.object(narrative_pass, "get_rag_memory_stats", return_value={"rag": {}, "memory": {}}),
            patch.object(narrative_pass, "get_task_board_snapshot", return_value={"InProgress": 18, "Completed": 0}),
        ):
            legacy = narrative_pass.assemble_final_markdown("2026-05-19", llm_content, metrics, {})
        with (
            patch.object(narrative_pass, "_get_weather_for_date", return_value="weather"),
            patch.object(narrative_pass, "get_lessons_structured", return_value=[]),
            patch.object(narrative_pass, "get_new_skills_structured", return_value=[]),
            patch.object(narrative_pass, "get_cron_structured", return_value=[]),
            patch.object(narrative_pass, "get_rag_memory_stats", return_value={"rag": {}, "memory": {}}),
            patch.object(narrative_pass, "get_task_board_snapshot", return_value={"InProgress": 12, "Completed": 0}),
        ):
            foundation = narrative_pass.assemble_final_markdown("2026-05-19", llm_content, metrics, {})
        legacy_json = json.loads(legacy.split("```json\n", 1)[1].split("\n```", 1)[0])
        foundation_json = json.loads(foundation.split("```json\n", 1)[1].split("\n```", 1)[0])
        legacy_without_tasks = dict(legacy_json)
        foundation_without_tasks = dict(foundation_json)
        legacy_without_tasks.pop("tasks")
        foundation_without_tasks.pop("tasks")
        self.assertEqual(legacy_without_tasks, foundation_without_tasks)
        self.assertEqual(foundation_json["tasks"], {"InProgress": 12, "Completed": 0})
        self.assertEqual(
            [line for line in legacy.splitlines() if line.startswith("#")],
            [line for line in foundation.splitlines() if line.startswith("#")],
        )

    def test_foundation_metrics_do_not_change_diary_markdown_or_embedded_json_shape(self):
        llm_content = "\n".join(
            [
                "## 今日概要",
                "summary",
                "## Agent工作",
                "work",
                "## 重要提醒",
                "none",
                "## 定时任务情况",
                "none",
                "## 备注",
                "note",
            ]
        )
        metrics = _fixture_metrics()
        with (
            patch.object(narrative_pass, "_get_weather_for_date", return_value="weather"),
            patch.object(narrative_pass, "get_lessons_structured", return_value=[]),
            patch.object(narrative_pass, "get_new_skills_structured", return_value=[]),
            patch.object(narrative_pass, "get_cron_structured", return_value=[]),
            patch.object(narrative_pass, "get_rag_memory_stats", return_value={"rag": {}, "memory": {}}),
            patch.object(narrative_pass, "get_task_board_snapshot", return_value={}),
        ):
            markdown = narrative_pass.assemble_final_markdown("2026-05-19", llm_content, metrics, {})
        expected_headings = [
            "# 2026年05月19日 日记",
            "## 天气",
            "## 今日概要",
            "## 本日统计",
            "## Agent工作",
            "## 重要提醒",
            "## 定时任务情况",
            "## 备注",
        ]
        positions = [markdown.index(heading) for heading in expected_headings]
        self.assertEqual(positions, sorted(positions))
        embedded = json.loads(markdown.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(
            set(embedded),
            {"date", "metrics", "agents", "lessons", "newSkills", "cronTasks", "topTopics", "ragStats", "memoryStats", "tasks", "modelUsage"},
        )
        self.assertNotIn("dataFreshness", embedded)

    def test_blank_diary_keeps_only_weather_summary_cron_and_metadata_marker(self):
        metrics = _fixture_metrics()
        for source in ("openclaw", "gemini-cli", "claude-code", "hermes", "codex", "cron", "total"):
            metrics[source] = {key: 0 for key in metrics[source]}
        metrics["cron"].update({"messages_count": 2, "total_tokens": 120, "active_sessions": 1, "sessions_total": 1})
        metrics["total"].update(metrics["cron"])
        metrics["model_usage_list"] = []
        with (
            patch.object(narrative_pass, "_get_weather_for_date", return_value="weather"),
            patch.object(narrative_pass, "get_lessons_structured", return_value=[]),
            patch.object(narrative_pass, "get_new_skills_structured", return_value=[]),
            patch.object(narrative_pass, "get_cron_structured", return_value=[{"time": "04:03", "taskId": "daily", "status": "Success"}]),
            patch.object(narrative_pass, "get_rag_memory_stats", return_value={"rag": {}, "memory": {}}),
            patch.object(narrative_pass, "get_task_board_snapshot", return_value={}),
        ):
            markdown = narrative_pass.assemble_final_markdown("2026-05-19", "", metrics, {})

        self.assertIn("## 天气\nweather", markdown)
        self.assertIn("## 今日概要\n今日无活动", markdown)
        self.assertIn("## 定时任务情况", markdown)
        self.assertNotIn("## 本日统计", markdown)
        self.assertNotIn("## Agent工作", markdown)
        self.assertNotIn("## 重要提醒", markdown)
        self.assertNotIn("## 备注", markdown)
        embedded = json.loads(markdown.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(embedded["activityState"], "empty")

    def test_write_narrative_report_writes_blank_day_without_llm_when_entries_empty(self):
        metrics = _fixture_metrics()
        for source in ("openclaw", "gemini-cli", "claude-code", "hermes", "codex", "cron", "total"):
            metrics[source] = {key: 0 for key in metrics[source]}
        metrics["model_usage_list"] = []
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(narrative_pass, "load_paths", return_value=type("Paths", (), {"diary_dir": Path(tmp)})()),
                patch.object(narrative_pass, "load_filtered_entries", return_value={}),
                patch.object(narrative_pass, "calculate_stats_raw", return_value=metrics),
                patch.object(narrative_pass, "generate_diary_with_fallback", side_effect=AssertionError("LLM should not be called")),
                patch.object(narrative_pass, "_get_weather_for_date", return_value="weather"),
                patch.object(narrative_pass, "get_lessons_structured", return_value=[]),
                patch.object(narrative_pass, "get_new_skills_structured", return_value=[]),
                patch.object(narrative_pass, "get_cron_structured", return_value=[]),
                patch.object(narrative_pass, "get_rag_memory_stats", return_value={"rag": {}, "memory": {}}),
                patch.object(narrative_pass, "get_task_board_snapshot", return_value={}),
            ):
                out_file = narrative_pass.write_narrative_report("2026-05-19")

            markdown = Path(out_file).read_text(encoding="utf-8")
        self.assertEqual(Path(out_file).name, "日记-260519-no-activity.md")
        self.assertIn("## 今日概要\n今日无活动", markdown)
        embedded = json.loads(markdown.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(embedded["activityState"], "empty")

    def test_cron_section_uses_deterministic_structured_tasks(self):
        llm_content = "\n".join(
            [
                "## 今日概要",
                "summary",
                "## Agent工作",
                "work",
                "## 重要提醒",
                "none",
                "## 定时任务情况",
                "LLM cron text should not be used",
                "## 备注",
                "note",
            ]
        )
        cron_tasks = [
            {
                "time": "04:12",
                "taskId": "daily001",
                "status": "Success",
                "duration": "8.5s",
                "conclusion": "执行完成",
            }
        ]
        metrics = _fixture_metrics()
        with (
            patch.object(narrative_pass, "_get_weather_for_date", return_value="weather"),
            patch.object(narrative_pass, "get_lessons_structured", return_value=[]),
            patch.object(narrative_pass, "get_new_skills_structured", return_value=[]),
            patch.object(narrative_pass, "get_cron_structured", return_value=cron_tasks),
            patch.object(narrative_pass, "get_rag_memory_stats", return_value={"rag": {}, "memory": {}}),
            patch.object(narrative_pass, "get_task_board_snapshot", return_value={}),
        ):
            markdown = narrative_pass.assemble_final_markdown("2026-05-19", llm_content, metrics, {})

        self.assertIn("| 时间 | 任务ID | 状态 | 耗时 | 执行结论 |", markdown)
        self.assertIn("| 04:12 | `daily001` | Success | 8.5s | 执行完成 |", markdown)
        self.assertNotIn("LLM cron text should not be used", markdown)
        embedded = json.loads(markdown.split("```json\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(embedded["cronTasks"], cron_tasks)

    def test_cron_reporter_reads_migrated_jsonl_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            occurred = datetime(2026, 5, 20, 6, 4, tzinfo=timezone.utc)
            (root / "run.jsonl.migrated").write_text(
                json.dumps(
                    {
                        "action": "finished",
                        "ts": int(occurred.timestamp() * 1000),
                        "jobId": "daily-cron-job",
                        "status": "ok",
                        "durationMs": 8500,
                        "summary": "备份完成\n详细日志",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.object(cron_run_reporter, "CRON_RUNS_DIR", root), redirect_stdout(io.StringIO()):
                report = cron_run_reporter.generate_cron_report("2026-05-20")

        self.assertIn("| 14:04 | `daily-cr", report)
        self.assertIn("| ✅ OK | 8.5s | 备份完成 |", report)

    def test_cron_reporter_uses_configured_timezone_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            occurred = datetime(2026, 5, 20, 6, 4, tzinfo=timezone.utc)
            (root / "run.jsonl").write_text(
                json.dumps(
                    {
                        "action": "finished",
                        "ts": int(occurred.timestamp() * 1000),
                        "jobId": "utc-cron-job",
                        "status": "ok",
                        "durationMs": 8500,
                        "summary": "UTC 完成",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"TARGET_TIMEZONE": "UTC"}, clear=False),
                patch.object(cron_run_reporter, "CRON_RUNS_DIR", root),
                redirect_stdout(io.StringIO()),
            ):
                report = cron_run_reporter.generate_cron_report("2026-05-20")

        self.assertIn("| 06:04 | `utc-cron", report)
        self.assertIn("| ✅ OK | 8.5s | UTC 完成 |", report)

    def test_narrative_window_uses_configured_timezone(self):
        with patch.dict(os.environ, {"TARGET_TIMEZONE": "UTC"}, clear=False):
            start_ts, duration = narrative_pass.get_hkt_window("2026-05-22")

        self.assertEqual(start_ts, datetime(2026, 5, 22, 4, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(duration, 86400)

    def test_weather_uses_archive_endpoint_and_weather_code(self):
        payload = {
            "daily": {
                "time": ["2026-06-05"],
                "temperature_2m_max": [34.2],
                "temperature_2m_min": [28.2],
                "precipitation_sum": [1.5],
                "weather_code": [95],
            }
        }

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        opened = []

        def fake_urlopen(url, **kwargs):
            del kwargs
            opened.append(url)
            return _Response()

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            with patch.dict(os.environ, {"TARGET_TIMEZONE": "UTC"}, clear=False):
                weather = weather_service.fetch_weather_for_date(
                    "2026-06-05",
                    paths=paths,
                    weather_settings={
                        "enabled": True,
                        "locationMode": "manual",
                        "latitude": 37.7749,
                        "longitude": -122.4194,
                        "timezone": "UTC",
                    },
                    urlopen=fake_urlopen,
                    sleep_seconds=0,
                )

        self.assertIn("archive-api.open-meteo.com", opened[0])
        self.assertIn("latitude=37.774900", opened[0])
        self.assertIn("longitude=-122.419400", opened[0])
        self.assertIn("timezone=UTC", opened[0])
        self.assertEqual(weather, "雷暴，最高34.2°C，最低28.2°C (降水1.5mm)")


class NarrativeQualityGateTests(unittest.TestCase):
    def test_smart_truncation_preserves_head_signal_and_tail(self):
        content = (
            "Start objective and user context. " * 8
            + "\nordinary middle text\n"
            + "src/diary_generator/narrative_pass.py failed with traceback during pytest\n"
            + "More ordinary text. " * 8
            + "Final conclusion: rollback not required and tests passed."
        )
        truncated = narrative_pass._smart_truncate_content(content, 180)
        self.assertIn("[前文]", truncated)
        self.assertIn("[中间关键信号摘录]", truncated)
        self.assertIn("[结尾]", truncated)
        self.assertIn("Start objective", truncated)
        self.assertIn("narrative_pass.py", truncated)
        self.assertIn("tests passed", truncated)

    def test_try_generate_enforces_quality_gate_before_calling_llm(self):
        entries = [{"role": "user", "time": "04:00", "content": "fixture"}]
        with (
            patch.object(
                narrative_pass,
                "get_token_count",
                side_effect=[narrative_pass.QUALITY_GATE_TOKENS + 1, narrative_pass.QUALITY_GATE_TOKENS - 1],
            ) as count,
            patch.object(narrative_pass, "call_llm", return_value="summary") as call,
            redirect_stdout(io.StringIO()),
        ):
            result = narrative_pass.try_generate(entries, "agent", truncate_list=[400, 300])
        self.assertEqual(result, "summary")
        self.assertEqual(count.call_count, 2)
        call.assert_called_once()

    def test_agent_generation_falls_back_to_two_hour_windows_and_integrates(self):
        entries = [
            {"role": "user", "time": f"{4 + (index % 4):02d}:10", "content": f"message {index}"}
            for index in range(50)
        ]

        calls = []

        def fake_count(prompt):
            agent = re.search(r"日志数据（([^，]+)，", prompt).group(1)
            if "上午(04-12)" in agent:
                return narrative_pass.QUALITY_GATE_TOKENS + 1
            return 100

        def fake_call(prompt, is_int=False, **kwargs):
            del is_int, kwargs
            calls.append(re.search(r"日志数据（([^，]+)，", prompt).group(1))
            return "### heading\nslot summary"

        with (
            patch.object(narrative_pass, "get_token_count", side_effect=fake_count),
            patch.object(narrative_pass, "call_llm", side_effect=fake_call),
            patch.object(narrative_pass, "_integrate_agent_summary", return_value="agent day") as integrate,
            redirect_stdout(io.StringIO()),
        ):
            result = narrative_pass._generate_agent_summary("agent", entries)

        self.assertEqual(result, "agent day")
        self.assertNotIn("agent - 上午(04-12)", calls)
        self.assertIn("agent - 04:00-06:00", calls)
        self.assertIn("agent - 06:00-08:00", calls)
        integrate.assert_called_once()
        summaries = integrate.call_args.args[1]
        self.assertEqual([item["slot"] for item in summaries], ["04:00-06:00", "06:00-08:00"])

    def test_agent_generation_uses_message_chunks_when_hour_still_exceeds_gate(self):
        entries = [{"role": "user", "time": "09:10", "content": f"message {index}"} for index in range(50)]

        def fake_count(prompt):
            agent = re.search(r"日志数据（([^，]+)，", prompt).group(1)
            if "#" in agent:
                return 100
            return narrative_pass.QUALITY_GATE_TOKENS + 1

        def fake_call(prompt, is_int=False, **kwargs):
            del is_int, kwargs
            return "chunk summary"

        chunks = [entries[:25], entries[25:]]
        with (
            patch.object(narrative_pass, "get_token_count", side_effect=fake_count),
            patch.object(narrative_pass, "call_llm", side_effect=fake_call),
            patch.object(narrative_pass, "_split_entries_by_gate", return_value=chunks) as split,
            patch.object(narrative_pass, "_integrate_agent_summary", return_value="agent day") as integrate,
            redirect_stdout(io.StringIO()),
        ):
            result = narrative_pass._generate_agent_summary("agent", entries)

        self.assertEqual(result, "agent day")
        split.assert_called()
        summaries = integrate.call_args.args[1]
        self.assertEqual([item["slot"] for item in summaries], ["09:00-10:00 #1", "09:00-10:00 #2"])

    def test_final_integration_precompresses_when_combined_summary_exceeds_gate(self):
        counts = []

        def fake_count(prompt):
            counts.append(prompt)
            if "compressed summary" in prompt and "技术日记整合助手" in prompt:
                return 100
            if "全日最终整合预压缩" in prompt:
                return 100
            return narrative_pass.QUALITY_GATE_TOKENS + 1

        with (
            patch.object(narrative_pass, "get_token_count", side_effect=fake_count),
            patch.object(narrative_pass, "call_llm", side_effect=["compressed summary", "final diary"]) as call,
            redirect_stdout(io.StringIO()),
        ):
            result = narrative_pass._call_final_integration({"agent": "large summary"})

        self.assertEqual(result, "final diary")
        self.assertEqual(call.call_count, 2)
        self.assertFalse(call.call_args_list[0].args[1] if len(call.call_args_list[0].args) > 1 else False)
        self.assertTrue(call.call_args_list[1].args[1])


if __name__ == "__main__":
    unittest.main()

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "adapters"
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.adapters.usage import (
    ClaudeCodeAdapter,
    CodexAdapter,
    CronAdapter,
    GeminiCliAdapter,
    HermesAdapter,
    OpenClawAdapter,
)
from data_foundation.adapters.base import SourceArtifact
from data_foundation.aggregate import daily_project_totals, daily_tool_totals
from data_foundation.db import connect, migrate
from data_foundation.ingest import run_shadow_ingestion, run_shadow_period_ingestion
from data_foundation.paths import initialize_home
from data_foundation.settings import write_settings
from ai_assets_center import token_engine


class ShadowIngestionTests(unittest.TestCase):
    def _adapters(self, root: Path):
        openclaw = root / "openclaw" / "agent-one" / "sessions"
        claude = root / "claude"
        codex = root / "codex"
        gemini = root / "gemini"
        cron = root / "cron"
        for path in (openclaw, claude, codex, gemini, cron):
            path.mkdir(parents=True)
        shutil.copy(FIXTURES / "openclaw_usage.jsonl", openclaw / "session.jsonl")
        shutil.copy(FIXTURES / "claude_usage.jsonl", claude / "session.jsonl")
        shutil.copy(FIXTURES / "codex_token_count.jsonl", codex / "rollout-fixture.jsonl")
        shutil.copy(FIXTURES / "gemini_usage.jsonl", gemini / "session-fixture.jsonl")
        shutil.copy(FIXTURES / "cron_usage.jsonl", cron / "run.jsonl")
        hermes = root / "hermes.db"
        with closing(sqlite3.connect(hermes)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE sessions (
                        id TEXT, started_at REAL, model TEXT, input_tokens INTEGER,
                        output_tokens INTEGER, cache_read_tokens INTEGER,
                        cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                        api_call_count INTEGER
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("hermes-session", 1779163200, "hermes-model", 40, 6, 8, 900, 1, 2),
                )
        return (
            OpenClawAdapter(root / "openclaw"),
            ClaudeCodeAdapter(claude),
            CodexAdapter(codex),
            GeminiCliAdapter(gemini),
            HermesAdapter(hermes),
            CronAdapter(cron),
        )

    def test_usage_is_idempotent_hkt_scoped_and_protocol_excludes_cache_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "legacy")
            adapters = self._adapters(root)
            target = date(2026, 5, 19)
            (paths.config_dir / "projects-registry.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "projects": [
                            {"canonical_name": "open-nova", "canonical_root": "/workspace/example/open-nova"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            first = run_shadow_ingestion(paths, target, adapters=adapters, observe_assets=False)
            second = run_shadow_ingestion(paths, target, adapters=adapters, observe_assets=False)
            totals = daily_tool_totals(paths, target)
            self.assertEqual(first.events_in_window, 7)
            self.assertEqual(first.errors, 0)
            self.assertEqual(second.events_in_window, 7)
            self.assertEqual(totals["openclaw"]["tokens"], 15)
            self.assertEqual(totals["claude-code"]["tokens"], 30)
            self.assertEqual(totals["codex"]["tokens"], 15)
            self.assertEqual(totals["gemini-cli"]["tokens"], 42)
            self.assertEqual(totals["hermes"]["tokens"], 54)
            self.assertEqual(totals["cron"]["tokens"], 14)
            self.assertEqual(totals["cron"]["sessions"], 1)
            with connect(paths, read_only=True) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0], 7)
                codex = connection.execute(
                    "SELECT input_tokens, protocol_total_tokens, metadata_json FROM usage_events WHERE tool_key = 'codex'"
                ).fetchone()
                codex_model = connection.execute(
                    "SELECT model_key, tokens FROM daily_model_usage WHERE tool_key = 'codex'"
                ).fetchone()
                evidence = connection.execute("SELECT normalized_path FROM activity_evidence").fetchall()
            self.assertEqual(codex["input_tokens"], 10)
            self.assertEqual(codex["protocol_total_tokens"], 15)
            self.assertEqual(json.loads(codex["metadata_json"])["token_semantics"], "last_token_usage")
            self.assertEqual(dict(codex_model), {"model_key": "gpt-5.5", "tokens": 15})
            self.assertEqual([row["normalized_path"] for row in evidence], ["/workspace/example/open-nova"])
            project_rows = daily_project_totals(paths, target)
            assigned = [row for row in project_rows if row["project_id_or_bucket"].startswith("project:")]
            self.assertEqual(assigned[0]["tool_key"], "claude-code")
            self.assertEqual(assigned[0]["tokens"], 30)
            self.assertEqual(assigned[0]["evidence_confidence"], "high")

    def test_codex_adapter_normalizes_cached_input_when_total_tokens_excludes_cache_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_path = root / "rollout-codex.jsonl"
            usage = {
                "input_tokens": 10,
                "output_tokens": 2,
                "cached_input_tokens": 3,
                "reasoning_output_tokens": 4,
                "total_tokens": 12,
            }
            rows = [
                {"type": "session_meta", "payload": {"id": "codex-session", "cwd": "/workspace/example/open-nova"}},
                {"timestamp": "2026-05-19T04:00:00Z", "type": "turn_context", "payload": {"model": "gpt-5.5"}},
                {
                    "timestamp": "2026-05-19T04:00:00Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "info": {"last_token_usage": usage}},
                },
            ]
            artifact_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            events = list(CodexAdapter(root).read_incremental(SourceArtifact("codex", artifact_path, "rollout_jsonl")))

        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["input_tokens"], 7)
        self.assertEqual(payload["output_tokens"], 2)
        self.assertEqual(payload["cache_read_tokens"], 3)
        self.assertEqual(payload["input_tokens"] + payload["output_tokens"] + payload["cache_read_tokens"], 12)
        self.assertEqual(payload["metadata"]["raw_input_tokens"], 10)
        self.assertEqual(payload["metadata"]["reported_total_tokens"], 12)
        self.assertEqual(payload["metadata"]["cache_input_semantics"], "input_includes_cached_input")
        self.assertEqual(payload["model_key"], "gpt-5.5")

    def test_shadow_ingestion_uses_runtime_timezone_for_business_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_path = root / "rollout-codex.jsonl"
            rows = [
                {"type": "session_meta", "payload": {"id": "codex-session", "cwd": "/workspace/example/open-nova"}},
                {"timestamp": "2026-05-19T02:00:00Z", "type": "turn_context", "payload": {"model": "gpt-5.5"}},
                {
                    "timestamp": "2026-05-19T02:00:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"last_token_usage": {"input_tokens": 4, "output_tokens": 1, "cached_input_tokens": 2}},
                    },
                },
            ]
            artifact_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "legacy")
            write_settings({"general": {"timezone": "UTC"}}, paths)

            result = run_shadow_ingestion(
                paths,
                date(2026, 5, 18),
                adapters=(CodexAdapter(root),),
                observe_assets=False,
            )

            self.assertEqual(result.events_in_window, 1)
            with connect(paths, read_only=True) as connection:
                row = connection.execute("SELECT business_date FROM usage_events").fetchone()
            self.assertEqual(row["business_date"], "2026-05-18")

    def test_codex_adapter_falls_back_to_context_window_model_when_turn_context_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_path = root / "rollout-codex.jsonl"
            rows = [
                {"type": "session_meta", "payload": {"id": "codex-session", "cwd": "/workspace/example/open-nova"}},
                {
                    "timestamp": "2026-05-19T04:00:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "model_context_window": 258400,
                            "last_token_usage": {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 3},
                        },
                    },
                },
            ]
            artifact_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            events = list(CodexAdapter(root).read_incremental(SourceArtifact("codex", artifact_path, "rollout_jsonl")))

        self.assertEqual(events[0].payload["model_key"], "gpt-5.5")

    def test_legacy_token_engine_uses_codex_turn_context_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex = root / "codex"
            codex.mkdir()
            artifact = codex / "rollout-codex.jsonl"
            rows = [
                {"timestamp": "2026-05-19T04:00:00Z", "type": "turn_context", "payload": {"model": "gpt-5.5"}},
                {
                    "timestamp": "2026-05-19T04:00:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 3},
                        },
                    },
                },
            ]
            artifact.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            missing = root / "missing"
            with (
                patch.object(token_engine, "AGENTS_DIR", missing),
                patch.object(token_engine, "GEMINI_DIR", missing),
                patch.object(token_engine, "CLAUDE_DIR", missing),
                patch.object(token_engine, "CODEX_DIR", codex),
                patch.object(token_engine, "HERMES_DB", missing / "hermes.db"),
            ):
                _stats, model_usage = token_engine.scan_tokens("2026-05-19")

        self.assertEqual(model_usage["gpt-5.5"], {"calls": 1, "tokens": 15})

    def test_legacy_token_engine_window_uses_configured_timezone(self):
        with patch.dict("os.environ", {"TARGET_TIMEZONE": "UTC"}, clear=False):
            start_ts, duration = token_engine.get_hkt_window("2026-05-22")

        self.assertEqual(start_ts, datetime(2026, 5, 22, 4, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(duration, 86400)

    def test_cron_adapter_discovers_migrated_jsonl_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(FIXTURES / "cron_usage.jsonl", root / "run.jsonl.migrated")
            adapter = CronAdapter(root)
            artifacts = list(adapter.discover_sources())
            self.assertEqual([artifact.path.name for artifact in artifacts], ["run.jsonl.migrated"])

    def test_observation_records_only_non_rag_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "legacy"
            (legacy / "__diary_daily").mkdir(parents=True)
            (legacy / "__diary_daily" / "data.jsonl").write_text("{}\n", encoding="utf-8")
            (legacy / "__diary_rag").mkdir()
            (legacy / "__diary_rag" / "index.jsonl").write_text("excluded\n", encoding="utf-8")
            openclaw = root / ".openclaw"
            agents = openclaw / "agents"
            (agents / "main" / "memory").mkdir(parents=True)
            (agents / "main" / "memory" / "memory.jsonl").write_text("{}\n", encoding="utf-8")
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=legacy)
            from data_foundation.jobs import begin_ingestion_run
            from data_foundation.observations import observe_non_rag_assets

            migrate(paths)
            run_id = begin_ingestion_run(paths, trigger_type="fixture", business_date=date(2026, 5, 19))
            observe_non_rag_assets(
                paths,
                date(2026, 5, 19),
                run_id,
                openclaw_root=openclaw,
                tool_homes={"openclaw": openclaw},
                workspace_root=root / "workspace",
            )
            with connect(paths, read_only=True) as connection:
                observations = connection.execute("SELECT asset_key, details_json FROM asset_observations").fetchall()
            self.assertTrue(
                {"openclaw_memory", "legacy_daily_archive", "skill_inventory", "configured_tools"}
                <= {row["asset_key"] for row in observations}
            )
            self.assertNotIn("dashboard_inventory", {row["asset_key"] for row in observations})
            self.assertNotIn("__diary_rag", "".join(row["details_json"] for row in observations))
            self.assertTrue(observations)

    def test_period_ingestion_scans_sources_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary")
            start = date(2026, 5, 18)
            end = date(2026, 5, 19)
            result = run_shadow_period_ingestion(paths, start, end, adapters=self._adapters(root), observe_assets=False)
            self.assertEqual(result.artifacts_seen, 6)
            self.assertEqual(daily_tool_totals(paths, start)["openclaw"]["tokens"], 297)
            self.assertEqual(daily_tool_totals(paths, end)["codex"]["tokens"], 15)

    def test_gemini_zero_token_message_is_kept_for_legacy_message_parity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary")
            chat_root = root / "gemini"
            chat_root.mkdir()
            (chat_root / "session-zero.jsonl").write_text(
                '{"id":"zero","timestamp":"2026-05-19T02:00:00Z","type":"gemini","model":"gemini-model"}\n',
                encoding="utf-8",
            )
            result = run_shadow_ingestion(
                paths,
                date(2026, 5, 19),
                adapters=(GeminiCliAdapter(chat_root),),
                observe_assets=False,
            )
            totals = daily_tool_totals(paths, date(2026, 5, 19))
            self.assertEqual(result.events_in_window, 1)
            self.assertEqual(totals["gemini-cli"]["messages"], 1)
            self.assertEqual(totals["gemini-cli"]["tokens"], 0)


if __name__ == "__main__":
    unittest.main()

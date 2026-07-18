import os
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.services import ai_assets
from data_foundation.db import migrate
from data_foundation.jobs import begin_ingestion_run
from data_foundation.paths import initialize_home, update_runtime_manifest_paths
from data_foundation.settings import write_settings
from data_foundation.snapshots import write_dashboard_snapshot


def _write_bytes(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


class DashboardAiAssetsFilePolicyTests(unittest.TestCase):
    def test_file_policy_uses_configured_external_tool_home_without_unblocking_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            codex_home = root / "configured-tools" / "codex"
            codex_home.mkdir(parents=True)
            readable = codex_home / "AGENTS.md"
            readable.write_text("context\n", encoding="utf-8")
            blocked = codex_home / "session.jsonl"
            blocked.write_text("{}\n", encoding="utf-8")
            write_settings({"externalTools": {"codex": {"home": str(codex_home)}}}, paths)

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                ok = ai_assets.read_file_content(str(readable))
                denied = ai_assets.read_file_content(str(blocked))

        self.assertEqual(ok["content"], "context\n")
        self.assertEqual(denied["status"], 403)

    def test_file_policy_rejects_sibling_path_prefix_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            codex_home = root / "configured-tools" / "codex"
            sibling = root / "configured-tools" / "codex-evil"
            codex_home.mkdir(parents=True)
            sibling.mkdir(parents=True)
            escaped = sibling / "AGENTS.md"
            escaped.write_text("escaped\n", encoding="utf-8")
            write_settings({"externalTools": {"codex": {"home": str(codex_home)}}}, paths)

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                denied = ai_assets.read_file_content(str(escaped))

        self.assertEqual(denied["status"], 403)
        self.assertEqual(denied["error"], "Path not in whitelist")

    def test_ai_assets_key_file_cards_use_configured_external_tool_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            codex_home = root / "configured-tools" / "codex"
            codex_home.mkdir(parents=True)
            (codex_home / "AGENTS.md").write_text("context\n", encoding="utf-8")
            write_settings({"externalTools": {"codex": {"home": str(codex_home)}}}, paths)
            tool_stats = [
                {"name": "Codex", "emoji": "🤖", "sessionCount": 0, "allTimeMessages": 0, "lastActivity": ""},
            ]

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                agents = ai_assets._get_agents_enhanced(tool_stats)
                tree = ai_assets._get_agent_tree(tool_stats)

        codex_agent = next(item for item in agents if item["name"] == "Codex")
        self.assertEqual(codex_agent["workspace"], str(codex_home.absolute()))
        self.assertTrue(codex_agent["documents"]["AGENTS.md"]["exists"])
        codex_tree = next(item for item in tree if item["name"] == "Codex")
        global_item = next(item for item in codex_tree["items"] if item["name"] == "__global__")
        self.assertEqual(global_item["workspace"], str(codex_home.absolute()))
        self.assertTrue(next(item for item in global_item["keyFiles"] if item["name"] == "AGENTS.md")["exists"])

    def test_ai_assets_skills_inventory_uses_configured_codex_skills_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            codex_skills = root / "configured-tools" / "codex" / "skills"
            _write_skill(codex_skills / "global-one", "Codex configured skill")
            write_settings({"externalTools": {"codex": {"skillsRoot": str(codex_skills)}}}, paths)

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                payload = ai_assets._get_skills_stats()

        codex = payload["byTool"]["Codex"]
        self.assertTrue(any(item["id"] == "global-one" for item in codex))

    def test_ai_assets_display_labels_follow_english_profile_without_changing_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            diary_root = Path(tmp) / "Diary"
            diary_root.mkdir()
            with (
                patch.object(ai_assets, "dashboard_language_profile", return_value="en"),
                patch.object(ai_assets, "_diary_dir", return_value=diary_root),
                patch.object(ai_assets, "_rag_index_path", return_value=None),
                patch.object(ai_assets.urllib.request, "urlopen", side_effect=ai_assets.urllib.error.URLError("offline")),
            ):
                storage = ai_assets._get_detailed_storage()
                rag = ai_assets._get_rag_stats()
                level_label = ai_assets._skill_level_label("global")
                source_label = ai_assets._skill_source_kind_label("plugin")

        self.assertEqual([item["label"] for item in storage["categories"]], [
            "Diary Documents",
            "Archive / Processing Intermediates",
            "Historical Archive",
            "RAG Index",
        ])
        self.assertEqual(rag["embeddingStatus"], "Stopped")
        self.assertEqual(rag["health"], "missing")
        self.assertFalse(rag["indexReady"])
        self.assertFalse(rag["embeddingRunning"])
        self.assertEqual(rag["source"], "v2")
        self.assertEqual(level_label, "Global")
        self.assertEqual(source_label, "Plugin")

    def test_ai_assets_rag_health_reports_index_only_without_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = Path(tmp) / "rag-index.jsonl"
            index_path.write_text("{}\n{}\n", encoding="utf-8")
            with (
                patch.object(ai_assets, "dashboard_language_profile", return_value="zh"),
                patch.object(ai_assets, "_rag_index_path", return_value=index_path),
                patch.object(ai_assets.urllib.request, "urlopen", side_effect=ai_assets.urllib.error.URLError("offline")),
            ):
                rag = ai_assets._get_rag_stats()

        self.assertEqual(rag["health"], "index-only")
        self.assertTrue(rag["indexReady"])
        self.assertFalse(rag["embeddingRunning"])
        self.assertEqual(rag["entries"], 2)

    def test_ai_assets_embedding_health_url_falls_back_to_rag_defaults(self):
        with patch.object(ai_assets, "resolve_rag_settings", side_effect=RuntimeError("settings unavailable")):
            url = ai_assets._embedding_health_url()

        self.assertEqual(url, "http://127.0.0.1:3037/health")

    def test_ai_assets_display_labels_keep_chinese_profile_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            diary_root = Path(tmp) / "Diary"
            diary_root.mkdir()
            with (
                patch.object(ai_assets, "dashboard_language_profile", return_value="zh"),
                patch.object(ai_assets, "_diary_dir", return_value=diary_root),
                patch.object(ai_assets, "_rag_index_path", return_value=None),
            ):
                storage = ai_assets._get_detailed_storage()

        self.assertEqual([item["label"] for item in storage["categories"]], [
            "正式日记",
            "归档 / 清洗中间产物",
            "历史归档",
            "RAG 索引",
        ])

    def test_ai_assets_tool_storage_uses_configured_tool_home_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            tool_root = root / "tools"
            openclaw_home = tool_root / "openclaw"
            codex_home = tool_root / "codex"
            _write_bytes(openclaw_home / "state.bin", 128 * 1024)
            _write_bytes(codex_home / "sessions" / "one.jsonl", 384 * 1024)
            write_settings(
                {
                    "externalTools": {
                        "openclaw": {"home": str(openclaw_home)},
                        "claudeCode": {"home": str(tool_root / "claude")},
                        "geminiCli": {"home": str(tool_root / "gemini")},
                        "codex": {"home": str(codex_home)},
                        "hermes": {"home": str(tool_root / "hermes")},
                    }
                },
                paths,
            )

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                storage = ai_assets._get_detailed_storage(include_rag=False)

        by_tool = {item["name"]: item for item in storage["tools"]}
        self.assertEqual(by_tool["OpenClaw"]["sizeMB"], 0.1)
        self.assertEqual(by_tool["Codex"]["sizeMB"], 0.4)
        self.assertEqual(by_tool["Claude Code"]["sizeMB"], 0)
        self.assertEqual(by_tool["Gemini CLI"]["sizeMB"], 0)
        self.assertEqual(by_tool["Hermes"]["sizeMB"], 0)

    def test_ai_assets_actanara_storage_follows_runtime_paths_and_rag_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "ConfiguredDiary"
            archives_root = root / "ConfiguredArchives"
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=root / "LegacyDiary").home,
                generated_diary_root=diary_root,
                legacy_diary_root=root / "LegacyDiary",
                archives_root=archives_root,
            )
            _write_bytes(diary_root / "diary-2026" / "diary-2026-06" / "06-07" / "日记-260607.md", 320 * 1024)
            _write_bytes(diary_root / "__diary_daily" / "2026-06-07" / "_filtered" / "session.jsonl", 256 * 1024)
            _write_bytes(archives_root / "2026-06-07" / "raw" / "tool" / "session.jsonl", 384 * 1024)
            _write_bytes(diary_root / "_archive" / "old.md", 128 * 1024)
            rag_store = paths.home / "reserved" / "rag" / "v2"
            active_dir = rag_store / "indexes" / "active" / "run-current"
            candidate_dir = rag_store / "indexes" / "candidates" / "run-candidate"
            _write_bytes(active_dir / "index.jsonl", 384 * 1024)
            _write_bytes(candidate_dir / "index.jsonl", 256 * 1024)
            (rag_store / "manifest.json").write_text(
                json.dumps({"status": "active", "activeRunId": "run-current", "activeIndexPath": str(active_dir)}),
                encoding="utf-8",
            )
            write_settings({"rag": {"enabled": True, "mode": "v2", "v2": {"storePath": str(rag_store)}}}, paths)

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                storage = ai_assets._get_detailed_storage(include_rag=True)

        by_label = {item["label"]: item for item in storage["categories"]}
        self.assertEqual(by_label["正式日记"]["paths"], [str(diary_root.absolute())])
        self.assertEqual(by_label["正式日记"]["sizeMB"], 0.3)
        self.assertEqual(
            by_label["归档 / 清洗中间产物"]["paths"],
            [str((diary_root / "__diary_daily").absolute()), str(archives_root.absolute())],
        )
        self.assertEqual(by_label["归档 / 清洗中间产物"]["sizeMB"], 0.6)
        self.assertEqual(by_label["历史归档"]["paths"], [str((diary_root / "_archive").absolute())])
        self.assertEqual(by_label["历史归档"]["sizeMB"], 0.1)
        self.assertEqual(by_label["RAG 索引"]["paths"], [str((rag_store / "indexes").absolute())])
        self.assertEqual(by_label["RAG 索引"]["sizeMB"], 0.6)

    def test_ai_assets_file_save_message_uses_profile_text_without_changing_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "note.md"
            with (
                patch.object(ai_assets, "_validate_file_path", return_value=(True, "")),
                patch.object(ai_assets, "dashboard_language_profile", return_value="en"),
            ):
                result = ai_assets.write_file_content(
                    str(target),
                    "content\n",
                    confirmation_text=ai_assets.FILE_WRITE_CONFIRMATION,
                )

        self.assertTrue(result["success"])
        self.assertEqual(result["message"], "File saved")
        self.assertEqual(result["path"], str(target))
        self.assertIn("size", result)

    def test_ai_assets_workspace_and_cache_key_follow_dashboard_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            project_a = root / "ProjectA"
            project_b = root / "ProjectB"
            project_a.mkdir()
            project_b.mkdir()
            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                write_settings({"dashboard": {"projectRoot": str(project_a)}}, paths)
                first_key = ai_assets._ai_assets_cache_key("foundation")
                self.assertEqual(ai_assets._workspace_dir(), project_a.absolute())

                write_settings({"dashboard": {"projectRoot": str(project_b)}}, paths)
                second_key = ai_assets._ai_assets_cache_key("foundation")
                self.assertEqual(ai_assets._workspace_dir(), project_b.absolute())

        self.assertNotEqual(first_key["projectRoot"], second_key["projectRoot"])

    def test_ai_assets_tool_config_snapshot_uses_configured_openclaw_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            snapshot = root / "configured-tools" / "openclaw" / "workspace" / ".dashboard-tool-configs.json"
            write_settings({"externalTools": {"openclaw": {"toolConfigSnapshotPath": str(snapshot)}}}, paths)

            ai_assets.TOOL_CONFIG_SNAPSHOT = ai_assets._DEFAULT_TOOL_CONFIG_SNAPSHOT
            with (
                patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}),
                patch.object(ai_assets, "_find_tool_executable", return_value=None),
            ):
                self.assertEqual(ai_assets._tool_config_snapshot_path(), snapshot.absolute())
                configs = ai_assets.discover_tool_configs(persist=False)

        self.assertIsInstance(configs, list)

    def test_foundation_ai_assets_uses_static_snapshot_without_live_path_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "ConfiguredDiary"
            day = diary_root / "diary-2026-06-07"
            day.mkdir(parents=True)
            (day / "日记-260607.md").write_text("one two three\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(initialize_home(root / "Actanara", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)
            run_id = begin_ingestion_run(paths, trigger_type="fixture", business_date=date(2026, 6, 7))
            write_dashboard_snapshot(
                paths,
                {
                    "diary": {"count": 1, "firstDate": "2026-05-14", "lastDate": "2026-05-14", "totalWords": 1},
                    "memory": {"sessionFiles": 999, "totalSizeMB": 999, "diaryCount": 999, "dailyNoteCount": 999},
                    "storage": {"tools": [], "categories": [{"label": "正式日记", "sizeMB": 0}]},
                    "tools": [],
                },
                source_run_id=run_id,
            )

            ai_assets._cache = {"data": None, "ts": 0}
            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                payload = ai_assets._get_ai_assets_foundation()

        self.assertEqual(payload["diary"]["count"], 1)
        self.assertEqual(payload["diary"]["firstDate"], "2026-05-14")
        self.assertEqual(payload["diary"]["lastDate"], "2026-05-14")
        self.assertEqual(payload["memory"]["sessionFiles"], 999)
        self.assertTrue(payload["dataFreshness"]["aiAssets"]["staticSnapshotOnly"])
        self.assertEqual(payload["dataFreshness"]["aiAssets"]["diaryRoot"], str(diary_root.absolute()))

    def test_cached_ai_assets_does_not_fallback_to_live_scanner_for_retired_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            ai_assets._cache = {"data": None, "ts": 0}
            foundation_payload = {
                "dataFreshness": {"aiAssets": {"source": "foundation", "staticSnapshotOnly": True}},
                "tools": [],
            }

            with (
                patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}),
                patch.object(ai_assets, "resolve_runtime_source", return_value="legacy"),
                patch.object(ai_assets, "_get_ai_assets_foundation", return_value=foundation_payload),
                patch.object(ai_assets, "get_ai_assets", side_effect=AssertionError("legacy scanner called")),
            ):
                payload = ai_assets.get_ai_assets_cached()

        self.assertEqual(payload["dataFreshness"]["aiAssets"]["source"], "foundation")
        self.assertEqual(payload["dataFreshness"]["aiAssets"]["retiredSourceRequested"], "legacy")

    def test_file_content_write_requires_confirmation_and_backs_up_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            codex_home = root / "configured-tools" / "codex"
            codex_home.mkdir(parents=True)
            target = codex_home / "AGENTS.md"
            target.write_text("old\n", encoding="utf-8")
            write_settings({"externalTools": {"codex": {"home": str(codex_home)}}}, paths)

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                dry_run = ai_assets.write_file_content(str(target), "new\n", dry_run=True)
                rejected = ai_assets.write_file_content(str(target), "new\n", confirmation_text="wrong")
                written = ai_assets.write_file_content(
                    str(target),
                    "new\n",
                    confirmation_text=ai_assets.FILE_WRITE_CONFIRMATION,
                )

            self.assertTrue(dry_run["dryRun"])
            self.assertTrue(dry_run["wouldBackup"])
            self.assertEqual(dry_run["confirmationTextRequired"], ai_assets.FILE_WRITE_CONFIRMATION)
            self.assertEqual(rejected["status"], 400)
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            backup_path = Path(written["backupPath"])
            self.assertTrue(backup_path.exists())
            self.assertEqual(backup_path.read_text(encoding="utf-8"), "old\n")

    def test_file_policy_rejects_sensitive_files_even_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            workspace = root / "Project"
            workspace.mkdir()
            env_file = workspace / ".env"
            env_file.write_text("TOKEN=secret\n", encoding="utf-8")
            settings_file = workspace / "settings.json"
            settings_file.write_text("{}\n", encoding="utf-8")
            config_file = workspace / "config.toml"
            config_file.write_text("token='secret'\n", encoding="utf-8")
            write_settings({"dashboard": {"projectRoot": str(workspace)}}, paths)

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                denied_env = ai_assets.read_file_content(str(env_file))
                denied_settings = ai_assets.read_file_content(str(settings_file))
                denied_config = ai_assets.read_file_content(str(config_file))

        self.assertEqual(denied_env["status"], 403)
        self.assertEqual(denied_env["error"], "Sensitive file not allowed")
        self.assertEqual(denied_settings["status"], 403)
        self.assertEqual(denied_settings["error"], "Sensitive file not allowed")
        self.assertEqual(denied_config["status"], 403)
        self.assertEqual(denied_config["error"], "Sensitive file not allowed")

    def test_file_policy_rejects_hidden_subpaths_and_large_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            workspace = root / "Project"
            workspace.mkdir()
            hidden_dir = workspace / ".secret"
            hidden_dir.mkdir(parents=True)
            hidden_doc = hidden_dir / "note.md"
            hidden_doc.write_text("secret\n", encoding="utf-8")
            large_doc = workspace / "large.md"
            large_doc.write_text("x" * (ai_assets.MAX_FILE_CONTENT_BYTES + 1), encoding="utf-8")
            write_settings({"dashboard": {"projectRoot": str(workspace)}}, paths)

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                hidden = ai_assets.read_file_content(str(hidden_doc))
                large = ai_assets.read_file_content(str(large_doc))

        self.assertEqual(hidden["status"], 403)
        self.assertEqual(hidden["error"], "Hidden paths are not allowed")
        self.assertEqual(large["status"], 403)
        self.assertIn("byte limit", large["error"])

    def test_file_policy_allows_workspace_markdown_without_parent_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            workspace = root / "Project"
            workspace.mkdir()
            allowed = workspace / "README.md"
            allowed.write_text("ok\n", encoding="utf-8")
            parent = root / "README.md"
            parent.write_text("outside\n", encoding="utf-8")
            write_settings({"dashboard": {"projectRoot": str(workspace)}}, paths)

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                ok = ai_assets.read_file_content(str(allowed))
                denied = ai_assets.read_file_content(str(parent))

        self.assertEqual(ok["content"], "ok\n")
        self.assertEqual(denied["status"], 403)
        self.assertEqual(denied["error"], "Path not in whitelist")

def _write_skill(path: Path, description: str) -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\ndescription: {description}\n---\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

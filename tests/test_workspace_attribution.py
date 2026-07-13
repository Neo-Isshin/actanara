import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.db import connect, migrate
from data_foundation.paths import initialize_home
from data_foundation.usage_attribution import resolve_usage_group
from data_foundation.workspace_attribution import (
    add_workspace_attribution_rule,
    attribute_workspace_path,
    build_workspace_attribution_catalog,
    clear_workspace_attribution_caches,
    infer_workspace_name_from_text,
    materialize_workspace_attribution_catalog,
    read_workspace_attribution_rules,
    workspace_attribution_catalog_path,
    workspace_display_name,
    workspace_usage_display_allowed,
    write_workspace_attribution_rules,
)


class WorkspaceAttributionTests(unittest.TestCase):
    def tearDown(self):
        clear_workspace_attribution_caches()

    def test_workspace_name_uses_project_marker_without_fixed_parent_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "custom" / "deep" / "project-source"
            nested = project / "src" / "pkg"
            nested.mkdir(parents=True)
            (project / "pyproject.toml").write_text('[project]\nname = "real-project"\n', encoding="utf-8")

            attribution = attribute_workspace_path(nested / "module.py")
            display_name = workspace_display_name(project)

        self.assertIsNotNone(attribution)
        self.assertEqual(attribution.display_name, "real-project")
        self.assertEqual(attribution.root_path, str(project))
        self.assertEqual(display_name, "real-project")

    def test_text_inference_extracts_existing_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "work" / "nova"
            file_path = project / "src" / "app.py"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("print('x')\n", encoding="utf-8")
            (project / "package.json").write_text(json.dumps({"name": "nova-app"}), encoding="utf-8")

            name = infer_workspace_name_from_text(f"edited {file_path}:12 and reviewed logs")

        self.assertEqual(name, "nova-app")

    def test_text_inference_ignores_unmarked_paths_and_globs(self):
        with tempfile.TemporaryDirectory() as tmp:
            loose_dir = Path(tmp) / "Diary"
            loose_dir.mkdir()
            (loose_dir / "note.md").write_text("x\n", encoding="utf-8")

            name = infer_workspace_name_from_text(f"scan {loose_dir}/*.md\\ and {loose_dir / 'note.md'}")

        self.assertIsNone(name)

    def test_catalog_materializes_from_structured_session_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "work" / "cataloged"
            project.mkdir(parents=True)
            (project / ".git").mkdir()
            paths = initialize_home(root / "NovaDiary")
            migrate(paths)
            with connect(paths) as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO tool_sources(
                        tool_key, display_name, adapter_version, capabilities_json,
                        enabled, created_at, updated_at
                    ) VALUES ('codex', 'Codex', 'test', '{}', 1, '2026-05-19T00:00:00+08:00', '2026-05-19T00:00:00+08:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO sessions(tool_key, external_session_key, started_at, last_active_at, initial_cwd, metadata_json)
                    VALUES ('codex', 's1', '2026-05-19T00:00:00+08:00', '2026-05-19T00:01:00+08:00', ?, '{}')
                    """,
                    (str(project),),
                )

            catalog = materialize_workspace_attribution_catalog(paths)
            loaded = json.loads(workspace_attribution_catalog_path(paths).read_text(encoding="utf-8"))

        cataloged = next(project for project in loaded["projects"] if project["display_name"] == "cataloged")
        self.assertGreaterEqual(catalog["counts"]["projects"], 1)
        self.assertEqual(cataloged["root_path"], str(project))
        self.assertEqual(cataloged["sources"], ["codex"])

    def test_workspace_usage_display_policy_filters_infrastructure_catalog(self):
        hidden = ["nvm", "homebrew", "memories", ".opencode", ".codex", ".cache", "node_modules", "Library"]
        for name in hidden:
            with self.subTest(name=name):
                self.assertFalse(workspace_usage_display_allowed(name))

        self.assertTrue(workspace_usage_display_allowed("open-nova"))
        self.assertTrue(workspace_usage_display_allowed("homebrew", project_marker_confirmed=True))

    def test_usage_group_resolver_prefers_openclaw_agent_path(self):
        resolved = resolve_usage_group(
            "openclaw",
            raw_path="/Users/example/.openclaw/agents/research-agent/sessions/session.jsonl",
        )

        self.assertEqual(resolved.group, "research-agent")
        self.assertEqual(resolved.confidence, "high")
        self.assertEqual(resolved.source, "openclaw-agent-path")

    def test_usage_group_resolver_uses_codex_cwd_project_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "work" / "open-nova"
            nested = project / "src"
            nested.mkdir(parents=True)
            (project / "pyproject.toml").write_text('[project]\nname = "open-nova"\n', encoding="utf-8")

            resolved = resolve_usage_group("codex", cwd=str(nested))

        self.assertEqual(resolved.group, "open-nova")
        self.assertEqual(resolved.confidence, "high")
        self.assertEqual(resolved.source, "cwd")

    def test_usage_group_resolver_uses_codex_transcript_when_cwd_is_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "work" / "open-nova"
            project.mkdir(parents=True)
            (project / "pyproject.toml").write_text('[project]\nname = "open-nova"\n', encoding="utf-8")
            session = root / ".codex" / "sessions" / "rollout-test.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text(
                '{"type":"event_msg","payload":{"type":"user_message","message":"继续 open-nova，项目目录：'
                + str(project)
                + '"}}\n',
                encoding="utf-8",
            )

            resolved = resolve_usage_group("codex", raw_path=str(session), cwd=str(Path.home()))

        self.assertEqual(resolved.group, "open-nova")
        self.assertEqual(resolved.confidence, "high")
        self.assertEqual(resolved.source, "codex-transcript-path")

    def test_legacy_nova_diary_v2_name_is_canonicalized_to_open_nova(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "nova-diary-v2"
            project.mkdir()
            (project / "pyproject.toml").write_text('[project]\nname = "nova-diary-v2"\n', encoding="utf-8")

            self.assertEqual(workspace_display_name(project), "open-nova")

    def test_user_workspace_attribution_rules_apply_to_paths_aliases_and_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary")
            project = root / "work" / "TokenClock"
            nested = project / "src"
            nested.mkdir(parents=True)
            (project / "package.json").write_text(json.dumps({"name": "TokenClock"}), encoding="utf-8")

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                clear_workspace_attribution_caches()
                preview = add_workspace_attribution_rule(
                    {"type": "path", "tool": "gemini-cli", "workspacePath": str(project)},
                    paths,
                    dry_run=True,
                )
                result = add_workspace_attribution_rule(
                    {"type": "path", "tool": "gemini-cli", "workspacePath": str(project)},
                    paths,
                    dry_run=False,
                )
                write_workspace_attribution_rules(
                    paths,
                    {
                        "rules": [
                            *read_workspace_attribution_rules(paths)["rules"],
                            {"type": "alias", "source": "TokenClock-normal", "target": "TokenClock"},
                            {"type": "container", "name": "tmp_workspace"},
                        ]
                    },
                )
                clear_workspace_attribution_caches()
                attribution = attribute_workspace_path(nested / "app.ts")
                catalog = build_workspace_attribution_catalog(paths)

        self.assertTrue(preview["dryRun"])
        self.assertFalse(result["dryRun"])
        self.assertEqual(attribution.display_name, "TokenClock")
        self.assertEqual(attribution.evidence, "user-path-rule")
        self.assertEqual(workspace_display_name(project), "TokenClock")
        self.assertFalse(workspace_usage_display_allowed("tmp_workspace"))
        self.assertIn("TokenClock", {item["display_name"] for item in catalog["projects"]})

    def test_usage_group_resolver_decodes_claude_project_segment(self):
        # Claude's slash-to-hyphen encoding is lossy when a parent component
        # itself contains a hyphen. Use a neutral, hyphen-free system parent so
        # this test exercises the exact-decode/high-confidence contract.
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            project = root / "Volumes" / "Example" / "work" / "SampleProject"
            project.mkdir(parents=True)
            (project / ".git").mkdir()
            encoded = "-" + str(project).lstrip("/").replace("/", "-")
            raw_path = root / ".claude" / "projects" / encoded / "session.jsonl"

            resolved = resolve_usage_group("claude-code", raw_path=str(raw_path))

        self.assertEqual(resolved.group, "SampleProject")
        self.assertEqual(resolved.confidence, "high")

    def test_usage_group_resolver_uses_claude_transcript_when_project_segment_is_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            container = root / "Volumes" / "Example"
            project = container / "work" / "SampleProject"
            project.mkdir(parents=True)
            (project / "package.json").write_text(json.dumps({"name": "SampleProject"}), encoding="utf-8")
            session = root / ".claude" / "projects" / "-Volumes-Example" / "session.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text(
                json.dumps({"type": "user", "message": f"review {project / 'src' / 'app.ts'}"}) + "\n",
                encoding="utf-8",
            )

            resolved = resolve_usage_group("claude-code", raw_path=str(session), cwd=str(container))

        self.assertEqual(resolved.group, "SampleProject")
        self.assertEqual(resolved.confidence, "high")
        self.assertEqual(resolved.source, "claude-transcript-path")

    def test_usage_group_resolver_prefers_claude_transcript_over_low_confidence_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            container = root / "Users" / "operator" / "work"
            project = container / "SampleProject"
            project.mkdir(parents=True)
            (project / "package.json").write_text(json.dumps({"name": "SampleProject"}), encoding="utf-8")
            session = root / ".claude" / "projects" / "-Users-operator-work" / "session.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text(
                json.dumps({"type": "user", "message": f"edited {project / 'Sources' / 'SampleProject' / 'AppDelegate.swift'}"}) + "\n",
                encoding="utf-8",
            )

            resolved = resolve_usage_group("claude-code", raw_path=str(session), cwd=str(container))

        self.assertEqual(resolved.group, "SampleProject")
        self.assertEqual(resolved.confidence, "high")
        self.assertEqual(resolved.source, "claude-transcript-path")

    def test_usage_group_resolver_uses_gemini_transcript_when_fallback_is_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "Volumes" / "Example" / "work" / "open-nova"
            project.mkdir(parents=True)
            (project / "pyproject.toml").write_text('[project]\nname = "open-nova"\n', encoding="utf-8")
            session = root / ".gemini" / "tmp" / "ssd" / "chats" / "session-test.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text(
                json.dumps({"type": "user", "content": f"inspect {project / 'src' / 'app.py'}"}) + "\n",
                encoding="utf-8",
            )

            resolved = resolve_usage_group("gemini-cli", raw_path=str(session), fallback="Example")

        self.assertEqual(resolved.group, "open-nova")
        self.assertEqual(resolved.confidence, "high")
        self.assertEqual(resolved.source, "gemini-transcript-path")


if __name__ == "__main__":
    unittest.main()

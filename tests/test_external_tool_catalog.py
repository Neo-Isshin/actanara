import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.external_tool_catalog import (
    add_external_tool_instance,
    rediscover_external_tools,
    supported_external_tool_catalog,
)
from data_foundation.paths import initialize_home
from data_foundation.settings import default_external_tool_settings, read_settings, write_settings


class ExternalToolCatalogTests(unittest.TestCase):
    def test_catalog_documents_supported_fields_and_skill_registration(self):
        catalog = supported_external_tool_catalog()
        by_id = {item["id"]: item for item in catalog["tools"]}

        self.assertIn("openclaw", by_id)
        self.assertIn("agentsRoot", by_id["openclaw"]["fields"])
        self.assertIn("~/.openclaw-*", by_id["openclaw"]["homeCandidates"])
        self.assertIn("globalSkillRegistration", by_id["codex"])
        for definition in by_id.values():
            fields = set(definition["fields"])
            for target in definition["globalSkillRegistration"]["targets"]:
                self.assertIn(target, fields)
        self.assertEqual(by_id["hermes"]["globalSkillRegistration"]["targets"], ["skillsRoot"])
        self.assertNotIn("optionalSkillsRoot", by_id["hermes"]["globalSkillRegistration"]["targets"])

    def test_catalog_fields_match_settings_defaults(self):
        home = Path("/Users/example")
        defaults = default_external_tool_settings(home)
        catalog = supported_external_tool_catalog()
        by_id = {item["id"]: item for item in catalog["tools"]}

        self.assertEqual(set(defaults), set(by_id))
        for tool_id, values in defaults.items():
            self.assertEqual(set(values), set(by_id[tool_id]["fields"]))

    def test_rediscover_names_second_openclaw_instance_without_overwriting_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            primary = home / ".openclaw"
            secondary = home / ".openclaw-work"
            (primary / "agents").mkdir(parents=True)
            (primary / "config.json").write_text("{}\n", encoding="utf-8")
            (secondary / "agents").mkdir(parents=True)
            (secondary / "config.json").write_text("{}\n", encoding="utf-8")
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            write_settings({"externalTools": {"openclaw": {"home": str(primary)}}}, paths)

            with patch("data_foundation.external_tool_catalog.Path.home", return_value=home):
                result = rediscover_external_tools(paths)

        discoveries = {item["path"]: item for item in result["discoveries"]}
        self.assertEqual(discoveries[str(primary.absolute())]["status"], "unchanged")
        self.assertEqual(discoveries[str(secondary.absolute())]["instanceId"], "openclaw-2")
        self.assertIn("openclaw-2", result["suggestedUpdates"])

    def test_rediscover_uses_catalog_home_candidates_for_supported_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            codex = home / ".codex"
            gemini = home / ".gemini"
            hermes = home / ".hermes"
            (codex / "sessions").mkdir(parents=True)
            (gemini / "tmp").mkdir(parents=True)
            (hermes / "profiles").mkdir(parents=True)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")

            with patch("data_foundation.external_tool_catalog.Path.home", return_value=home):
                result = rediscover_external_tools(paths)

        by_tool = {item["tool"]: item for item in result["discoveries"]}
        self.assertEqual(by_tool["codex"]["status"], "unchanged")
        self.assertEqual(by_tool["codex"]["update"]["sessionsRoot"], str(codex.absolute() / "sessions"))
        self.assertEqual(by_tool["geminiCli"]["status"], "unchanged")
        self.assertEqual(by_tool["geminiCli"]["update"]["chatsRoot"], str(gemini.absolute() / "tmp" / "ssd" / "chats"))
        self.assertEqual(by_tool["hermes"]["status"], "unchanged")
        self.assertEqual(by_tool["hermes"]["update"]["stateDbPath"], str(hermes.absolute() / "state.db"))

    def test_add_external_tool_instance_persists_derived_paths_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            tool_home = root / "codex-alt"
            tool_home.mkdir()

            result = add_external_tool_instance("codex", str(tool_home), paths, instance_id="codex-alt")
            settings = read_settings(paths)

        self.assertEqual(result["added"], "codex-alt")
        self.assertEqual(settings["externalTools"]["codex-alt"]["home"], str(tool_home.absolute()))
        self.assertEqual(settings["externalTools"]["codex-alt"]["sessionsRoot"], str(tool_home.absolute() / "sessions"))
        self.assertEqual(settings["externalTools"]["codex-alt"]["configPath"], str(tool_home.absolute() / "config.toml"))

    def test_add_external_tool_instance_persists_all_catalog_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            tool_home = root / "hermes-alt"
            tool_home.mkdir()

            result = add_external_tool_instance("hermes", str(tool_home), paths, instance_id="hermes-alt")
            settings = read_settings(paths)

        self.assertEqual(result["added"], "hermes-alt")
        fields = settings["externalTools"]["hermes-alt"]
        self.assertEqual(fields["optionalSkillsRoot"], str(tool_home.absolute() / "hermes-agent" / "optional-skills"))
        self.assertEqual(fields["pluginsRoot"], str(tool_home.absolute() / "hermes-agent" / "plugins"))
        self.assertEqual(fields["configPath"], str(tool_home.absolute() / "config.yaml"))


if __name__ == "__main__":
    unittest.main()

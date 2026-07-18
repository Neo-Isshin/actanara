import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from diary_generator import language_profile, learning_pass, narrative_pass, technical_pass

EN_NARRATIVE_SPEC = importlib.util.spec_from_file_location(
    "english_narrative_payload",
    ROOT / "src" / "diary_generator" / "en" / "narrative_payload.py",
)
english_narrative_payload = importlib.util.module_from_spec(EN_NARRATIVE_SPEC)
assert EN_NARRATIVE_SPEC.loader is not None
EN_NARRATIVE_SPEC.loader.exec_module(english_narrative_payload)

EN_NARRATIVE_PASS_SPEC = importlib.util.spec_from_file_location(
    "english_narrative_pass",
    ROOT / "src" / "diary_generator" / "en" / "narrative_pass.py",
)
english_narrative_pass = importlib.util.module_from_spec(EN_NARRATIVE_PASS_SPEC)
assert EN_NARRATIVE_PASS_SPEC.loader is not None
EN_NARRATIVE_PASS_SPEC.loader.exec_module(english_narrative_pass)

EN_TECHNICAL_SPEC = importlib.util.spec_from_file_location(
    "english_technical_payload",
    ROOT / "src" / "diary_generator" / "en" / "technical_payload.py",
)
english_technical_payload = importlib.util.module_from_spec(EN_TECHNICAL_SPEC)
assert EN_TECHNICAL_SPEC.loader is not None
EN_TECHNICAL_SPEC.loader.exec_module(english_technical_payload)

EN_TECHNICAL_PASS_SPEC = importlib.util.spec_from_file_location(
    "english_technical_pass",
    ROOT / "src" / "diary_generator" / "en" / "technical_pass.py",
)
english_technical_pass = importlib.util.module_from_spec(EN_TECHNICAL_PASS_SPEC)
assert EN_TECHNICAL_PASS_SPEC.loader is not None
EN_TECHNICAL_PASS_SPEC.loader.exec_module(english_technical_pass)

EN_LEARNING_SPEC = importlib.util.spec_from_file_location(
    "english_learning_payload",
    ROOT / "src" / "diary_generator" / "en" / "learning_payload.py",
)
english_learning_payload = importlib.util.module_from_spec(EN_LEARNING_SPEC)
assert EN_LEARNING_SPEC.loader is not None
EN_LEARNING_SPEC.loader.exec_module(english_learning_payload)

EN_LEARNING_PASS_SPEC = importlib.util.spec_from_file_location(
    "english_learning_pass",
    ROOT / "src" / "diary_generator" / "en" / "learning_pass.py",
)
english_learning_pass = importlib.util.module_from_spec(EN_LEARNING_PASS_SPEC)
assert EN_LEARNING_PASS_SPEC.loader is not None
EN_LEARNING_PASS_SPEC.loader.exec_module(english_learning_pass)


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"content":[{"type":"text","text":"ok"}]}'


class PromptPayloadContractTests(unittest.TestCase):
    def test_english_pipeline_sources_do_not_use_retired_artifact_names(self):
        forbidden = ("technical-progress-", "learning-audit-")
        for path in sorted((ROOT / "src" / "diary_generator" / "en").glob("*.py")):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                for token in forbidden:
                    self.assertNotIn(token, source)

    def test_diary_generator_language_profile_env_contract_is_outside_prompt_payloads(self):
        default_profile = language_profile.current_language_profile({})
        self.assertEqual(default_profile.pipeline_language_profile, "zh")
        self.assertEqual(default_profile.diary_schema_version, "diary-v1-zh")
        self.assertEqual(default_profile.prompt_payload_profile, "zh-CN")
        self.assertFalse(default_profile.is_english)

        english_profile = language_profile.current_language_profile(
            {
                "ACTANARA_PIPELINE_LANGUAGE_PROFILE": "en",
                "ACTANARA_DIARY_SCHEMA_VERSION": "diary-v1-en",
                "ACTANARA_PROMPT_PAYLOAD_PROFILE": "en-US",
                "ACTANARA_DISPLAY_LOCALE": "en-US",
                "NOVA_RAG_LANGUAGE_PROFILE": "en",
            }
        )
        self.assertEqual(english_profile.pipeline_language_profile, "en")
        self.assertEqual(english_profile.diary_schema_version, "diary-v1-en")
        self.assertEqual(english_profile.prompt_payload_profile, "en-US")
        self.assertTrue(english_profile.is_english)

    def test_protected_instruction_templates_match_frozen_baseline(self):
        expected = json.loads(
            (ROOT / "tests" / "fixtures" / "phase0" / "prompt-payload-sha256.json").read_text(encoding="utf-8")
        )
        values = {
            "narrative.PROMPT_PARTIAL": narrative_pass.PROMPT_PARTIAL,
            "narrative.PROMPT_INTEGRATION": narrative_pass.PROMPT_INTEGRATION,
            "narrative.system.partial": "你是一个专业的AI技术日记助手。" + narrative_pass._thinking_instruction(),
            "narrative.system.integration": "你是一个专业的技术日记整合助手。直接从'## 今日概要'开始输出。" + narrative_pass._thinking_instruction(),
            "technical.TASK_RULES": technical_pass.TASK_RULES,
            "technical.SYSTEM_PROMPT": technical_pass.SYSTEM_PROMPT,
            "technical.PROMPT_TECHNICAL_PARTIAL": technical_pass.PROMPT_TECHNICAL_PARTIAL,
            "technical.PROMPT_TECHNICAL_INTEGRATION": technical_pass.PROMPT_TECHNICAL_INTEGRATION,
            "learning.PROMPT_LEARNING": learning_pass.PROMPT_LEARNING,
            "learning.system": learning_pass.SYSTEM_LEARNING + learning_pass._thinking_instruction(),
        }
        actual = {key: hashlib.sha256(value.encode("utf-8")).hexdigest() for key, value in values.items()}
        self.assertEqual(actual, expected)

    def _request_payload(self, function, argument):
        captured = {}

        def fake_urlopen(request, **kwargs):
            captured.update(json.loads(request.data.decode("utf-8")))
            return _Response()

        module = sys.modules[function.__module__]
        with (
            patch.object(module, "API_TYPE", "anthropic-messages"),
            patch.object(module, "API_HOST", "https://llm.test"),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            redirect_stdout(io.StringIO()),
        ):
            function(argument)
        return captured

    def test_narrative_payload_keeps_system_instructions_and_user_text(self):
        partial = narrative_pass.PROMPT_PARTIAL.replace("{agent_info}", "fixture-agent").replace(
            "{raw_text}", "fixture-text"
        )
        payload = self._request_payload(narrative_pass.call_llm, partial)
        self.assertEqual(payload["system"], "你是一个专业的AI技术日记助手。" + narrative_pass._thinking_instruction())
        self.assertEqual(payload["messages"][0]["content"], partial)
        integrated = narrative_pass.PROMPT_INTEGRATION.replace("{raw_text}", "fixture-summary")
        with (
            patch.object(narrative_pass, "API_TYPE", "anthropic-messages"),
            patch.object(narrative_pass, "API_HOST", "https://llm.test"),
            patch("urllib.request.urlopen") as urlopen,
            redirect_stdout(io.StringIO()),
        ):
            urlopen.return_value = _Response()
            narrative_pass.call_llm(integrated, True)
            payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(
            payload["system"],
            "你是一个专业的技术日记整合助手。直接从'## 今日概要'开始输出。" + narrative_pass._thinking_instruction(),
        )
        self.assertEqual(payload["messages"][0]["content"], integrated)

    def test_english_narrative_payload_is_isolated_from_chinese_prompt_payload(self):
        partial = english_narrative_payload.partial_prompt(
            "codex",
            [
                {
                    "time": "10:00",
                    "role": "assistant",
                    "content": "Implemented a runtime language gate and added contract tests.",
                }
            ],
        )
        integrated = english_narrative_payload.integration_prompt({"codex": "Runtime language gate completed."})

        self.assertIn("Write in English only", partial)
        self.assertIn("## Daily Overview", integrated)
        self.assertIn("Preserve observed timestamps", partial)
        self.assertIn("Do not infer, normalize, or invent time ranges", integrated)
        self.assertNotIn("今日概要", partial + integrated)
        self.assertNotIn("Agent工作", partial + integrated)
        self.assertEqual(english_narrative_payload.SYSTEM_PARTIAL_EN, "You are a precise AI work-log summarization assistant.")

    def test_english_narrative_fixture_uses_llm_generation_path(self):
        calls = []

        def fake_sender(**kwargs):
            calls.append(kwargs)
            if "Daily Overview" in kwargs["prompt"]:
                return "## Daily Overview\n* **Runtime language gate**: The English profile stayed isolated."
            return "- Runtime language gate implemented with contract coverage."

        fixture = {
            "codex": [
                {
                    "time": "10:00",
                    "role": "assistant",
                    "content": "Implemented pipeline.englishEnabled and kept the Chinese pipeline unchanged.",
                }
            ]
        }
        with (
            patch.object(english_narrative_payload, "API_TYPE", "anthropic-messages"),
            patch.object(english_narrative_payload, "send_anthropic_message", side_effect=fake_sender),
            patch.object(english_narrative_payload, "send_openai_compatible_message") as openai_sender,
            redirect_stdout(io.StringIO()),
        ):
            result = english_narrative_payload.generate_from_entries(fixture)

        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["pipelineLanguageProfile"], "en")
        self.assertEqual(result["pass"], "narrative")
        self.assertIn("Daily Overview", result["markdown"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0]["system"].startswith("You are a precise AI work-log summarization assistant."))
        self.assertTrue(calls[1]["system"].startswith("You are a precise technical diary editor."))
        self.assertIn("pipeline.englishEnabled", calls[0]["prompt"])
        openai_sender.assert_not_called()

    def test_english_narrative_pass_writes_profile_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filtered = root / "__diary_daily" / "2026-05-19" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            (filtered / "unified_daily.jsonl").write_text(
                '{"time":"10:00","role":"assistant","content":"Implemented English narrative."}\n',
                encoding="utf-8",
            )
            with patch.object(
                english_narrative_pass,
                "generate_from_entries",
                return_value={"markdown": "## Daily Overview\n* **English narrative**: generated."},
            ) as generator, patch.object(english_narrative_pass, "fetch_weather_for_date", return_value="Cloudy, 28 C"):
                out_file = english_narrative_pass.write_narrative_report("2026-05-19", root)
                content = out_file.read_text(encoding="utf-8")

        self.assertEqual(out_file.name, "diary-260519.md")
        self.assertIn("## Weather\nCloudy, 28 C", content)
        self.assertIn("Daily Overview", content)
        generator.assert_called_once()
        self.assertIn("codex", generator.call_args.args[0])

    def test_english_narrative_pass_writes_no_activity_filename_without_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(english_narrative_pass, "generate_from_entries") as generator,
                patch.object(english_narrative_pass, "fetch_weather_for_date", return_value="Cloudy, 28 C"),
            ):
                out_file = english_narrative_pass.write_narrative_report("2026-05-19", root)
                content = out_file.read_text(encoding="utf-8")

        self.assertEqual(out_file.name, "diary-260519-no-activity.md")
        self.assertIn("## Weather\nCloudy, 28 C", content)
        self.assertIn("## Daily Overview\nNo activity today.", content)
        self.assertIn('"activityState": "empty"', content)
        generator.assert_not_called()

    def test_technical_payload_keeps_system_and_rendered_template(self):
        partial = technical_pass.PROMPT_TECHNICAL_PARTIAL.format(
            agent_info="agent", raw_text="raw-log", hints="hint-json"
        )
        self.assertIn("agent", partial)
        self.assertIn("raw-log", partial)
        self.assertNotIn("hint-json", partial)
        self.assertNotIn("{raw_text}", partial)
        prompt = (
            technical_pass.PROMPT_TECHNICAL_INTEGRATION.replace("{{date}}", "2026-05-19")
            .replace("{date}", "2026-05-19")
            .replace("{{task_graph_context}}", "graph")
            .replace("{{raw_text}}", "summary")
        )
        payload = self._request_payload(technical_pass.call_llm, prompt)
        self.assertEqual(payload["system"], technical_pass.SYSTEM_PROMPT)
        self.assertEqual(payload["messages"][0]["content"], prompt)
        self.assertIn(technical_pass.TASK_RULES, technical_pass.SYSTEM_PROMPT)
        self.assertIn("Engineering Chronicle", prompt)
        self.assertIn("no_material_technical_progress", prompt)
        self.assertIn("Nova-Task Reconciliation Hooks", prompt)
        self.assertNotIn("```yaml", prompt)
        self.assertNotIn("nova_task:", prompt)
        self.assertNotIn("任务概览 (Task Summary)", prompt)
        self.assertNotIn("项目进展详情 (Project Details)", prompt)
        self.assertIn("graph", prompt)

    def test_english_technical_payload_preserves_chronicle_contract(self):
        partial = english_technical_payload.partial_prompt(
            "codex",
            [
                {
                    "time": "11:05",
                    "role": "assistant",
                    "content": "Added src/diary_generator/en/narrative_payload.py and verified prompt SHA isolation.",
                }
            ],
        )
        integrated = english_technical_payload.integration_prompt(
            "2026-05-19",
            "NT-123 English pipeline adaptation",
            {"codex": "Evidence: src/diary_generator/en/narrative_payload.py changed."},
        )

        self.assertIn("Write in English only", partial)
        self.assertIn("Do not output YAML, JSON, or `nova_task` blocks", partial)
        self.assertIn("Technical Chronicle Contract", english_technical_payload.SYSTEM_TECHNICAL_EN)
        self.assertIn("## Nova-Task Reconciliation Hooks", integrated)
        self.assertIn("no_material_technical_progress", integrated)
        self.assertNotIn("```yaml", integrated)
        self.assertNotIn("nova_task:", integrated)
        self.assertNotIn("candidate_subtasks", integrated)
        self.assertNotIn("技术进展报告", partial + integrated)
        self.assertNotIn("候选", partial + integrated)

    def test_english_technical_fixture_uses_llm_generation_path(self):
        calls = []

        def fake_sender(**kwargs):
            calls.append(kwargs)
            if "Technical Progress Report" in kwargs["prompt"]:
                return (
                    "# 2026-05-19 Technical Progress Report\n\n"
                    "## Engineering Objectives and Outcomes\n- English technical pass dry-run generated evidence.\n\n"
                    "## Obstacles, Root Causes, and Detours\nNone\n\n"
                    "## Implementation Path and Key Decisions\n- Updated src/diary_generator/en/technical_payload.py.\n\n"
                    "## Verification Evidence\n- Fixture generation path executed.\n\n"
                    "## Residual Risks and Follow-up Observation\nNone\n\n"
                    "## Reusable Lessons\nNone\n\n"
                    "## Nova-Task Reconciliation Hooks\nNone\n"
                )
            return "- Evidence packet: changed src/diary_generator/en/technical_payload.py."

        fixture = {
            "codex": [
                {
                    "time": "11:10",
                    "role": "assistant",
                    "content": "Added src/diary_generator/en/technical_payload.py for English Technical dry-run.",
                }
            ]
        }
        with (
            patch.object(english_technical_payload, "API_TYPE", "anthropic-messages"),
            patch.object(english_technical_payload, "send_anthropic_message", side_effect=fake_sender),
            patch.object(english_technical_payload, "send_openai_compatible_message") as openai_sender,
            redirect_stdout(io.StringIO()),
        ):
            result = english_technical_payload.generate_from_entries(
                "2026-05-19",
                fixture,
                "NT-123 English pipeline adaptation",
            )

        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["pipelineLanguageProfile"], "en")
        self.assertEqual(result["pass"], "technical")
        self.assertIn("Nova-Task Reconciliation Hooks", result["markdown"])
        self.assertNotIn("nova_task:", result["markdown"])
        self.assertEqual(len(calls), 2)
        self.assertIn("Technical Chronicle Contract", calls[0]["system"])
        self.assertIn("NT-123 English pipeline adaptation", calls[1]["prompt"])
        openai_sender.assert_not_called()

    def test_english_technical_pass_writes_profile_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filtered = root / "__diary_daily" / "2026-05-19" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            (filtered / "unified_daily.jsonl").write_text(
                '{"time":"11:00","role":"assistant","content":"Implemented English technical pass."}\n',
                encoding="utf-8",
            )
            with (
                patch.object(english_technical_pass, "load_task_graph_context", return_value="NT-123 English pipeline"),
                patch.object(
                    english_technical_pass,
                    "generate_from_entries",
                    return_value={"markdown": "# 2026-05-19 Technical Progress Report (Nova-Task v2)\n"},
                ) as generator,
            ):
                out_file = english_technical_pass.write_technical_report("2026-05-19", root)
                content = out_file.read_text(encoding="utf-8")

        self.assertEqual(out_file.name, "technical-260519.md")
        self.assertIn("Technical Progress Report", content)
        self.assertEqual(generator.call_args.args[0], "2026-05-19")
        self.assertIn("codex", generator.call_args.args[1])
        self.assertEqual(generator.call_args.args[2], "NT-123 English pipeline")

    def test_technical_pass_skips_empty_filtered_agent_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent_dir = Path(tmp) / "empty"
            agent_dir.mkdir()
            (agent_dir / "unified_daily.jsonl").write_text("", encoding="utf-8")
            self.assertEqual(technical_pass.load_agent_entries(agent_dir), [])

            full_dir = Path(tmp) / "full"
            full_dir.mkdir()
            (full_dir / "unified_daily.jsonl").write_text(
                '{"role":"user","time":"09:01","content":"changed file"}\n',
                encoding="utf-8",
            )
            entries = technical_pass.load_agent_entries(full_dir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["content"], "changed file")

    def test_learning_payload_keeps_system_and_rendered_template(self):
        prompt = learning_pass.PROMPT_LEARNING.replace("{date}", "2026-05-19").replace("{summary}", "fixture-summary")
        payload = self._request_payload(learning_pass.call_llm, prompt)
        self.assertEqual(payload["system"], learning_pass.SYSTEM_LEARNING + learning_pass._thinking_instruction())
        self.assertEqual(payload["messages"][0]["content"], prompt)

    def test_english_learning_payload_uses_english_structure_without_chinese_headings(self):
        prompt = english_learning_payload.build_prompt(
            "2026-05-19",
            "\n".join(
                [
                    "## Daily Overview",
                    "* **Prompt drift found**: Technical partial packets emitted YAML too early.",
                    "## Scheduled Jobs",
                    "cron details should be removed",
                    "## Notes",
                    "The final integration prompt is the only stage allowed to emit Nova-Task YAML.",
                ]
            ),
        )

        self.assertIn("# 2026-05-19 Learning and Infrastructure Audit", prompt)
        self.assertIn("## Lessons", prompt)
        self.assertIn("## Infrastructure Updates", prompt)
        self.assertIn("Problem", prompt)
        self.assertIn("Root Cause", prompt)
        self.assertIn("Recommendation", prompt)
        self.assertIn("## Scheduled Jobs\nNone", prompt)
        self.assertNotIn("cron details should be removed", prompt)
        self.assertNotIn("黄金教训", prompt)
        self.assertNotIn("基建变动", prompt)

    def test_english_learning_fixture_uses_llm_generation_path(self):
        calls = []

        def fake_sender(**kwargs):
            calls.append(kwargs)
            return (
                "# 2026-05-19 Learning and Infrastructure Audit\n\n"
                "## Lessons\n### [codex] Partial YAML drift\n#### Problem\nPartial packets emitted YAML.\n"
                "#### Root Cause\nThe prompt did not forbid intermediate YAML.\n"
                "#### Recommendation\nOnly final integration should emit Nova-Task YAML.\n\n"
                "## Infrastructure Updates\nNone"
            )

        with (
            patch.object(english_learning_payload, "API_TYPE", "anthropic-messages"),
            patch.object(english_learning_payload, "send_anthropic_message", side_effect=fake_sender),
            patch.object(english_learning_payload, "send_openai_compatible_message") as openai_sender,
            redirect_stdout(io.StringIO()),
        ):
            result = english_learning_payload.generate_from_summary(
                "2026-05-19",
                "## Daily Overview\nTechnical partial packets emitted YAML too early.",
            )

        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["pipelineLanguageProfile"], "en")
        self.assertEqual(result["pass"], "learning")
        self.assertIn("Learning and Infrastructure Audit", result["markdown"])
        self.assertIn("## Lessons", result["markdown"])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["system"].startswith("You are a precise technical audit assistant."))
        self.assertIn("Technical partial packets emitted YAML too early", calls[0]["prompt"])
        openai_sender.assert_not_called()

    def test_english_learning_pass_reads_english_narrative_and_writes_profile_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            narrative = root / "diary-2026" / "diary-2026-05" / "05-19" / "diary-260519.md"
            narrative.parent.mkdir(parents=True)
            narrative.write_text("## Daily Overview\nEnglish source only.\n", encoding="utf-8")
            (narrative.parent / "日记-260519.md").write_text("## 今日概要\nWrong source.\n", encoding="utf-8")
            with patch.object(
                english_learning_pass,
                "generate_from_summary",
                return_value={"markdown": "# 2026-05-19 Learning and Infrastructure Audit\n"},
            ) as generator:
                out_file = english_learning_pass.write_learning_report("2026-05-19", root)
                content = out_file.read_text(encoding="utf-8")

        self.assertEqual(out_file.name, "learning-260519.md")
        self.assertIn("Learning and Infrastructure Audit", content)
        self.assertIn("English source only", generator.call_args.args[1])
        self.assertNotIn("Wrong source", generator.call_args.args[1])

    def test_english_payloads_use_configured_llm_timeout(self):
        def fake_sender(**kwargs):
            observed.append(kwargs)
            return "ok"

        observed = []
        with (
            patch.object(english_narrative_payload, "API_TYPE", "anthropic-messages"),
            patch.object(english_narrative_payload, "LLM_TIMEOUT_SECONDS", 37),
            patch.object(english_narrative_payload, "send_anthropic_message", side_effect=fake_sender),
        ):
            english_narrative_payload.call_llm("Summarize this.", max_tokens=64)

        with (
            patch.object(english_technical_payload, "API_TYPE", "anthropic-messages"),
            patch.object(english_technical_payload, "LLM_TIMEOUT_SECONDS", 38),
            patch.object(english_technical_payload, "send_anthropic_message", side_effect=fake_sender),
        ):
            english_technical_payload.call_llm("Extract evidence.", max_tokens=64)

        with (
            patch.object(english_learning_payload, "API_TYPE", "anthropic-messages"),
            patch.object(english_learning_payload, "LLM_TIMEOUT_SECONDS", 39),
            patch.object(english_learning_payload, "send_anthropic_message", side_effect=fake_sender),
        ):
            english_learning_payload.call_llm("Extract lessons.")

        self.assertEqual([call["timeout"] for call in observed], [37, 38, 39])

    def test_english_pipeline_mock_llm_smoke_chains_profile_artifacts(self):
        fixture_root = ROOT / "tests" / "fixtures" / "english_pipeline"
        expected = json.loads((fixture_root / "expected-artifacts.json").read_text(encoding="utf-8"))
        business_date = expected["businessDate"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filtered_target = root / "__diary_daily" / business_date / "_filtered"
            shutil.copytree(fixture_root / "filtered", filtered_target)
            day_dir = root / "diary-2026" / "diary-2026-05" / "05-19"
            day_dir.mkdir(parents=True, exist_ok=True)
            (day_dir / "日记-260519.md").write_text("## 今日概要\nThis Chinese file must not be read.\n", encoding="utf-8")
            (day_dir / "技术进展-260519.md").write_text("# 中文技术进展\n", encoding="utf-8")
            (day_dir / "智慧沉淀-260519.md").write_text("# 中文智慧沉淀\n", encoding="utf-8")

            def narrative(entries_by_agent):
                self.assertIn("codex", entries_by_agent)
                self.assertIn("profile-aware artifact filenames", entries_by_agent["codex"][0]["content"])
                return {"markdown": "## Daily Overview\n* **English pipeline**: profile artifacts were generated.\n"}

            def technical(date_str, entries_by_source, task_graph_context):
                self.assertEqual(date_str, business_date)
                self.assertIn("codex", entries_by_source)
                self.assertIn("NT-EN", task_graph_context)
                return {
                    "markdown": (
                        "# 2026-05-19 Technical Progress Report\n\n"
                        "## Engineering Objectives and Outcomes\nEnglish pipeline profile artifacts were generated.\n\n"
                        "## Nova-Task Reconciliation Hooks\nNone\n"
                    )
                }

            def learning(date_str, summary_text):
                self.assertEqual(date_str, business_date)
                self.assertIn("Daily Overview", summary_text)
                self.assertNotIn("This Chinese file must not be read", summary_text)
                return {"markdown": "# 2026-05-19 Learning and Infrastructure Audit\n\n## Lessons\nNone\n"}

            with (
                patch.object(english_narrative_pass, "generate_from_entries", side_effect=narrative),
                patch.object(english_narrative_pass, "fetch_weather_for_date", return_value="Cloudy, 28 C"),
                patch.object(english_technical_pass, "load_task_graph_context", return_value="NT-EN English pipeline"),
                patch.object(english_technical_pass, "generate_from_entries", side_effect=technical),
                patch.object(english_learning_pass, "generate_from_summary", side_effect=learning),
            ):
                narrative_path = english_narrative_pass.write_narrative_report(business_date, root)
                technical_path = english_technical_pass.write_technical_report(business_date, root)
                learning_path = english_learning_pass.write_learning_report(business_date, root)

            self.assertEqual(narrative_path.name, expected["artifacts"]["narrative"])
            self.assertEqual(technical_path.name, expected["artifacts"]["technical"])
            self.assertEqual(learning_path.name, expected["artifacts"]["learning"])
            self.assertTrue(narrative_path.exists())
            self.assertTrue(technical_path.exists())
            self.assertTrue(learning_path.exists())

    def test_learning_summary_excludes_cron_section_and_embedded_json(self):
        summary = "\n".join(
            [
                "# 2026年05月19日 日记",
                "## 今日概要",
                "summary",
                "## 定时任务情况",
                "| 时间 | 任务ID | 状态 | 耗时 | 执行结论 |",
                "| 04:00 | `cron01` | Failed | 120.2s | cron timeout |",
                "## 备注",
                "note",
                "```json",
                '{"cronTasks": [{"taskId": "cron01"}]}',
                "```",
            ]
        )
        cleaned = learning_pass.prepare_learning_summary(summary)
        self.assertIn("## 定时任务情况\n无", cleaned)
        self.assertNotIn("cron timeout", cleaned)
        self.assertNotIn("cronTasks", cleaned)


if __name__ == "__main__":
    unittest.main()

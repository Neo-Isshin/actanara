import io
import importlib.util
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation import cli
from data_foundation import operator_cli
from data_foundation.external_agent_memory import (
    DEFAULT_SEARCH_TIMEOUT_SECONDS,
    ExternalSearchBudget,
    compact_memory_results,
    normalize_memory_response,
    search_memory,
)


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class _FakeRawResponse(_FakeResponse):
    def read(self):
        return self.payload


class ExternalAgentMemoryCliTests(unittest.TestCase):
    def test_packaged_cli_reports_product_version(self):
        with (
            patch("data_foundation.cli.product_version", return_value="1.2.0") as version,
            redirect_stdout(io.StringIO()) as output,
        ):
            code = cli.main(["--version"])

        self.assertEqual(code, 0)
        version.assert_called_once_with()
        self.assertEqual(output.getvalue(), "actanara 1.2.0\n")

    def test_advanced_cli_wrapper_delegates_to_packaged_entrypoint(self):
        module_path = ROOT / "advanced" / "cli" / "actanara.py"
        spec = importlib.util.spec_from_file_location("actanara_advanced_wrapper", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        self.assertIs(module.main, cli.main)

    def test_search_memory_calls_external_read_only_facade(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse({"available": True, "results": []})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = search_memory(
                "RAG current status",
                top_k=99,
                dashboard_url="http://dashboard.local",
                timeout_seconds=3,
                filters={"dateFrom": "2026-06-01", "sourceSets": ["task-board-snapshot"]},
            )

        self.assertEqual(result["results"], [])
        self.assertIn("queryPlan", result)
        self.assertIn("citationPack", result)
        self.assertIn("answerSynthesis", result)
        self.assertEqual(captured["url"], "http://dashboard.local/api/rag/external/search")
        self.assertEqual(captured["timeout"], 3)
        self.assertEqual(captured["body"]["query"], "RAG current status")
        self.assertEqual(captured["body"]["topK"], 20)
        self.assertEqual(captured["body"]["dateFrom"], "2026-06-01")
        self.assertEqual(captured["body"]["sourceSets"], ["task-board-snapshot"])
        self.assertEqual(captured["body"]["budgetCall"], 1)
        self.assertEqual(captured["body"]["budgetMaxCalls"], 3)
        self.assertGreater(captured["body"]["remainingBudgetMs"], 0)

    def test_search_memory_resolves_active_runtime_dashboard_url_when_not_explicit(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return _FakeResponse({"available": True, "results": []})

        with (
            patch("data_foundation.external_agent_memory._active_runtime_dashboard_url", return_value="http://127.0.0.1:8765") as resolver,
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            search_memory("runtime port")

        resolver.assert_called_once_with()
        self.assertEqual(captured["url"], "http://127.0.0.1:8765/api/rag/external/search")

    def test_search_memory_explicit_url_overrides_active_runtime(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return _FakeResponse({"available": True, "results": []})

        with (
            patch("data_foundation.external_agent_memory._active_runtime_dashboard_url") as resolver,
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            search_memory("explicit port", dashboard_url="http://dashboard.local:9999")

        resolver.assert_not_called()
        self.assertEqual(captured["url"], "http://dashboard.local:9999/api/rag/external/search")

    def test_product_and_legacy_search_parsers_defer_url_to_active_runtime(self):
        product = operator_cli._parser().parse_args(["search", "memory"])
        legacy = cli.build_parser().parse_args(["rag", "search-memory", "memory"])

        self.assertIsNone(product.dashboard_url)
        self.assertIsNone(legacy.dashboard_url)
        self.assertEqual(product.timeout, DEFAULT_SEARCH_TIMEOUT_SECONDS)
        self.assertEqual(legacy.timeout, DEFAULT_SEARCH_TIMEOUT_SECONDS)

    def test_shared_budget_enforces_three_calls_and_returns_stable_fourth_result(self):
        budget = ExternalSearchBudget(total_seconds=90, max_calls=3)
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((json.loads(request.data.decode("utf-8")), timeout))
            return _FakeResponse({"available": True, "results": []})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = [
                search_memory("bounded recall", dashboard_url="http://dashboard.local", budget=budget)
                for _ in range(4)
            ]

        self.assertEqual(len(calls), 3)
        self.assertEqual([item[0]["budgetCall"] for item in calls], [1, 2, 3])
        self.assertFalse(results[3]["available"])
        self.assertEqual(results[3]["reason"], "rag-external-budget-exhausted")
        self.assertEqual(results[3]["budgetTelemetry"]["callsUsed"], 3)
        self.assertTrue(results[3]["budgetTelemetry"]["exhausted"])

    def test_shared_budget_uses_monotonic_deadline_before_transport(self):
        now = [100.0]
        budget = ExternalSearchBudget(total_seconds=1, clock=lambda: now[0])
        now[0] = 101.1
        with patch("urllib.request.urlopen") as urlopen:
            result = search_memory("expired", dashboard_url="http://dashboard.local", budget=budget)

        urlopen.assert_not_called()
        self.assertEqual(result["reason"], "rag-external-budget-exhausted")
        self.assertEqual(result["budgetTelemetry"]["remainingBudgetMs"], 0)

    def test_search_memory_timeout_returns_stable_unavailable_schema(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("synthetic timeout")):
            payload = search_memory("timeout", dashboard_url="http://dashboard.local")

        self.assertFalse(payload["available"])
        self.assertEqual(payload["quality"]["status"], "insufficient")
        self.assertTrue(payload["quality"]["needsMoreEvidence"])
        self.assertTrue(payload["retrievalController"]["serverSide"])
        self.assertEqual(payload["retrievalController"]["passesRun"], ["quality-gate"])

    def test_search_memory_invalid_encoding_and_shape_return_stable_unavailable_schema(self):
        for body, reason in (
            (b"\xff\xfe", "rag-external-invalid-encoding"),
            (b"[]", "rag-external-invalid-schema"),
        ):
            with self.subTest(reason=reason):
                with patch("urllib.request.urlopen", return_value=_FakeRawResponse(body)):
                    payload = search_memory("bad response", dashboard_url="http://dashboard.local")

                self.assertFalse(payload["available"])
                self.assertEqual(payload["reason"], reason)
                self.assertEqual(payload["results"], [])
                self.assertEqual(payload["quality"]["status"], "insufficient")
                self.assertTrue(payload["retrievalController"]["needsMoreEvidence"])

    def test_cli_search_memory_prints_json(self):
        payload = {"available": True, "results": [{"sourceSet": "lessons", "textPreview": "Use read-only memory."}]}
        with (
            patch("data_foundation.cli.search_memory", return_value=payload) as search,
            redirect_stdout(io.StringIO()) as output,
        ):
            code = cli.main(["rag", "search-memory", "memory policy", "--top-k", "3", "--json"])

        self.assertEqual(code, 0)
        search.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())["results"][0]["sourceSet"], "lessons")

    def test_product_search_command_is_stable_rag_facade(self):
        payload = {"available": True, "results": [{"sourceSet": "lessons", "textPreview": "Use product search."}]}
        with (
            patch("data_foundation.operator_cli.search_memory", return_value=payload) as search,
            redirect_stdout(io.StringIO()) as output,
        ):
            code = cli.main(["search", "memory policy", "--top-k", "3", "--json"])

        self.assertEqual(code, 0)
        search.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())["results"][0]["textPreview"], "Use product search.")

    def test_packaged_cli_delegates_operator_commands(self):
        payload = {"summary": {"errors": 0}}
        with (
            patch("data_foundation.operator_cli.actanara_settings_status", return_value=payload) as status,
            patch("data_foundation.operator_cli.format_actanara_settings_status", return_value="Actanara · System status\n") as formatter,
            redirect_stdout(io.StringIO()) as output,
        ):
            code = cli.main(["settings", "status"])

        self.assertEqual(code, 0)
        status.assert_called_once_with(None, doctor_profile="all")
        formatter.assert_called_once_with(payload)
        self.assertIn("Actanara · System status", output.getvalue())

    def test_packaged_cli_supports_layered_doctor_flags(self):
        payload = {"summary": {"errors": 0}, "doctorProfile": "pipeline"}
        with (
            patch("data_foundation.operator_cli.actanara_settings_status", return_value=payload) as status,
            patch("data_foundation.operator_cli.format_actanara_settings_status", return_value="Actanara · Daily diary check\n"),
            redirect_stdout(io.StringIO()) as output,
        ):
            code = cli.main(["doctor", "--pipeline"])

        self.assertEqual(code, 0)
        status.assert_called_once_with(None, doctor_profile="pipeline")
        self.assertIn("Actanara · Daily diary check", output.getvalue())

    def test_packaged_cli_no_args_prints_product_command_guide(self):
        with redirect_stdout(io.StringIO()) as output:
            code = cli.main([])

        self.assertEqual(code, 0)
        self.assertIn("Actanara", output.getvalue())
        self.assertIn("Start here:", output.getvalue())
        self.assertIn("actanara doctor", output.getvalue())

    def test_rag_group_help_keeps_existing_nonzero_exit_code(self):
        with redirect_stdout(io.StringIO()) as output:
            code = cli.main(["rag"])

        self.assertEqual(code, 1)
        self.assertIn("search-memory", output.getvalue())

    def test_compact_memory_results_reports_unavailable_without_mutation_hint(self):
        text = compact_memory_results({"available": False, "reason": "server-unavailable"})
        self.assertIn("Actanara · Memory search", text)
        self.assertIn("Unavailable", text)
        self.assertIn("not responding", text)
        self.assertIn("actanara doctor --rag", text)
        self.assertNotIn("server-unavailable", text)

    def test_normalize_memory_response_preserves_external_evidence_schema(self):
        payload = normalize_memory_response({"available": False, "reason": "server-unavailable"}, query="policy", top_k=3)

        self.assertFalse(payload["available"])
        self.assertEqual(payload["queryPlan"]["query"], "policy")
        self.assertEqual(payload["queryPlan"]["topK"], 3)
        self.assertEqual(payload["citationPack"], [])
        self.assertEqual(payload["eventAggregation"]["schemaVersion"], 2)
        self.assertEqual(payload["eventAggregation"]["status"], "unavailable")
        self.assertEqual(payload["answerSynthesis"]["status"], "unavailable")
        self.assertEqual(payload["quality"]["schemaVersion"], 1)
        self.assertTrue(payload["quality"]["needsMoreEvidence"])
        self.assertEqual(payload["retrievalController"]["schemaVersion"], 1)
        self.assertTrue(payload["retrievalController"]["needsMoreEvidence"])
        self.assertTrue(payload["agentic"]["evidenceFieldsStable"])
        self.assertTrue(payload["agentic"]["serverSideMultiPass"])
        self.assertTrue(payload["agentic"]["serverSideEventAggregation"])


if __name__ == "__main__":
    unittest.main()

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["ACTANARA_SECRET_BACKEND"] = "memory"

from data_foundation.paths import initialize_home
from data_foundation.pipeline_llm_attribution import (
    aggregate_pipeline_llm_calls,
    list_pipeline_llm_calls,
    pipeline_llm_attribution_by_stage,
    pipeline_llm_attribution_context,
    record_pipeline_llm_call,
    record_pipeline_llm_call_from_environment,
)
from data_foundation.pipeline_runs import create_pipeline_run


class PipelineLlmAttributionTests(unittest.TestCase):
    def _runtime_with_run(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
        run_id = create_pipeline_run(
            paths,
            business_date="2026-07-19",
            run_kind="manual",
            requested_by="test",
        )
        return temporary, paths, run_id

    def test_old_pipeline_run_without_call_rows_is_unavailable_not_zero(self):
        temporary, paths, run_id = self._runtime_with_run()
        with temporary:
            summary = aggregate_pipeline_llm_calls(paths, run_id)
            attribution = pipeline_llm_attribution_by_stage(paths, run_id)

        self.assertFalse(summary["callDataAvailable"])
        self.assertFalse(summary["usageAvailable"])
        self.assertEqual(summary["usageStatus"], "unavailable")
        self.assertIsNone(summary["llmCallCount"])
        self.assertTrue(all(value is None for value in summary["tokens"].values()))
        self.assertEqual(attribution["stages"], [])

    def test_records_nullable_usage_aggregates_by_run_and_stage(self):
        temporary, paths, run_id = self._runtime_with_run()
        with temporary:
            first_id = record_pipeline_llm_call(
                paths,
                pipeline_run_id=run_id,
                stage_id="technical",
                pass_id="technical",
                call_id="technical-chunk-1",
                chunk_id="chunk-1",
                status="completed",
                provider_id="primary",
                model="model-a",
                api_type="openai-compatible",
                duration_ms=1200,
                usage={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_tokens": 10,
                    "total_tokens": 130,
                },
                usage_source="response",
                retry_count=1,
                attempts=[
                    {
                        "providerId": "primary",
                        "model": "model-a",
                        "status": "completed",
                        "errorSummary": '{"token":"must-not-persist"}',
                        "headers": {"Authorization": "Bearer must-not-persist"},
                    }
                ],
                metadata={
                    "label": "technical chunk",
                    "prompt": "must-not-persist",
                    "apiKey": "must-not-persist",
                    "token": "must-not-persist",
                },
            )
            second_id = record_pipeline_llm_call(
                paths,
                pipeline_run_id=run_id,
                stage_id="technical",
                call_id="technical-chunk-2",
                chunk_id="chunk-2",
                status="completed",
                provider_id="fallback",
                model="model-b",
                usage={"input": 80, "output": 15, "total": 95},
                usage_source="estimated",
                estimation_method="cl100k_base prompt + response text",
                fallback_count=1,
            )
            record_pipeline_llm_call(
                paths,
                pipeline_run_id=run_id,
                stage_id="learning",
                call_id="learning-1",
                status="failed",
                provider_id="fallback",
                model="model-b",
                usage_source="unavailable",
                failure_class="timeout",
                error_summary="Authorization: Bearer must-not-persist api_key=also-secret",
                fallback_count=1,
            )
            calls = list_pipeline_llm_calls(paths, run_id)
            technical = aggregate_pipeline_llm_calls(paths, run_id, stage_id="technical")
            run_summary = aggregate_pipeline_llm_calls(paths, run_id)
            by_stage = pipeline_llm_attribution_by_stage(paths, run_id)

        self.assertLess(first_id, second_id)
        self.assertEqual(len(calls), 3)
        self.assertIsNone(calls[0]["usage"]["reasoningTokens"])
        self.assertNotIn("prompt", calls[0]["metadata"])
        self.assertNotIn("apiKey", calls[0]["metadata"])
        self.assertNotIn("token", calls[0]["metadata"])
        self.assertNotIn("headers", calls[0]["attempts"][0])
        self.assertNotIn("must-not-persist", json.dumps(calls[0]))
        self.assertIn("[REDACTED]", calls[0]["attempts"][0]["errorSummary"])
        self.assertNotIn("must-not-persist", calls[2]["errorSummary"])
        self.assertIn("[REDACTED]", calls[2]["errorSummary"])

        self.assertEqual(technical["usageStatus"], "available")
        self.assertTrue(technical["estimated"])
        self.assertEqual(technical["llmCallCount"], 2)
        self.assertEqual(technical["retryCount"], 1)
        self.assertEqual(technical["fallbackCount"], 1)
        self.assertEqual(technical["tokens"]["inputTokens"], 180)
        self.assertEqual(technical["tokens"]["outputTokens"], 35)
        self.assertEqual(technical["tokens"]["totalTokens"], 225)
        self.assertIsNone(technical["tokens"]["reasoningTokens"])

        self.assertEqual(run_summary["usageStatus"], "partial")
        self.assertEqual(run_summary["failedCallCount"], 1)
        self.assertEqual(run_summary["unavailableCallCount"], 1)
        self.assertEqual([stage["stageId"] for stage in by_stage["stages"]], ["technical", "learning"])
        self.assertEqual(by_stage["stages"][1]["usageStatus"], "unavailable")
        self.assertTrue(by_stage["stages"][1]["callDataAvailable"])
        self.assertEqual(by_stage["stages"][1]["llmCallCount"], 1)

    def test_environment_helper_is_optional_and_requires_valid_run_and_stage(self):
        temporary, paths, run_id = self._runtime_with_run()
        with temporary:
            self.assertIsNone(pipeline_llm_attribution_context({}))
            self.assertIsNone(pipeline_llm_attribution_context({"ACTANARA_PIPELINE_RUN_ID": str(run_id)}))
            context = pipeline_llm_attribution_context(
                {
                    "ACTANARA_PIPELINE_RUN_ID": str(run_id),
                    "ACTANARA_PIPELINE_STAGE_ID": "narrative",
                }
            )
            record_id = record_pipeline_llm_call_from_environment(
                paths,
                environment={
                    "ACTANARA_PIPELINE_RUN_ID": str(run_id),
                    "ACTANARA_PIPELINE_STAGE_ID": "narrative",
                },
                status="completed",
                provider_id="primary",
                model="model-a",
                usage={"totalTokens": 0},
                usage_source="response",
            )
            ignored = record_pipeline_llm_call_from_environment(
                paths,
                environment={},
                status="completed",
            )
            calls = list_pipeline_llm_calls(paths, run_id)

        self.assertEqual(context, {"pipelineRunId": run_id, "stageId": "narrative"})
        self.assertIsInstance(record_id, int)
        self.assertIsNone(ignored)
        self.assertEqual(calls[0]["usage"]["totalTokens"], 0)

    def test_estimated_usage_requires_method_and_missing_run_is_rejected(self):
        temporary, paths, run_id = self._runtime_with_run()
        with temporary:
            with self.assertRaisesRegex(ValueError, "estimation_method"):
                record_pipeline_llm_call(
                    paths,
                    pipeline_run_id=run_id,
                    stage_id="narrative",
                    status="completed",
                    usage={"totalTokens": 10},
                    usage_source="estimated",
                )
            with self.assertRaisesRegex(ValueError, "does not exist"):
                record_pipeline_llm_call(
                    paths,
                    pipeline_run_id=run_id + 999,
                    stage_id="narrative",
                    status="completed",
                )

    def test_usage_source_requires_values_and_reported_total_is_not_recomputed(self):
        temporary, paths, run_id = self._runtime_with_run()
        with temporary:
            for usage_source in ("response", "estimated"):
                with self.subTest(usage_source=usage_source), self.assertRaisesRegex(
                    ValueError,
                    "requires at least one token value",
                ):
                    record_pipeline_llm_call(
                        paths,
                        pipeline_run_id=run_id,
                        stage_id="narrative",
                        status="completed",
                        usage_source=usage_source,
                        estimation_method="fixture" if usage_source == "estimated" else None,
                    )

            record_pipeline_llm_call(
                paths,
                pipeline_run_id=run_id,
                stage_id="narrative",
                status="completed",
                usage={
                    "inputTokens": 100,
                    "outputTokens": 20,
                    "cacheReadTokens": 40,
                    "reasoningTokens": 5,
                    "totalTokens": 120,
                },
                usage_source="response",
            )
            summary = aggregate_pipeline_llm_calls(paths, run_id)

        self.assertEqual(summary["tokens"]["inputTokens"], 100)
        self.assertEqual(summary["tokens"]["outputTokens"], 20)
        self.assertEqual(summary["tokens"]["cacheReadTokens"], 40)
        self.assertEqual(summary["tokens"]["reasoningTokens"], 5)
        self.assertEqual(summary["tokens"]["totalTokens"], 120)


if __name__ == "__main__":
    unittest.main()

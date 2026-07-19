import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.llm_execution import ProviderChainError, execute_llm_message
from data_foundation.llm_transport import (
    LlmTransportAttempt,
    LlmTransportError,
    LlmTransportResult,
    LlmUsage,
)
from data_foundation.paths import initialize_home
from data_foundation.pipeline_llm_attribution import list_pipeline_llm_calls
from data_foundation.pipeline_runs import create_pipeline_run


def _provider(
    entry_id: str,
    *,
    provider: str,
    model: str,
    api: str = "openai-compatible",
    api_key: str = "synthetic-provider-secret",
    ready: bool = True,
) -> dict:
    return {
        "entryId": entry_id,
        "provider": provider,
        "endpoint": f"https://{entry_id}.invalid/v1",
        "model": model,
        "api": api,
        "apiKey": api_key,
        "hasApiKey": bool(api_key),
        "secretRef": {"backend": "runtime-file"},
        "timeoutSeconds": 30,
        "maxTokens": 4096,
        "readiness": {
            "ready": ready,
            "status": "ready" if ready else "missing-configuration",
            **({"error": "missing apiKey", "missing": ["apiKey"]} if not ready else {}),
        },
    }


def _result(text: str = "ok", *, estimated: bool = False) -> LlmTransportResult:
    return LlmTransportResult(
        text=text,
        usage=LlmUsage(
            input_tokens=20,
            output_tokens=5,
            cache_tokens=3,
            reasoning_tokens=2,
            total_tokens=25,
            reported_total_tokens=None if estimated else 25,
            cache_read_tokens=3,
            cache_write_tokens=None,
            estimated=estimated,
            source="local_estimate" if estimated else "provider_response",
            method="bytes-divided-by-4" if estimated else "provider-reported-total",
            estimated_fields=("input_tokens", "output_tokens", "total_tokens") if estimated else (),
        ),
        api_type="injected",
        model="injected",
        payload_variant="full",
        attempts=(LlmTransportAttempt("full", 0, "success"),),
        response_id="response-1",
    )


def _transport_failure(
    failure_class: str,
    *,
    status_code: int | None = None,
    retryable: bool = True,
    message: str = "provider failed",
) -> LlmTransportError:
    return LlmTransportError(
        message,
        failure_class=failure_class,
        retryable=retryable,
        status_code=status_code,
        attempts=(
            LlmTransportAttempt(
                "full",
                0,
                "failed",
                failure_class=failure_class,
                status_code=status_code,
                retryable=retryable,
            ),
            LlmTransportAttempt(
                "full",
                1,
                "failed",
                failure_class=failure_class,
                status_code=status_code,
                retryable=retryable,
            ),
        ),
        api_type="openai-compatible",
        model="model",
    )


class LlmExecutionTests(unittest.TestCase):
    def setUp(self):
        self.primary = _provider("primary-slot", provider="primary", model="model-a")
        self.fallback = _provider("fallback-slot", provider="fallback", model="model-b")

    def _execute(self, **overrides):
        arguments = {
            "system": "system-private-text",
            "prompt": "prompt-private-text",
            "temperature": 0.1,
            "max_tokens": 512,
            "paths": object(),
            "environment": {},
            "label": "technical chunk",
            "pass_id": "technical",
            "chunk_id": "chunk-1",
        }
        arguments.update(overrides)
        return execute_llm_message(**arguments)

    def test_transient_transport_classes_fallback_in_order_and_record_success(self):
        cases = (
            ("auth", 401),
            ("rate_limit", 429),
            ("timeout", None),
            ("network", None),
            ("5xx", 503),
            ("content_parse", None),
        )

        for failure_class, status_code in cases:
            sent_models = []
            records = []

            def sender(**kwargs):
                sent_models.append(kwargs["model"])
                if kwargs["model"] == "model-a":
                    raise _transport_failure(
                        failure_class,
                        status_code=status_code,
                        message=(
                            "Authorization: Bearer synthetic-provider-secret "
                            "prompt-private-text"
                        ),
                    )
                return _result("fallback-ok")

            with self.subTest(failure_class=failure_class):
                with (
                    patch(
                        "data_foundation.llm_execution.resolve_llm_provider_chain",
                        return_value=[self.primary, self.fallback],
                    ),
                    patch(
                        "data_foundation.llm_execution.record_pipeline_llm_call_from_environment",
                        side_effect=lambda _paths, **call: records.append(call),
                    ),
                ):
                    result = self._execute(sender=sender)

                self.assertEqual(result.text, "fallback-ok")
                self.assertEqual(result.model, "model-b")
                self.assertEqual(result.api_type, "openai-compatible")
                self.assertEqual(sent_models, ["model-a", "model-b"])
                self.assertEqual(len(records), 1)
                record = records[0]
                self.assertEqual(record["status"], "completed")
                self.assertEqual(record["provider_id"], "fallback")
                self.assertEqual(record["model"], "model-b")
                self.assertEqual(record["usage_source"], "response")
                self.assertEqual(record["retry_count"], 1)
                self.assertEqual(record["fallback_count"], 1)
                self.assertEqual(
                    [attempt["providerId"] for attempt in record["attempts"]],
                    ["primary-slot", "fallback-slot"],
                )
                serialized = json.dumps(record)
                self.assertNotIn("synthetic-provider-secret", serialized)
                self.assertNotIn("prompt-private-text", serialized)

    def test_all_providers_failed_raises_safe_chain_error_and_records_once(self):
        third = _provider("third-slot", provider="third", model="model-c")
        records = []
        failure_by_model = {
            "model-a": ("rate_limit", 429),
            "model-b": ("timeout", None),
            "model-c": ("5xx", 503),
        }

        def sender(**kwargs):
            failure_class, status_code = failure_by_model[kwargs["model"]]
            raise _transport_failure(
                failure_class,
                status_code=status_code,
                message="api_key=synthetic-provider-secret prompt-private-text",
            )

        with (
            patch(
                "data_foundation.llm_execution.resolve_llm_provider_chain",
                return_value=[self.primary, self.fallback, third],
            ),
            patch(
                "data_foundation.llm_execution.record_pipeline_llm_call_from_environment",
                side_effect=lambda _paths, **call: records.append(call),
            ),
        ):
            with self.assertRaises(ProviderChainError) as raised:
                self._execute(sender=sender)

        error = raised.exception
        self.assertEqual(error.failure_class, "5xx")
        self.assertEqual(error.provider_id, "third")
        self.assertEqual([attempt["provider"] for attempt in error.attempts], ["primary", "fallback", "third"])
        self.assertNotIn("synthetic-provider-secret", json.dumps(error.to_dict()))
        self.assertNotIn("prompt-private-text", json.dumps(error.to_dict()))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "failed")
        self.assertEqual(records[0]["usage_source"], "unavailable")
        self.assertEqual(records[0]["retry_count"], 3)
        self.assertEqual(records[0]["fallback_count"], 2)

    def test_request_failure_does_not_fallback(self):
        sent_models = []
        records = []

        def sender(**kwargs):
            sent_models.append(kwargs["model"])
            raise _transport_failure("request", retryable=False)

        with (
            patch(
                "data_foundation.llm_execution.resolve_llm_provider_chain",
                return_value=[self.primary, self.fallback],
            ),
            patch(
                "data_foundation.llm_execution.record_pipeline_llm_call_from_environment",
                side_effect=lambda _paths, **call: records.append(call),
            ),
        ):
            with self.assertRaises(ProviderChainError) as raised:
                self._execute(sender=sender)

        self.assertEqual(raised.exception.failure_class, "request")
        self.assertEqual(sent_models, ["model-a"])
        self.assertEqual(records[0]["fallback_count"], 0)
        self.assertEqual(len(records[0]["attempts"]), 1)

    def test_unready_fallback_entry_fails_fast_before_any_sender_call(self):
        unready = _provider(
            "fallback-slot",
            provider="fallback",
            model="model-b",
            api_key="",
            ready=False,
        )
        records = []

        with (
            patch(
                "data_foundation.llm_execution.resolve_llm_provider_chain",
                return_value=[self.primary, unready],
            ),
            patch(
                "data_foundation.llm_execution.record_pipeline_llm_call_from_environment",
                side_effect=lambda _paths, **call: records.append(call),
            ),
        ):
            with self.assertRaises(ProviderChainError) as raised:
                self._execute(sender=lambda **_kwargs: self.fail("sender must not run"))

        self.assertEqual(raised.exception.failure_class, "config")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.provider_id, "fallback")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "failed")
        self.assertEqual(records[0]["failure_class"], "config")
        self.assertEqual(records[0]["attempts"][0]["status"], "not-ready")

    def test_memory_secret_fails_fast_for_cross_process_execution(self):
        memory_provider = {
            **self.primary,
            "secretRef": {"backend": "memory"},
            "readiness": {"ready": True, "status": "ready"},
        }

        with (
            patch(
                "data_foundation.llm_execution.resolve_llm_provider_chain",
                return_value=[memory_provider],
            ),
            patch("data_foundation.llm_execution.record_pipeline_llm_call_from_environment"),
        ):
            with self.assertRaises(ProviderChainError) as raised:
                self._execute(sender=lambda **_kwargs: self.fail("sender must not run"))

        self.assertEqual(raised.exception.failure_class, "config")
        self.assertIn("memory", str(raised.exception))

    def test_concurrent_chunks_fallback_independently_with_one_record_per_logical_call(self):
        records = []
        record_lock = threading.Lock()

        def collect(_paths, **call):
            with record_lock:
                records.append(call)

        def sender(**kwargs):
            if kwargs["model"] == "model-a":
                raise _transport_failure("timeout", message="primary chunk timed out")
            return _result("fallback-chunk-ok")

        with (
            patch(
                "data_foundation.llm_execution.resolve_llm_provider_chain",
                return_value=[self.primary, self.fallback],
            ),
            patch(
                "data_foundation.llm_execution.record_pipeline_llm_call_from_environment",
                side_effect=collect,
            ),
        ):
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [
                    executor.submit(
                        self._execute,
                        sender=sender,
                        chunk_id=f"chunk-{index}",
                    )
                    for index in range(24)
                ]
                results = [future.result(timeout=5) for future in futures]

        self.assertTrue(all(result.text == "fallback-chunk-ok" for result in results))
        self.assertTrue(all(result.model == "model-b" for result in results))
        self.assertEqual(len(records), 24)
        self.assertEqual(len({record["call_id"] for record in records}), 24)
        self.assertEqual(
            {record["chunk_id"] for record in records},
            {f"chunk-{index}" for index in range(24)},
        )
        self.assertTrue(all(record["provider_id"] == "fallback" for record in records))
        self.assertTrue(all(record["fallback_count"] == 1 for record in records))
        self.assertTrue(
            all(
                [attempt["providerId"] for attempt in record["attempts"]]
                == ["primary-slot", "fallback-slot"]
                for record in records
            )
        )

    def test_provider_chain_resolver_uses_keyword_only_contract(self):
        records = []
        runtime_paths = object()
        with (
            patch(
                "data_foundation.llm_execution.resolve_llm_provider_chain",
                autospec=True,
                return_value=[self.primary],
            ) as resolver,
            patch(
                "data_foundation.llm_execution.record_pipeline_llm_call_from_environment",
                side_effect=lambda _paths, **call: records.append(call),
            ),
        ):
            result = self._execute(
                paths=runtime_paths,
                sender=lambda **_kwargs: _result("keyword-contract-ok"),
            )

        self.assertEqual(result.text, "keyword-contract-ok")
        resolver.assert_called_once_with(
            runtime_paths,
            redact_secrets=False,
            require_cross_process_secret=True,
        )
        self.assertEqual(len(records), 1)

    def test_success_is_persisted_as_one_estimated_logical_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            run_id = create_pipeline_run(
                paths,
                business_date="2026-07-19",
                run_kind="manual",
                requested_by="test",
            )
            environment = {
                "ACTANARA_PIPELINE_RUN_ID": str(run_id),
                "ACTANARA_PIPELINE_STAGE_ID": "technical",
            }

            with patch(
                "data_foundation.llm_execution.resolve_llm_provider_chain",
                return_value=[self.primary],
            ):
                result = execute_llm_message(
                    system="must-not-persist-system",
                    prompt="must-not-persist-prompt",
                    temperature=0.1,
                    max_tokens=512,
                    paths=paths,
                    environment=environment,
                    pass_id="technical",
                    chunk_id="chunk-7",
                    label="Technical chunk 7",
                    metadata={"chunkOrdinal": 7, "prompt": "must-not-persist-metadata"},
                    sender=lambda **_kwargs: _result("estimated-ok", estimated=True),
                )
            calls = list_pipeline_llm_calls(paths, run_id)

        self.assertEqual(result.text, "estimated-ok")
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["status"], "completed")
        self.assertEqual(call["providerId"], "primary")
        self.assertEqual(call["model"], "model-a")
        self.assertEqual(call["passId"], "technical")
        self.assertEqual(call["chunkId"], "chunk-7")
        self.assertEqual(call["usageSource"], "estimated")
        self.assertEqual(call["estimationMethod"], "bytes-divided-by-4")
        self.assertEqual(call["usage"]["totalTokens"], 25)
        self.assertEqual(call["metadata"]["label"], "Technical chunk 7")
        self.assertEqual(call["metadata"]["chunkOrdinal"], 7)
        self.assertNotIn("prompt", call["metadata"])
        serialized = json.dumps(call)
        self.assertNotIn("must-not-persist", serialized)


if __name__ == "__main__":
    unittest.main()

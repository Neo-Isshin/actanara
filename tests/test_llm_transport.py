import json
import io
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import sys
import unittest
from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.llm_transport import (
    LlmTransportError,
    LlmTransportResult,
    LlmUsage,
    _reduced_max_tokens,
    anthropic_messages_payload,
    anthropic_messages_url,
    openai_chat_completions_payload,
    openai_chat_completions_url,
    send_anthropic_message,
    send_anthropic_message_detailed,
    send_openai_compatible_message,
    send_openai_compatible_message_detailed,
)
from diary_generator import learning_pass, narrative_pass, technical_pass


RUN_SLOW_TESTS = os.getenv("ACTANARA_RUN_SLOW_TESTS") == "1"


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"ok-md"}}]}'


class _AnthropicResponse(_Response):
    def read(self):
        return b'{"content":[{"type":"text","text":"OK"}]}'


class _PayloadResponse(_Response):
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _run_fixture_openssl(executable: str, *args: str) -> None:
    try:
        subprocess.run(
            [executable, *args],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError("OpenSSL TLS fixture generation timed out") from None
    except subprocess.CalledProcessError as error:
        operation = args[0] if args else "unknown"
        raise AssertionError(
            f"OpenSSL TLS fixture {operation} failed with status {error.returncode}"
        ) from None


def _make_fixture_ca(root: Path, executable: str, name: str) -> tuple[Path, Path]:
    key = root / f"{name}.key"
    certificate = root / f"{name}.crt"
    config = root / f"{name}.cnf"
    config.write_text(
        "[req]\n"
        "distinguished_name=dn\n"
        "x509_extensions=v3_ca\n"
        "prompt=no\n"
        "[dn]\n"
        f"CN=ActanaraTLSFixture{name}\n"
        "[v3_ca]\n"
        "basicConstraints=critical,CA:true\n"
        "keyUsage=critical,keyCertSign,cRLSign\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid:always,issuer\n",
        encoding="utf-8",
    )
    _run_fixture_openssl(
        executable,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-sha256",
        "-config",
        str(config),
        "-keyout",
        str(key),
        "-out",
        str(certificate),
        "-days",
        "2",
    )
    return key, certificate


def _make_fixture_leaf(
    root: Path,
    executable: str,
    name: str,
    ca_key: Path,
    ca_certificate: Path,
    *,
    subject_alt_name: str,
    expired: bool = False,
) -> tuple[Path, Path]:
    key = root / f"{name}.key"
    request = root / f"{name}.csr"
    certificate = root / f"{name}.crt"
    _run_fixture_openssl(
        executable,
        "req",
        "-new",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-sha256",
        "-keyout",
        str(key),
        "-out",
        str(request),
        "-subj",
        "/CN=127.0.0.1",
    )
    extensions = root / f"{name}-extensions.cnf"
    extensions.write_text(
        "[server]\n"
        "basicConstraints=critical,CA:false\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n"
        f"subjectAltName={subject_alt_name}\n",
        encoding="utf-8",
    )
    if not expired:
        _run_fixture_openssl(
            executable,
            "x509",
            "-req",
            "-sha256",
            "-in",
            str(request),
            "-CA",
            str(ca_certificate),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(certificate),
            "-days",
            "2",
            "-extfile",
            str(extensions),
            "-extensions",
            "server",
        )
        return key, certificate

    ca_state = root / f"{name}-ca-state"
    ca_state.mkdir()
    (ca_state / "newcerts").mkdir()
    (ca_state / "index.txt").write_text("", encoding="utf-8")
    (ca_state / "serial").write_text("1000\n", encoding="ascii")
    ca_config = ca_state / "ca.cnf"
    ca_config.write_text(
        "[ca]\n"
        "default_ca=test_ca\n"
        "[test_ca]\n"
        f"database={ca_state / 'index.txt'}\n"
        f"new_certs_dir={ca_state / 'newcerts'}\n"
        f"certificate={ca_certificate}\n"
        f"private_key={ca_key}\n"
        f"serial={ca_state / 'serial'}\n"
        "default_md=sha256\n"
        "default_days=1\n"
        "policy=policy_any\n"
        "x509_extensions=server\n"
        "unique_subject=no\n"
        "[policy_any]\n"
        "commonName=supplied\n"
        "[server]\n"
        "basicConstraints=critical,CA:false\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n"
        f"subjectAltName={subject_alt_name}\n",
        encoding="utf-8",
    )
    _run_fixture_openssl(
        executable,
        "ca",
        "-batch",
        "-config",
        str(ca_config),
        "-startdate",
        "200101000000Z",
        "-enddate",
        "200102000000Z",
        "-in",
        str(request),
        "-out",
        str(certificate),
        "-notext",
    )
    return key, certificate


class _QuietTLSFixtureHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_body = b'{"choices":[{"message":{"content":"fixture-ok"}}]}'
    raw_response = None
    stall_event = None
    request_seen = None

    def log_message(self, _format, *args):
        del args

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.request_seen.set()
        if self.stall_event is not None:
            self.stall_event.wait(2)
        try:
            if self.raw_response is not None:
                self.connection.sendall(self.raw_response)
                self.close_connection = True
                return
            self.send_response(self.response_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(self.response_body)))
            self.end_headers()
            self.wfile.write(self.response_body)
        except OSError:
            pass


@contextmanager
def _local_tls_server(
    key: Path,
    certificate: Path,
    *,
    response_status: int = 200,
    response_body: bytes | None = None,
    raw_response: bytes | None = None,
    stall_response: bool = False,
):
    request_seen = threading.Event()
    stall_event = threading.Event() if stall_response else None
    handler = type(
        "TLSFixtureHandler",
        (_QuietTLSFixtureHandler,),
        {
            "response_status": response_status,
            "response_body": response_body or _QuietTLSFixtureHandler.response_body,
            "raw_response": raw_response,
            "stall_event": stall_event,
            "request_seen": request_seen,
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    server.block_on_close = False
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=certificate, keyfile=key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield server.server_address[1], request_seen
    finally:
        if stall_event is not None:
            stall_event.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _stalled_tls_handshake_server():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.05)
    stopped = threading.Event()
    connection_seen = threading.Event()
    connections = []

    def accept_connections():
        while not stopped.is_set():
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            connections.append(connection)
            connection_seen.set()

    thread = threading.Thread(target=accept_connections, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], connection_seen
    finally:
        stopped.set()
        listener.close()
        thread.join(timeout=2)
        for connection in connections:
            connection.close()


class LLMTransportTests(unittest.TestCase):
    def test_diary_passes_default_thinking_mode_is_off(self):
        self.assertEqual(narrative_pass.THINKING_MODE, "off")
        self.assertEqual(technical_pass.THINKING_MODE, "off")
        self.assertEqual(learning_pass.THINKING_MODE, "off")

    def test_technical_pass_skips_active_graph_context_when_nova_task_disabled(self):
        with (
            patch.object(technical_pass, "load_paths") as load_paths,
            patch.object(technical_pass, "is_nova_task_enabled", return_value=False),
            patch.object(technical_pass, "render_task_graph_context", side_effect=AssertionError("active graph rendered")),
        ):
            load_paths.return_value = object()

            self.assertEqual(
                technical_pass.load_task_graph_context(),
                "Nova-Task v2 active graph disabled by settings.",
            )

    def test_technical_pass_manual_gate_rules_fall_back_to_default_rule(self):
        self.assertEqual(
            technical_pass._technical_gate_rule({"default": {"step": 3, "t": 120}}, "codex"),
            {"step": 3, "t": 120},
        )
        self.assertEqual(technical_pass._technical_gate_rule(None, "codex"), technical_pass.DEFAULT_GATE_RULE)

    def test_anthropic_url_supports_kimi_code_endpoint(self):
        self.assertEqual(
            anthropic_messages_url("https://api.kimi.com/coding/"),
            "https://api.kimi.com/coding/v1/messages",
        )

    def test_anthropic_sender_uses_x_api_key_header_casing(self):
        captured = {}

        def fake_urlopen(request, **kwargs):
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["context"] = kwargs.get("context")
            return _AnthropicResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = send_anthropic_message(
                endpoint="https://api.minimaxi.com",
                api_key="secret",
                model="MiniMax-M3",
                system="system",
                prompt="prompt",
                temperature=0,
                max_tokens=16,
                timeout=1,
            )

        self.assertEqual(result, "OK")
        self.assertEqual(captured["headers"]["X-api-key"], "secret")
        self.assertNotIn("Authorization", captured["headers"])
        self.assertTrue(captured["context"].check_hostname)
        self.assertEqual(captured["context"].verify_mode, ssl.CERT_REQUIRED)

    def test_anthropic_url_requires_endpoint(self):
        with self.assertRaisesRegex(ValueError, "endpoint is required"):
            anthropic_messages_url("")

    def test_openai_chat_url_preserves_versioned_endpoint(self):
        self.assertEqual(
            openai_chat_completions_url("https://api.moonshot.cn/v1"),
            "https://api.moonshot.cn/v1/chat/completions",
        )

    def test_anthropic_payload_can_disable_thinking(self):
        payload = anthropic_messages_payload("model", "system", "prompt", 0.1, 123, "off")
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_openai_payload_can_set_reasoning_effort(self):
        payload = openai_chat_completions_payload("model", "system", "prompt", 0.1, 123, "medium")
        self.assertEqual(payload["reasoning_effort"], "medium")

    def test_reduced_output_budget_never_increases_small_budget(self):
        self.assertEqual(_reduced_max_tokens(512), 512)
        self.assertEqual(_reduced_max_tokens(16384), 8192)

    def test_openai_compatible_payload_uses_bearer_auth(self):
        captured = {}

        def fake_urlopen(request, **kwargs):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = send_openai_compatible_message(
                endpoint="https://api.moonshot.cn/v1",
                api_key="secret",
                model="kimi-k2.5",
                system="system",
                prompt="prompt",
                temperature=0.1,
                max_tokens=123,
                timeout=1,
            )
        self.assertEqual(result, "ok-md")
        self.assertEqual(captured["url"], "https://api.moonshot.cn/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(captured["payload"]["messages"][0]["role"], "system")

    def test_openai_sender_falls_back_when_reasoning_parameter_is_rejected(self):
        payloads = []

        def fake_urlopen(request, **kwargs):
            del kwargs
            payload = json.loads(request.data.decode("utf-8"))
            payloads.append(payload)
            if len(payloads) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    {},
                    BytesIO(b"unsupported reasoning_effort"),
                )
            return _Response()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = send_openai_compatible_message(
                endpoint="https://api.example.com/v1",
                api_key="secret",
                model="model",
                system="system",
                prompt="prompt",
                temperature=0.1,
                max_tokens=123,
                timeout=1,
                thinking_mode="off",
            )
        self.assertEqual(result, "ok-md")
        self.assertEqual(payloads[0]["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", payloads[1])

    def test_openai_sender_retries_rate_limit_before_fallback(self):
        calls = {"count": 0}

        def fake_urlopen(request, **kwargs):
            del kwargs
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "Rate Limited", {}, BytesIO(b"rate limit"))
            return _Response()

        with (
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            patch("data_foundation.llm_transport.time.sleep") as sleep,
        ):
            result = send_openai_compatible_message(
                endpoint="https://api.example.com/v1",
                api_key="secret",
                model="model",
                system="system",
                prompt="prompt",
                temperature=0.1,
                max_tokens=123,
                timeout=1,
                thinking_mode="off",
            )
        self.assertEqual(result, "ok-md")
        self.assertEqual(calls["count"], 2)
        sleep.assert_called_once()

    def test_openai_detailed_sender_normalizes_reported_usage(self):
        payload = {
            "id": "chat-usage-1",
            "choices": [{"message": {"content": "usage-ok"}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
                "prompt_tokens_details": {"cached_tokens": 25},
                "completion_tokens_details": {"reasoning_tokens": 9},
            },
        }

        with patch("urllib.request.urlopen", return_value=_PayloadResponse(payload)):
            result = send_openai_compatible_message_detailed(
                endpoint="https://api.example.com/v1",
                api_key="secret",
                model="model-a",
                system="system",
                prompt="prompt",
                temperature=0.1,
                max_tokens=123,
                timeout=1,
            )

        self.assertIsInstance(result, LlmTransportResult)
        self.assertIsInstance(result.usage, LlmUsage)
        self.assertEqual(result.text, "usage-ok")
        self.assertEqual(result.response_id, "chat-usage-1")
        self.assertEqual(result.api_type, "openai-compatible")
        self.assertEqual(result.model, "model-a")
        self.assertEqual(result.payload_variant, "full")
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(result.usage.input_tokens, 100)
        self.assertEqual(result.usage.output_tokens, 40)
        self.assertEqual(result.usage.cache_tokens, 25)
        self.assertEqual(result.usage.cache_read_tokens, 25)
        self.assertEqual(result.usage.reasoning_tokens, 9)
        self.assertEqual(result.usage.total_tokens, 140)
        self.assertEqual(result.usage.reported_total_tokens, 140)
        self.assertFalse(result.usage.estimated)
        self.assertEqual(result.usage.source, "provider_response")
        self.assertEqual(result.usage.method, "provider-reported-total")
        self.assertEqual(result.to_dict()["usage"]["reasoningTokens"], 9)

    def test_openai_detailed_sender_accepts_responses_style_usage_names(self):
        payload = {
            "choices": [{"message": {"content": "alias-ok"}}],
            "usage": {
                "input_tokens": 31,
                "output_tokens": 12,
                "input_tokens_details": {"cached_tokens": 7},
                "output_tokens_details": {"reasoning_tokens": 4},
            },
        }

        with patch("urllib.request.urlopen", return_value=_PayloadResponse(payload)):
            result = send_openai_compatible_message_detailed(
                endpoint="https://api.example.com/v1",
                api_key="secret",
                model="model-a",
                system="system",
                prompt="prompt",
                temperature=0.1,
                max_tokens=123,
                timeout=1,
            )

        self.assertEqual(result.usage.input_tokens, 31)
        self.assertEqual(result.usage.output_tokens, 12)
        self.assertEqual(result.usage.cache_tokens, 7)
        self.assertEqual(result.usage.reasoning_tokens, 4)
        self.assertEqual(result.usage.total_tokens, 43)
        self.assertIsNone(result.usage.reported_total_tokens)
        self.assertEqual(result.usage.method, "provider-input-plus-output")

    def test_anthropic_detailed_sender_normalizes_cache_usage(self):
        payload = {
            "id": "msg-usage-1",
            "content": [{"type": "text", "text": "anthropic-usage-ok"}],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 3,
                "cache_read_input_tokens": 7,
                "thinking_tokens": 2,
            },
        }

        with patch("urllib.request.urlopen", return_value=_PayloadResponse(payload)):
            result = send_anthropic_message_detailed(
                endpoint="https://api.example.com",
                api_key="secret",
                model="model-b",
                system="system",
                prompt="prompt",
                temperature=0.1,
                max_tokens=123,
                timeout=1,
            )

        self.assertEqual(result.text, "anthropic-usage-ok")
        self.assertEqual(result.response_id, "msg-usage-1")
        self.assertEqual(result.api_type, "anthropic")
        self.assertEqual(result.usage.input_tokens, 10)
        self.assertEqual(result.usage.output_tokens, 5)
        self.assertEqual(result.usage.cache_read_tokens, 7)
        self.assertEqual(result.usage.cache_write_tokens, 3)
        self.assertEqual(result.usage.cache_tokens, 10)
        self.assertEqual(result.usage.reasoning_tokens, 2)
        self.assertEqual(result.usage.total_tokens, 25)
        self.assertIsNone(result.usage.reported_total_tokens)
        self.assertEqual(result.usage.method, "provider-input-plus-output-plus-cache")

    def test_detailed_senders_mark_missing_usage_as_estimated(self):
        cases = (
            (
                "openai-compatible",
                send_openai_compatible_message_detailed,
                {"choices": [{"message": {"content": "estimated-openai"}}]},
            ),
            (
                "anthropic",
                send_anthropic_message_detailed,
                {"content": [{"type": "text", "text": "estimated-anthropic"}]},
            ),
        )

        for api_type, sender, payload in cases:
            with self.subTest(api_type=api_type):
                with patch("urllib.request.urlopen", return_value=_PayloadResponse(payload)):
                    result = sender(
                        endpoint="https://api.example.com/v1",
                        api_key="secret",
                        model="model",
                        system="system text",
                        prompt="prompt text",
                        temperature=0.1,
                        max_tokens=123,
                        timeout=1,
                    )

                self.assertTrue(result.usage.estimated)
                self.assertEqual(result.usage.source, "local_estimate")
                self.assertEqual(
                    result.usage.method,
                    "utf8-bytes-divided-by-4-plus-message-overhead-v1",
                )
                self.assertEqual(
                    result.usage.estimated_fields,
                    ("input_tokens", "output_tokens", "total_tokens"),
                )
                self.assertIsNone(result.usage.reported_total_tokens)
                self.assertGreater(result.usage.input_tokens, 0)
                self.assertGreater(result.usage.output_tokens, 0)
                self.assertEqual(
                    result.usage.total_tokens,
                    result.usage.input_tokens + result.usage.output_tokens,
                )

    def test_detailed_sender_classifies_http_failures_and_redacts_secrets(self):
        synthetic_credential_value = "synthetic-transport-http-secret"
        cases = (
            (401, "auth", False),
            (429, "rate_limit", True),
            (503, "5xx", True),
            (400, "request", False),
        )

        for status_code, failure_class, retryable in cases:
            def fail_urlopen(request, **kwargs):
                del kwargs
                body = json.dumps(
                    {"error": {"api_key": str(synthetic_credential_value), "message": "request failed"}}
                ).encode("utf-8")
                raise urllib.error.HTTPError(
                    request.full_url,
                    status_code,
                    "provider failure",
                    {},
                    BytesIO(body),
                )

            with self.subTest(status_code=status_code):
                with (
                    patch("urllib.request.urlopen", side_effect=fail_urlopen),
                    patch("data_foundation.llm_transport.time.sleep"),
                ):
                    with self.assertRaises(LlmTransportError) as raised:
                        send_openai_compatible_message_detailed(
                            endpoint="https://api.example.com/v1",
                            api_key=str(synthetic_credential_value),
                            model="model",
                            system="system",
                            prompt="prompt",
                            temperature=0.1,
                            max_tokens=16,
                            timeout=1,
                            thinking_mode="off",
                        )

                error = raised.exception
                serialized = json.dumps(error.to_dict())
                self.assertEqual(error.failure_class, failure_class)
                self.assertEqual(error.status_code, status_code)
                self.assertEqual(error.retryable, retryable)
                self.assertTrue(error.attempts)
                self.assertNotIn(synthetic_credential_value, str(error))
                self.assertNotIn(synthetic_credential_value, serialized)
                self.assertIn("[REDACTED]", str(error))

    def test_detailed_sender_classifies_timeout_network_parse_and_config(self):
        cases = (
            (TimeoutError("timed out Authorization: Bearer timeout-secret"), "timeout", True),
            (urllib.error.URLError(ConnectionResetError("network api_key=network-secret")), "network", True),
        )

        for failure, failure_class, retryable in cases:
            with self.subTest(failure_class=failure_class):
                with (
                    patch("urllib.request.urlopen", side_effect=failure),
                    patch("data_foundation.llm_transport.time.sleep"),
                ):
                    with self.assertRaises(LlmTransportError) as raised:
                        send_openai_compatible_message_detailed(
                            endpoint="https://api.example.com/v1",
                            api_key="secret",
                            model="model",
                            system="system",
                            prompt="prompt",
                            temperature=0.1,
                            max_tokens=16,
                            timeout=1,
                        )
                self.assertEqual(raised.exception.failure_class, failure_class)
                self.assertEqual(raised.exception.retryable, retryable)
                self.assertNotIn(failure_class + "-secret", str(raised.exception))

        malformed = _PayloadResponse({"choices": []})
        with patch("urllib.request.urlopen", return_value=malformed):
            with self.assertRaises(LlmTransportError) as parse_error:
                send_openai_compatible_message_detailed(
                    endpoint="https://api.example.com/v1",
                    api_key="secret",
                    model="model",
                    system="system",
                    prompt="prompt",
                    temperature=0.1,
                    max_tokens=16,
                    timeout=1,
                )
        self.assertEqual(parse_error.exception.failure_class, "content_parse")
        self.assertFalse(parse_error.exception.retryable)

        with self.assertRaises(LlmTransportError) as config_error:
            send_openai_compatible_message_detailed(
                endpoint="",
                api_key="secret",
                model="model",
                system="system",
                prompt="prompt",
                temperature=0.1,
                max_tokens=16,
                timeout=1,
            )
        self.assertEqual(config_error.exception.failure_class, "config")
        self.assertFalse(config_error.exception.retryable)

    def test_tls_and_timeout_errors_remain_strict_and_redact_synthetic_secrets(self):
        synthetic_secret = "synthetic-" + "transport-secret-value"
        failures = (
            urllib.error.URLError(
                ssl.SSLCertVerificationError(
                    f"certificate verify failed api_key={synthetic_secret}"
                )
            ),
            TimeoutError("request timeout Authorization: " + f"Bearer {synthetic_secret}"),
        )

        for failure in failures:
            captured_contexts = []

            def fail_urlopen(_request, **kwargs):
                captured_contexts.append(kwargs["context"])
                raise failure

            with self.subTest(error_type=failure.__class__.__name__):
                with (
                    patch("urllib.request.urlopen", side_effect=fail_urlopen),
                    patch("data_foundation.llm_transport.time.sleep"),
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        send_openai_compatible_message(
                            endpoint="https://api.example.com/v1",
                            api_key=synthetic_secret,
                            model="model",
                            system="system",
                            prompt="prompt",
                            temperature=0.1,
                            max_tokens=16,
                            timeout=1,
                        )

                self.assertTrue(captured_contexts)
                for context in captured_contexts:
                    self.assertTrue(context.check_hostname)
                    self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertNotIn(synthetic_secret, str(raised.exception))
                self.assertIn("[REDACTED]", str(raised.exception))

    def test_learning_pass_uses_unified_executor(self):
        with (
            patch.object(
                learning_pass,
                "execute_llm_message",
                return_value=SimpleNamespace(text="ok"),
            ) as executor,
            patch.object(learning_pass, "load_paths", return_value="runtime-paths"),
        ):
            self.assertEqual(learning_pass.call_llm("prompt"), "ok")
        executor.assert_called_once()
        self.assertEqual(executor.call_args.kwargs["thinking_mode"], "off")
        self.assertEqual(executor.call_args.kwargs["pass_id"], "learning")
        self.assertEqual(executor.call_args.kwargs["label"], "learning llm")
        self.assertEqual(executor.call_args.kwargs["paths"], "runtime-paths")

    def test_narrative_pass_uses_unified_executor(self):
        with (
            patch.object(
                narrative_pass,
                "execute_llm_message",
                return_value=SimpleNamespace(text="ok"),
            ) as executor,
            patch.object(narrative_pass, "load_paths", return_value="runtime-paths"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(narrative_pass.call_llm("prompt"), "ok")
        executor.assert_called_once()
        self.assertEqual(executor.call_args.kwargs["thinking_mode"], "off")
        self.assertEqual(executor.call_args.kwargs["pass_id"], "narrative")
        self.assertEqual(executor.call_args.kwargs["label"], "partial")
        self.assertEqual(executor.call_args.kwargs["paths"], "runtime-paths")

    def test_technical_pass_uses_unified_executor(self):
        with (
            patch.object(
                technical_pass,
                "execute_llm_message",
                return_value=SimpleNamespace(text="ok"),
            ) as executor,
            patch.object(technical_pass, "load_paths", return_value="runtime-paths"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(technical_pass.call_llm("prompt"), "ok")
        executor.assert_called_once()
        self.assertEqual(executor.call_args.kwargs["thinking_mode"], "off")
        self.assertEqual(executor.call_args.kwargs["pass_id"], "technical")
        self.assertEqual(executor.call_args.kwargs["label"], "technical llm")
        self.assertEqual(executor.call_args.kwargs["paths"], "runtime-paths")

    def test_chinese_narrative_and_technical_propagate_all_provider_failure(self):
        for module, invoke, marker in (
            (narrative_pass, lambda: narrative_pass.call_llm("prompt"), "[LLM-ERROR]"),
            (technical_pass, lambda: technical_pass.call_llm("prompt"), "[TECH-LLM-ERROR]"),
        ):
            output = io.StringIO()
            with self.subTest(module=module.__name__):
                with (
                    patch.object(
                        module,
                        "execute_llm_message",
                        side_effect=RuntimeError("all configured providers failed"),
                    ),
                    patch.object(module, "load_paths", return_value="runtime-paths"),
                    redirect_stdout(output),
                ):
                    with self.assertRaisesRegex(RuntimeError, "all configured providers failed"):
                        invoke()
            self.assertIn(marker, output.getvalue())

    def test_technical_pass_splits_partial_prompt_when_gate_is_exceeded(self):
        entries = [
            {"role": "user", "time": "10:00", "content": "first"},
            {"role": "assistant", "time": "10:01", "content": "second"},
        ]

        def fake_token_count(prompt):
            return 999 if "first" in prompt and "second" in prompt else 1

        with (
            patch.object(technical_pass, "PIPELINE_GATE_TOKENS", 10),
            patch.object(technical_pass, "get_token_count", side_effect=fake_token_count),
            patch.object(technical_pass, "call_llm", side_effect=["one", "two"]) as llm,
            redirect_stdout(io.StringIO()),
        ):
            result = technical_pass._summarize_entries_with_gate("agent", entries, 400)
        self.assertEqual(result, "one\n\ntwo")
        self.assertEqual(llm.call_count, 2)

    @unittest.skipUnless(RUN_SLOW_TESTS, "slow pathological gate guard; set ACTANARA_RUN_SLOW_TESTS=1")
    def test_technical_pass_split_guards_pathological_token_counter(self):
        entries = [{"role": "user", "time": "10:00", "content": f"message {index}"} for index in range(10)]
        with (
            patch.object(technical_pass, "PIPELINE_GATE_TOKENS", 10),
            patch.object(technical_pass, "MAX_GATE_SPLIT_CHUNKS", 3),
            patch.object(technical_pass, "get_token_count", return_value=999),
        ):
            chunks = technical_pass._split_entries_by_gate(entries, "agent", 400)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(sum(len(chunk) for chunk in chunks), len(entries))
        self.assertEqual(len(chunks[-1]), 8)

    @unittest.skipUnless(RUN_SLOW_TESTS, "slow pathological gate guard; set ACTANARA_RUN_SLOW_TESTS=1")
    def test_technical_pass_final_precompress_split_guard(self):
        text = "\n".join(f"line {index}" for index in range(10))
        with (
            patch.object(technical_pass, "PIPELINE_GATE_TOKENS", 10),
            patch.object(technical_pass, "MAX_FINAL_PRECOMPRESS_CHUNKS", 4),
            patch.object(technical_pass, "get_token_count", return_value=999),
        ):
            chunks = technical_pass._split_text_for_final_gate(text)
        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[-1].splitlines(), [f"line {index}" for index in range(3, 10)])

    @unittest.skipUnless(RUN_SLOW_TESTS, "slow pathological gate guard; set ACTANARA_RUN_SLOW_TESTS=1")
    def test_technical_pass_unified_split_guard(self):
        entries = [{"source": "main", "role": "user", "time": "10:00", "content": f"message {index}"} for index in range(10)]
        with (
            patch.object(technical_pass, "PIPELINE_GATE_TOKENS", 10),
            patch.object(technical_pass, "MAX_GATE_SPLIT_CHUNKS", 2),
            patch.object(technical_pass, "get_token_count", return_value=999),
        ):
            chunks = technical_pass._split_unified_entries_by_gate(entries, {"main": 400})
        self.assertEqual(len(chunks), 2)
        self.assertEqual(sum(len(chunk) for chunk in chunks), len(entries))
        self.assertEqual(len(chunks[-1]), 9)

    def test_technical_pass_unified_stream_uses_single_call_under_gate(self):
        entries = [
            {"source": "gemini-cli", "role": "assistant", "time": "10:01", "content": "changed src/app.py"},
            {"source": "main", "role": "user", "time": "10:00", "content": "check task status"},
        ]
        captured = {}

        def fake_llm(prompt, **kwargs):
            del kwargs
            captured["prompt"] = prompt
            return "report"

        with (
            patch.object(technical_pass, "PIPELINE_GATE_TOKENS", 30000),
            patch.object(technical_pass, "get_token_count", return_value=100),
            patch.object(technical_pass, "call_llm", side_effect=fake_llm) as llm,
            redirect_stdout(io.StringIO()),
        ):
            result = technical_pass._call_unified_technical_pass(
                "2026-05-19",
                "active graph",
                entries,
                {"gemini-cli": 400, "main": 400},
            )
        self.assertEqual(result, "report")
        self.assertEqual(llm.call_count, 1)
        self.assertIn("[10:00][main][user]", captured["prompt"])
        self.assertIn("[10:01][gemini-cli][assistant]", captured["prompt"])
        self.assertNotIn("Source Hints", captured["prompt"])


class LLMTransportTLSIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        executable = shutil.which("openssl")
        if not executable:
            raise AssertionError("OpenSSL is required for the release-gate TLS certificate matrix")
        cls._fixture_directory = tempfile.TemporaryDirectory(prefix="actanara-tls-")
        try:
            cls.fixture_root = Path(cls._fixture_directory.name)
            cls.empty_ca_directory = cls.fixture_root / "empty-ca-directory"
            cls.empty_ca_directory.mkdir()
            cls.ca_key, cls.ca_certificate = _make_fixture_ca(cls.fixture_root, executable, "ca-a")
            cls.other_ca_key, cls.other_ca_certificate = _make_fixture_ca(
                cls.fixture_root,
                executable,
                "ca-b",
            )
            cls.valid_key, cls.valid_certificate = _make_fixture_leaf(
                cls.fixture_root,
                executable,
                "valid",
                cls.ca_key,
                cls.ca_certificate,
                subject_alt_name="IP:127.0.0.1",
            )
            cls.mismatch_key, cls.mismatch_certificate = _make_fixture_leaf(
                cls.fixture_root,
                executable,
                "mismatch",
                cls.ca_key,
                cls.ca_certificate,
                subject_alt_name="DNS:mismatch.invalid",
            )
            cls.expired_key, cls.expired_certificate = _make_fixture_leaf(
                cls.fixture_root,
                executable,
                "expired",
                cls.ca_key,
                cls.ca_certificate,
                subject_alt_name="IP:127.0.0.1",
                expired=True,
            )
        except Exception:
            cls._fixture_directory.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls._fixture_directory.cleanup()
        super().tearDownClass()

    def _send_to_fixture(
        self,
        port: int,
        trusted_ca: Path,
        synthetic_secret: str,
        *,
        timeout: float = 1,
    ) -> str:
        environment = {
            "SSL_CERT_FILE": str(trusted_ca),
            "SSL_CERT_DIR": str(self.empty_ca_directory),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
        with (
            patch.dict(os.environ, environment, clear=False),
            patch.object(urllib.request, "_opener", None),
        ):
            return send_openai_compatible_message(
                endpoint=f"https://127.0.0.1:{port}/v1",
                api_key=synthetic_secret,
                model="fixture-model",
                system="fixture-system",
                prompt="fixture-prompt",
                temperature=0,
                max_tokens=16,
                timeout=timeout,
            )

    def _assert_certificate_failure(
        self,
        port: int,
        trusted_ca: Path,
        synthetic_secret: str,
    ) -> str:
        with patch("data_foundation.llm_transport.time.sleep"):
            with self.assertRaises(RuntimeError) as raised:
                self._send_to_fixture(port, trusted_ca, synthetic_secret)
        message = str(raised.exception)
        self.assertIn("certificate", message.casefold())
        self.assertNotIn(synthetic_secret, message)
        return message

    def test_valid_private_ca_and_matching_hostname_succeeds(self):
        synthetic_secret = "synthetic-" + "valid-private-ca-header"
        with _local_tls_server(self.valid_key, self.valid_certificate) as (port, request_seen):
            result = self._send_to_fixture(port, self.ca_certificate, synthetic_secret)

        self.assertEqual(result, "fixture-ok")
        self.assertTrue(request_seen.is_set())

    def test_untrusted_private_ca_is_rejected_before_http_request(self):
        synthetic_secret = "synthetic-" + "untrusted-ca-header"
        with _local_tls_server(self.valid_key, self.valid_certificate) as (port, request_seen):
            self._assert_certificate_failure(port, self.other_ca_certificate, synthetic_secret)

        self.assertFalse(request_seen.is_set())

    def test_hostname_mismatch_is_rejected_before_http_request(self):
        synthetic_secret = "synthetic-" + "hostname-mismatch-header"
        with _local_tls_server(self.mismatch_key, self.mismatch_certificate) as (port, request_seen):
            self._assert_certificate_failure(port, self.ca_certificate, synthetic_secret)

        self.assertFalse(request_seen.is_set())

    def test_expired_certificate_is_rejected_before_http_request(self):
        synthetic_secret = "synthetic-" + "expired-certificate-header"
        with _local_tls_server(self.expired_key, self.expired_certificate) as (port, request_seen):
            self._assert_certificate_failure(port, self.ca_certificate, synthetic_secret)

        self.assertFalse(request_seen.is_set())

    def test_tls_handshake_timeout_is_bounded(self):
        synthetic_secret = "synthetic-" + "handshake-timeout-header"
        with _stalled_tls_handshake_server() as (port, connection_seen):
            with patch("data_foundation.llm_transport.time.sleep"):
                with self.assertRaises(RuntimeError) as raised:
                    self._send_to_fixture(
                        port,
                        self.ca_certificate,
                        synthetic_secret,
                        timeout=0.1,
                    )

        message = str(raised.exception)
        self.assertTrue(connection_seen.is_set())
        self.assertIn("timed out", message.casefold())
        self.assertNotIn(synthetic_secret, message)

    def test_tls_response_timeout_is_bounded(self):
        synthetic_secret = "synthetic-" + "response-timeout-header"
        with _local_tls_server(
            self.valid_key,
            self.valid_certificate,
            stall_response=True,
        ) as (port, request_seen):
            with patch("data_foundation.llm_transport.time.sleep"):
                with self.assertRaises(RuntimeError) as raised:
                    self._send_to_fixture(
                        port,
                        self.ca_certificate,
                        synthetic_secret,
                        timeout=0.1,
                    )

        message = str(raised.exception)
        self.assertTrue(request_seen.is_set())
        self.assertIn("timed out", message.casefold())
        self.assertNotIn(synthetic_secret, message)

    def test_tls_transport_error_redacts_synthetic_authorization_value(self):
        synthetic_secret = "synthetic-" + "tls-error-redaction-header"
        invalid_status_line = (
            "Authorization: " + "Bearer " + synthetic_secret + "\r\n\r\n"
        ).encode("utf-8")
        with _local_tls_server(
            self.valid_key,
            self.valid_certificate,
            raw_response=invalid_status_line,
        ) as (port, request_seen):
            with patch("data_foundation.llm_transport.time.sleep"):
                with self.assertRaises(RuntimeError) as raised:
                    self._send_to_fixture(port, self.ca_certificate, synthetic_secret)

        message = str(raised.exception)
        self.assertTrue(request_seen.is_set())
        self.assertNotIn(synthetic_secret, message)
        self.assertIn("[REDACTED]", message)


if __name__ == "__main__":
    unittest.main()

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))


from app.services import token_clock, tokens
from data_foundation.adapters.usage import OpenClawAdapter
from data_foundation.session_files import is_openclaw_session_file


class DashboardSessionFileContractTests(unittest.TestCase):
    def test_shared_openclaw_session_filename_truth_table(self):
        cases = {
            "session.jsonl": True,
            "session.jsonl.reset.20260714": True,
            "session.jsonl.deleted.20260714": True,
            "": False,
            "sessions.json": False,
            "session.json": False,
            "session.jsonl.lock": False,
            "session.jsonl.codex-app-server.json": False,
            "session.trajectory.jsonl": False,
            "session.jsonl.checkpoint.1": False,
            "session.jsonl.bak": False,
            "session.jsonl.tmp": False,
        }

        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertIs(is_openclaw_session_file(filename), expected)

    def test_token_summary_ignores_jsonl_metadata_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / "agents"
            sessions = agents / "fixture-agent" / "sessions"
            sessions.mkdir(parents=True)
            (sessions / "session.jsonl").write_text(
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": "2026-05-19T04:00:00Z",
                        "message": {
                            "role": "assistant",
                            "usage": {"input": 3, "output": 2, "cacheRead": 1},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (sessions / "session.jsonl.codex-app-server.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "sessionFile": "session.jsonl",
                        "runtimeFingerprint": "fixture",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with (
                patch.dict(os.environ, {"TARGET_TIMEZONE": "UTC"}, clear=False),
                patch.object(tokens, "_agents_dir", return_value=agents),
                patch.object(tokens, "_CACHE", {}),
                patch.object(tokens, "_LAST_MTIME", 0.0),
                patch("app.services.tz.hkt_now", return_value=datetime(2026, 5, 19, 12, 0, 0)),
            ):
                result = tokens.compute_summary()

        self.assertEqual(result["summary"]["count"], 1)
        self.assertEqual(result["summary"]["total"], 6)
        self.assertEqual(result["today"]["fixture-agent"]["total"], 6)

    def test_token_summary_still_fails_closed_for_malformed_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / "agents"
            sessions = agents / "fixture-agent" / "sessions"
            sessions.mkdir(parents=True)
            (sessions / "malformed.jsonl").write_text(
                "not-json\n{truncated\n",
                encoding="utf-8",
            )

            with (
                patch.dict(os.environ, {"TARGET_TIMEZONE": "UTC"}, clear=False),
                patch.object(tokens, "_agents_dir", return_value=agents),
                patch.object(tokens, "_CACHE", {}),
                patch.object(tokens, "_LAST_MTIME", 0.0),
                patch("app.services.tz.hkt_now", return_value=datetime(2026, 5, 19, 12, 0, 0)),
                self.assertRaisesRegex(ValueError, "session file contains no valid JSON records"),
            ):
                tokens.compute_summary()

    def test_token_clock_collection_excludes_newer_metadata_sidecar(self):
        session_id = "12345678-1234-1234-1234-123456789abc"
        sidecar_only_id = "87654321-4321-4321-4321-cba987654321"
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / "agents"
            sessions = agents / "fixture-agent" / "sessions"
            sessions.mkdir(parents=True)
            session = sessions / f"{session_id}.jsonl"
            sidecar = sessions / f"{session_id}.jsonl.codex-app-server.json"
            sidecar_only = sessions / f"{sidecar_only_id}.jsonl.codex-app-server.json"
            session.write_text("{}\n", encoding="utf-8")
            sidecar.write_text("{}\n", encoding="utf-8")
            sidecar_only.write_text("{}\n", encoding="utf-8")
            old_ns = 1_800_000_000_000_000_000
            new_ns = old_ns + 1_000_000_000
            os.utime(session, ns=(old_ns, old_ns))
            os.utime(sidecar, ns=(new_ns, new_ns))
            os.utime(sidecar_only, ns=(new_ns, new_ns))

            collected = token_clock._collect_openclaw_session_files(agents)

        self.assertEqual(set(collected), {session_id})
        self.assertEqual(collected[session_id], session)

    def test_foundation_adapter_discovery_excludes_metadata_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "openclaw"
            sessions = root / "fixture-agent" / "sessions"
            sessions.mkdir(parents=True)
            for filename in (
                "session.jsonl",
                "session.jsonl.reset.20260714",
                "session.jsonl.deleted.20260714",
                "session.jsonl.codex-app-server.json",
            ):
                (sessions / filename).write_text("{}\n", encoding="utf-8")

            discovered = [artifact.path.name for artifact in OpenClawAdapter(root).discover_sources()]

        self.assertEqual(
            sorted(discovered),
            [
                "session.jsonl",
                "session.jsonl.deleted.20260714",
                "session.jsonl.reset.20260714",
            ],
        )

    def test_codex_scanner_tolerates_null_token_info_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "codex" / "sessions"
            sessions.mkdir(parents=True)
            fixture = sessions / "rollout-null-info.jsonl"
            rows = [
                {
                    "timestamp": "2026-05-19T04:00:00Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "info": None},
                },
                {
                    "timestamp": "2026-05-19T04:01:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 7,
                                "output_tokens": 2,
                                "cached_input_tokens": 3,
                            }
                        },
                    },
                },
            ]
            fixture.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )

            with (
                patch.object(token_clock, "_external_tool_path", return_value=sessions),
                patch.object(token_clock, "_file_cache", {}),
                patch.object(token_clock, "_file_mtime_before_today", return_value=False),
            ):
                entries = token_clock._scan_codex("2026-05-19", 12)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["rawInput"], 7)
        self.assertEqual(entries[0]["input"], 7)
        self.assertEqual(entries[0]["output"], 2)
        self.assertEqual(entries[0]["cacheRead"], 3)


if __name__ == "__main__":
    unittest.main()

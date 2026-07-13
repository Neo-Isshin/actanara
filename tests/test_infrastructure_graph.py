import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.db import connect, migrate
from data_foundation.infrastructure import (
    apply_infrastructure_updates,
    dashboard_infrastructure_payload,
    infrastructure_events_for_date,
    list_infrastructure_entities,
    redact_sensitive_value,
    render_infrastructure_graph_context,
)
from data_foundation.paths import initialize_home


class InfrastructureGraphTests(unittest.TestCase):
    def test_apply_updates_merges_entities_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            migrate(paths)

            result = apply_infrastructure_updates(
                paths,
                date(2026, 7, 3),
                [
                    {
                        "entityType": "device",
                        "name": "Home VPS",
                        "kind": "vps",
                        "status": "online",
                        "location": "HK",
                        "change": "VPS connection endpoint was confirmed",
                        "endpoint": "https://user:secret@example.invalid:8443/admin?token=abc&mode=ops",
                        "field": "endpoint",
                        "currentValue": "https://user:secret@example.invalid:8443/admin?token=abc&mode=ops",
                        "evidence": ["technical report endpoint token=abc"],
                        "confidence": "high",
                    },
                    {
                        "entityType": "service",
                        "name": "Dashboard server",
                        "kind": "launchd-service",
                        "host": "Home VPS",
                        "status": "running",
                        "port": "3036",
                        "change": "Dashboard server is running on port 3036",
                        "field": "port",
                        "currentValue": "3036",
                    },
                ],
            )

            self.assertEqual(result, {"entities": 2, "events": 2})
            entities = list_infrastructure_entities(paths)
            by_name = {item["name"]: item for item in entities}
            self.assertEqual(by_name["Home VPS"]["endpoint"], "https://example.invalid:8443/admin?token=%5Bredacted%5D&mode=ops")
            self.assertEqual(by_name["Dashboard server"]["port"], "3036")
            events = infrastructure_events_for_date(paths, "2026-07-03")
            self.assertEqual(len(events), 2)
            self.assertTrue(all("secret" not in event["currentValue"] for event in events))
            self.assertTrue(all("token=abc" not in " ".join(event["evidence"]) for event in events))

    def test_dashboard_payload_groups_services_and_exposes_recent_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            migrate(paths)
            apply_infrastructure_updates(
                paths,
                "2026-07-03",
                [
                    {"entityType": "device", "name": "Mac mini", "status": "online", "role": "local host"},
                    {
                        "entityType": "service",
                        "name": "Embedding server",
                        "host": "Mac mini",
                        "status": "running",
                        "port": "18787",
                        "change": "Embedding server port confirmed",
                        "field": "port",
                        "currentValue": "18787",
                    },
                ],
            )

            payload = dashboard_infrastructure_payload(paths)

            self.assertEqual(payload["dataAuthority"], "foundation-infrastructure-graph-v1")
            self.assertTrue(payload["redacted"])
            self.assertEqual(payload["devices"][0]["name"], "Mac mini")
            self.assertEqual(payload["devices"][0]["services"][0]["name"], "Embedding server")
            self.assertEqual(payload["devices"][0]["services"][0]["recentActivity"][0]["current"], "18787")

    def test_graph_context_is_redacted_and_prefers_existing_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            migrate(paths)
            apply_infrastructure_updates(
                paths,
                "2026-07-03",
                [
                    {
                        "entityType": "service",
                        "name": "RAG API",
                        "endpoint": "http://admin:secret@127.0.0.1:8765/search?api_key=raw",
                        "change": "RAG API endpoint configured",
                        "field": "endpoint",
                        "currentValue": "http://admin:secret@127.0.0.1:8765/search?api_key=raw",
                    }
                ],
            )

            context = render_infrastructure_graph_context(paths)

            self.assertIn("Infrastructure Active Graph", context)
            self.assertIn("entityId=", context)
            self.assertNotIn("secret", context)
            self.assertNotIn("api_key=raw", context)

    def test_same_service_name_on_different_hosts_does_not_alias_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            migrate(paths)

            result = apply_infrastructure_updates(
                paths,
                "2026-07-03",
                [
                    {"entityType": "device", "name": "Debian", "status": "online"},
                    {"entityType": "device", "name": "Tencent Cloud", "status": "online"},
                    {
                        "entityType": "service",
                        "name": "Docker Runtime",
                        "host": "Debian",
                        "status": "running",
                    },
                    {
                        "entityType": "service",
                        "name": "Docker Runtime",
                        "host": "Tencent Cloud",
                        "status": "available",
                    },
                ],
            )

            self.assertEqual(result["entities"], 4)
            services = [item for item in list_infrastructure_entities(paths) if item["name"] == "Docker Runtime"]
            self.assertEqual(len(services), 2)
            self.assertEqual({item["metadata"]["host"] for item in services}, {"Debian", "Tencent Cloud"})

    def test_empty_graph_context_mentions_new_entities_not_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            migrate(paths)

            context = render_infrastructure_graph_context(paths)

            self.assertIn("new entity rows", context)
            self.assertNotIn("candidates", context)

    def test_redact_sensitive_assignment_and_sensitive_fields(self):
        self.assertEqual(redact_sensitive_value("abc", field_name="password"), "[redacted]")
        self.assertEqual(redact_sensitive_value("token=abc123; mode=run"), "token=[redacted]; mode=run")
        self.assertEqual(
            redact_sensitive_value("endpoint ssh://admin@example.invalid:22; token=abc123"),
            "endpoint ssh://example.invalid:22; token=[redacted]",
        )

    def test_raw_event_audit_payload_is_redacted_by_field_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            migrate(paths)

            apply_infrastructure_updates(
                paths,
                "2026-07-03",
                [
                    {
                        "entityType": "service",
                        "name": "RAG API",
                        "field": "credential",
                        "eventType": "credential_rotated",
                        "change": "RAG API credential rotated",
                        "currentValue": "raw-secret-token",
                        "value": "raw-secret-token",
                        "evidence": ["token=raw-secret-token"],
                    }
                ],
            )

            with connect(paths, read_only=True) as connection:
                raw_json = connection.execute("SELECT raw_json FROM infrastructure_events").fetchone()["raw_json"]
            raw = json.loads(raw_json)

            self.assertEqual(raw["currentValue"], "[redacted]")
            self.assertEqual(raw["value"], "[redacted]")
            self.assertNotIn("raw-secret-token", raw_json)


if __name__ == "__main__":
    unittest.main()

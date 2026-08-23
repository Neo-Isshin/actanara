from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_foundation.environment_assets import (
    EnvironmentAssetError,
    read_environment_asset_ledger,
    read_environment_observation_ledger,
    read_environment_reconciliation_audit,
    write_environment_observation_ledger,
)
from data_foundation.environment_reconciliation import (
    build_environment_reconciliation_prompt,
    run_environment_reconciliation,
)
from data_foundation.engineering_artifacts import engineering_artifact_catalog
from data_foundation.infrastructure import apply_infrastructure_updates, infrastructure_entity_catalog
from data_foundation.db import connect
from data_foundation.diary_paths import diary_technical_report_path
from data_foundation.paths import initialize_home


class EnvironmentReconciliationTests(unittest.TestCase):
    def _service(self) -> dict:
        return {
            "assetClass": "service", "name": "managed dashboard", "kind": "dashboard",
            "state": "active", "changeType": "deployed", "summary": "Health check passed.",
            "host": "local workstation", "location": "local",
            "endpoint": "http://127.0.0.1:3036/health", "port": "3036",
            "path": "/srv/dashboard", "version": "v2", "project": "Actanara",
            "evidenceRefs": ["E000001"], "confidence": "high",
        }

    def _paths(self, tmp: str):
        return initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")

    def _service_evidence(self, outcome: str = "deployed") -> str:
        return (
            "managed dashboard on local workstation " + outcome
            + "; endpoint http://127.0.0.1:3036/health port 3036 "
            "path /srv/dashboard version v2"
        )

    def _decision(
        self,
        observation_id: str,
        action: str,
        entity_id: str = "",
        *,
        catalog_actions: list[dict] | None = None,
        normalized: dict | None = None,
    ) -> str:
        normalized = normalized or {
            "kind": "dashboard",
            "state": "active",
            "changeType": "deployed",
            "host": "local workstation",
            "location": "local",
            "endpoint": "http://127.0.0.1:3036/health",
            "port": "3036",
            "path": "/srv/dashboard",
            "version": "v2",
            "project": "",
        }
        return json.dumps({
            "schema": "actanara.environment-reconciliation.v2",
            "businessDate": "2026-08-12",
            "decisions": [{
                "observationId": observation_id,
                "action": action,
                "existingEntityId": entity_id,
                "reason": "Matches the current catalog and cited evidence.",
                "confidence": "high",
                "normalized": normalized,
            }],
            "catalogActions": catalog_actions or [],
        })

    def test_existing_service_is_updated_without_duplicate_entity(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            apply_infrastructure_updates(paths, "2026-08-11", [
                {"entityType": "service", "name": "managed dashboard", "kind": "dashboard"}
            ])
            entity_id = next(iter(infrastructure_entity_catalog(paths)))
            write_environment_observation_ledger(
                paths, business_date="2026-08-12", observations=[self._service()],
                evidence_by_ref={"E000001": (
                    "managed dashboard on local workstation deployed; endpoint "
                    "http://127.0.0.1:3036/health port 3036 path /srv/dashboard version v2"
                )},
            )
            observation_id = read_environment_observation_ledger(paths, "2026-08-12").observations[0]["assetId"]
            sender = lambda **_kwargs: self._decision(observation_id, "update_existing", entity_id)
            result = run_environment_reconciliation(paths, business_date="2026-08-12", sender=sender)

            self.assertEqual(result.accepted_count, 1)
            self.assertEqual(len(infrastructure_entity_catalog(paths)), 1)
            self.assertEqual(read_environment_asset_ledger(paths, "2026-08-12")[0]["existingEntityId"], entity_id)

    def test_one_asset_recon_call_updates_infrastructure_and_engineering_catalogs(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            report_path = diary_technical_report_path(paths.diary_dir, "2026-08-12")
            report_path.parent.mkdir(parents=True)
            report_path.write_text(
                "# 2026-08-12 技术进展报告\n\n"
                "## 四、验证证据\n"
                "形成并验证可复用诊断脚本 network-diagnose.sh。 [E000002]\n",
                encoding="utf-8",
            )
            write_environment_observation_ledger(
                paths,
                business_date="2026-08-12",
                observations=[self._service()],
                evidence_by_ref={
                    "E000001": self._service_evidence(),
                    "E000002": (
                        "Actanara produced and verified reusable diagnostics script "
                        "network-diagnose.sh at /workspace/tools/network-diagnose.sh"
                    ),
                },
            )
            observation_id = read_environment_observation_ledger(
                paths, "2026-08-12"
            ).observations[0]["assetId"]
            response = json.dumps({
                "schema": "actanara.asset-reconciliation.v1",
                "businessDate": "2026-08-12",
                "infrastructure": {
                    "decisions": [{
                        "observationId": observation_id,
                        "action": "create_new",
                        "existingEntityId": "",
                        "reason": "Evidence shows a deployed persistent dashboard.",
                        "confidence": "high",
                        "normalized": {
                            "kind": "dashboard", "state": "active", "changeType": "deployed",
                            "host": "local workstation", "location": "local",
                            "endpoint": "http://127.0.0.1:3036/health", "port": "3036",
                            "path": "/srv/dashboard", "version": "v2", "project": "",
                        },
                    }],
                    "catalogActions": [],
                },
                "artifacts": {
                    "decisions": [{
                        "candidateId": "A000001",
                        "action": "create_new",
                        "existingArtifactId": "",
                        "name": "network-diagnose.sh",
                        "artifactType": "diagnostic-script",
                        "summary": "Reusable network diagnostics script was produced and verified.",
                        "evidenceRefs": ["E000002"],
                        "reason": "It remains reusable and locatable after the task.",
                        "confidence": "high",
                        "normalized": {
                            "status": "verified", "version": "",
                            "locator": "/workspace/tools/network-diagnose.sh", "project": "Actanara",
                        },
                    }],
                    "catalogActions": [],
                },
            })
            calls = []

            result = run_environment_reconciliation(
                paths,
                business_date="2026-08-12",
                sender=lambda **kwargs: calls.append(kwargs) or response,
            )

            self.assertEqual(len(calls), 1)
            self.assertEqual(result.accepted_count, 1)
            self.assertEqual(result.artifact_accepted_count, 1)
            self.assertEqual(len(infrastructure_entity_catalog(paths)), 1)
            artifacts = engineering_artifact_catalog(paths)
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(next(iter(artifacts.values()))["name"], "network-diagnose.sh")

    def test_prompt_keeps_full_catalog_but_prioritizes_related_identity_cluster(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            apply_infrastructure_updates(paths, "2026-08-11", [
                {"entityType": "service", "name": "Unrelated billing API", "kind": "api"},
                {"entityType": "service", "name": "Actanara Dashboard service", "kind": "dashboard"},
                {"entityType": "service", "name": "actanara-dashboard", "kind": "dashboard"},
            ])
            write_environment_observation_ledger(
                paths,
                business_date="2026-08-12",
                observations=[self._service()],
                evidence_by_ref={"E000001": self._service_evidence()},
            )
            prompt, catalog, _ledger = build_environment_reconciliation_prompt(
                paths,
                business_date="2026-08-12",
            )
            self.assertEqual(len(catalog), 3)
            self.assertIn("Unrelated billing API", prompt)
            self.assertLess(prompt.index("Actanara Dashboard service"), prompt.index("Unrelated billing API"))
            self.assertLess(prompt.index("actanara-dashboard"), prompt.index("Unrelated billing API"))

    def test_new_collision_and_missing_decision_fail_closed(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            apply_infrastructure_updates(paths, "2026-08-11", [
                {"entityType": "service", "name": "managed dashboard", "kind": "dashboard"}
            ])
            write_environment_observation_ledger(
                paths, business_date="2026-08-12", observations=[self._service()],
                evidence_by_ref={"E000001": "managed dashboard deployed on local workstation"},
            )
            observation_id = read_environment_observation_ledger(paths, "2026-08-12").observations[0]["assetId"]
            collision = run_environment_reconciliation(
                paths, business_date="2026-08-12",
                sender=lambda **_kwargs: self._decision(observation_id, "create_new"),
            )
            self.assertEqual(collision.accepted_count, 0)
            self.assertEqual(collision.validation_rejected_count, 1)
            with self.assertRaises(EnvironmentAssetError):
                run_environment_reconciliation(
                    paths, business_date="2026-08-12",
                    sender=lambda **_kwargs: json.dumps({
                        "schema": "actanara.environment-reconciliation.v2",
                        "businessDate": "2026-08-12", "decisions": [], "catalogActions": [],
                    }),
                )
            self.assertEqual(read_environment_asset_ledger(paths, "2026-08-12"), ())

    def test_empty_observation_ledger_skips_llm_and_writes_empty_authority(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            write_environment_observation_ledger(
                paths, business_date="2026-08-12", observations=(), evidence_by_ref={},
            )
            result = run_environment_reconciliation(
                paths, business_date="2026-08-12",
                sender=lambda **_kwargs: self.fail("empty reconciliation must not call the LLM"),
            )
            self.assertEqual(result.observation_count, 0)
            self.assertEqual(read_environment_asset_ledger(paths, "2026-08-12"), ())
            self.assertEqual(
                read_environment_reconciliation_audit(paths, "2026-08-12")["catalogActions"],
                [],
            )

    def test_invalid_normalized_classification_defers_only_that_observation(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            apply_infrastructure_updates(paths, "2026-08-11", [
                {"entityType": "service", "name": "managed dashboard", "kind": "dashboard"},
            ])
            entity_id = next(iter(infrastructure_entity_catalog(paths)))
            write_environment_observation_ledger(
                paths, business_date="2026-08-12", observations=[self._service()],
                evidence_by_ref={"E000001": self._service_evidence()},
            )
            observation_id = read_environment_observation_ledger(
                paths, "2026-08-12"
            ).observations[0]["assetId"]
            invalid = {
                "kind": "", "state": "sometimes-running", "changeType": "deployed",
                "host": "", "location": "", "endpoint": "", "port": "",
                "path": "", "version": "", "project": "",
            }
            result = run_environment_reconciliation(
                paths, business_date="2026-08-12",
                sender=lambda **_kwargs: self._decision(
                    observation_id, "update_existing", entity_id, normalized=invalid,
                ),
            )
            self.assertEqual(result.accepted_count, 0)
            self.assertEqual(result.action_counts["defer"], 1)
            self.assertEqual(read_environment_asset_ledger(paths, "2026-08-12"), ())

    def test_missing_normalized_payload_is_deferred_without_losing_the_batch(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            write_environment_observation_ledger(
                paths, business_date="2026-08-12", observations=[self._service()],
                evidence_by_ref={"E000001": self._service_evidence()},
            )
            observation_id = read_environment_observation_ledger(
                paths, "2026-08-12"
            ).observations[0]["assetId"]
            payload = json.loads(self._decision(observation_id, "create_new"))
            payload["decisions"][0].pop("normalized")
            result = run_environment_reconciliation(
                paths, business_date="2026-08-12",
                sender=lambda **_kwargs: json.dumps(payload),
            )
            self.assertEqual(result.action_counts["defer"], 1)
            self.assertEqual(result.accepted_count, 0)

    def test_catalog_action_cannot_execute_from_a_rejected_observation(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            apply_infrastructure_updates(paths, "2026-08-11", [
                {"entityType": "service", "name": "managed dashboard", "kind": "dashboard"},
            ])
            entity_id = next(iter(infrastructure_entity_catalog(paths)))
            write_environment_observation_ledger(
                paths, business_date="2026-08-12", observations=[self._service()],
                evidence_by_ref={"E000001": self._service_evidence()},
            )
            observation_id = read_environment_observation_ledger(
                paths, "2026-08-12"
            ).observations[0]["assetId"]
            action = {
                "action": "transition_state", "entityId": entity_id,
                "canonicalEntityId": "", "lifecycleStatus": "suspended",
                "healthStatus": "offline", "observationIds": [observation_id],
                "reason": "The observation claims the service stopped.", "confidence": "high",
            }
            unsupported = {
                "kind": "dashboard", "state": "suspended", "changeType": "stopped",
                "host": "", "location": "", "endpoint": "", "port": "",
                "path": "", "version": "", "project": "not present in evidence",
            }
            result = run_environment_reconciliation(
                paths, business_date="2026-08-12",
                sender=lambda **_kwargs: self._decision(
                    observation_id, "update_existing", entity_id,
                    catalog_actions=[action], normalized=unsupported,
                ),
            )
            self.assertEqual(result.validation_rejected_count, 1)
            self.assertEqual(result.catalog_projection["transitioned"], 0)
            self.assertEqual(result.catalog_deferred_count, 1)

    def test_duplicate_service_is_merged_without_deleting_history(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            apply_infrastructure_updates(paths, "2026-08-10", [
                {"entityType": "service", "name": "managed dashboard", "kind": "dashboard"},
            ])
            apply_infrastructure_updates(paths, "2026-08-11", [
                {"entityType": "service", "name": "Actanara Dashboard", "kind": "dashboard"},
            ])
            catalog = infrastructure_entity_catalog(paths)
            target_id = next(key for key, row in catalog.items() if row["name"] == "managed dashboard")
            duplicate_id = next(key for key, row in catalog.items() if row["name"] == "Actanara Dashboard")
            write_environment_observation_ledger(
                paths,
                business_date="2026-08-12",
                observations=[self._service()],
                evidence_by_ref={"E000001": self._service_evidence("deployed and its health check passed")},
            )
            observation_id = read_environment_observation_ledger(
                paths, "2026-08-12"
            ).observations[0]["assetId"]
            catalog_action = {
                "action": "merge_existing",
                "entityId": duplicate_id,
                "canonicalEntityId": target_id,
                "lifecycleStatus": "",
                "healthStatus": "",
                "observationIds": [observation_id],
                "reason": "Both active rows describe the same dashboard service.",
                "confidence": "high",
            }
            result = run_environment_reconciliation(
                paths,
                business_date="2026-08-12",
                sender=lambda **_kwargs: self._decision(
                    observation_id,
                    "update_existing",
                    target_id,
                    catalog_actions=[catalog_action],
                ),
            )

            self.assertEqual(result.catalog_projection["merged"], 1)
            self.assertEqual(len(infrastructure_entity_catalog(paths)), 1)
            with connect(paths, read_only=True) as connection:
                duplicate = connection.execute(
                    "SELECT archived_at, metadata_json FROM infrastructure_entities WHERE entity_id = ?",
                    (duplicate_id,),
                ).fetchone()
                old_events = connection.execute(
                    "SELECT COUNT(*) n FROM infrastructure_events WHERE entity_id = ?",
                    (duplicate_id,),
                ).fetchone()["n"]
            self.assertIsNotNone(duplicate["archived_at"])
            self.assertEqual(json.loads(duplicate["metadata_json"])["catalogMaintenance"]["supersededBy"], target_id)
            self.assertGreaterEqual(old_events, 2)
            audit = read_environment_reconciliation_audit(paths, "2026-08-12")
            self.assertEqual(audit["catalogActions"], [catalog_action])
            self.assertEqual(audit["result"]["catalogProjection"]["merged"], 1)
            audit_path = (
                paths.home / "artifacts" / "environment"
                / "environment-reconciliation-audit-v1-2026-08-12.json"
            )
            self.assertEqual(audit_path.stat().st_mode & 0o777, 0o600)
            encrypted = audit_path.read_text(encoding="utf-8")
            self.assertNotIn("Actanara Dashboard", encrypted)
            self.assertNotIn("Both active rows", encrypted)

    def test_stopped_service_transitions_state_without_catalog_archive(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            apply_infrastructure_updates(paths, "2026-08-11", [
                {"entityType": "service", "name": "managed dashboard", "kind": "dashboard"},
            ])
            entity_id = next(iter(infrastructure_entity_catalog(paths)))
            observation = {**self._service(), "state": "suspended", "changeType": "stopped", "summary": "Service was stopped."}
            write_environment_observation_ledger(
                paths,
                business_date="2026-08-12",
                observations=[observation],
                evidence_by_ref={"E000001": self._service_evidence("service was stopped")},
            )
            observation_id = read_environment_observation_ledger(
                paths, "2026-08-12"
            ).observations[0]["assetId"]
            action = {
                "action": "transition_state",
                "entityId": entity_id,
                "canonicalEntityId": "",
                "lifecycleStatus": "suspended",
                "healthStatus": "offline",
                "observationIds": [observation_id],
                "reason": "The cited observation explicitly reports that the service stopped.",
                "confidence": "high",
            }
            result = run_environment_reconciliation(
                paths,
                business_date="2026-08-12",
                sender=lambda **_kwargs: self._decision(
                    observation_id,
                    "update_existing",
                    entity_id,
                    catalog_actions=[action],
                    normalized={
                        "kind": "dashboard", "state": "suspended", "changeType": "stopped",
                        "host": "local workstation", "location": "local",
                        "endpoint": "http://127.0.0.1:3036/health", "port": "3036",
                        "path": "/srv/dashboard", "version": "v2", "project": "",
                    },
                ),
            )
            self.assertEqual(result.accepted_count, 1)
            self.assertEqual(result.catalog_projection["transitioned"], 1)
            with connect(paths, read_only=True) as connection:
                row = connection.execute(
                    "SELECT lifecycle_status, health_status, archived_at FROM infrastructure_entities WHERE entity_id = ?",
                    (entity_id,),
                ).fetchone()
            self.assertEqual((row["lifecycle_status"], row["health_status"]), ("suspended", "offline"))
            self.assertIsNone(row["archived_at"])

    def test_medium_confidence_catalog_action_is_audited_but_not_applied(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            apply_infrastructure_updates(paths, "2026-08-11", [
                {"entityType": "service", "name": "managed dashboard", "kind": "dashboard"},
                {"entityType": "service", "name": "dashboard legacy label", "kind": "dashboard"},
            ])
            catalog = infrastructure_entity_catalog(paths)
            target_id = next(key for key, row in catalog.items() if row["name"] == "managed dashboard")
            duplicate_id = next(key for key, row in catalog.items() if row["name"] == "dashboard legacy label")
            write_environment_observation_ledger(
                paths,
                business_date="2026-08-12",
                observations=[self._service()],
                evidence_by_ref={"E000001": self._service_evidence("deployed and healthy")},
            )
            observation_id = read_environment_observation_ledger(paths, "2026-08-12").observations[0]["assetId"]
            action = {
                "action": "archive_existing", "entityId": duplicate_id,
                "canonicalEntityId": target_id, "lifecycleStatus": "", "healthStatus": "",
                "observationIds": [observation_id], "reason": "The labels may describe one service.",
                "confidence": "medium",
            }
            result = run_environment_reconciliation(
                paths,
                business_date="2026-08-12",
                sender=lambda **_kwargs: self._decision(
                    observation_id, "update_existing", target_id, catalog_actions=[action]
                ),
            )
            self.assertEqual(result.catalog_deferred_count, 1)
            self.assertEqual(result.catalog_projection["archived"], 0)
            self.assertIn(duplicate_id, infrastructure_entity_catalog(paths))
            self.assertEqual(
                read_environment_reconciliation_audit(paths, "2026-08-12")["catalogActions"],
                [action],
            )


if __name__ == "__main__":
    unittest.main()

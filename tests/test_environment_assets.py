from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentic_rag.rag_v2_indexer import _collect_environment_assets
from data_foundation.db import connect
from data_foundation.environment_assets import (
    EnvironmentAssetError,
    apply_environment_asset_projection,
    bind_environment_asset_authority,
    environment_asset_ledger_ready,
    environment_observation_ledger_ready,
    parse_technical_environment_output,
    read_environment_asset_ledger,
    read_environment_observation_ledger,
    write_environment_asset_ledger,
    write_environment_observation_ledger,
)
from data_foundation.infrastructure import apply_infrastructure_updates, infrastructure_entity_catalog
from data_foundation.paths import initialize_home


class EnvironmentAssetTests(unittest.TestCase):
    def _evidence(self) -> dict[str, str]:
        return {
            "E000001": (
                "managed dashboard deployed on local workstation; health endpoint "
                "http://127.0.0.1:3036/health, port 3036, path /srv/private/dashboard, version v2"
            ),
            "E000002": "daily technical report was written and verified at /srv/private/technical.md",
        }

    def _output(self) -> str:
        payload = {
            "schema": "actanara.environment-observations.v2",
            "businessDate": "2026-08-12",
            "observations": [
                {
                    "assetClass": "service", "name": "managed dashboard",
                    "summary": "Dashboard deployment passed its health check.",
                    "evidenceRefs": ["E000001"],
                },
                {
                    "assetClass": "deliverable", "name": "daily technical report",
                    "summary": "The report was written and verified.",
                    "evidenceRefs": ["E000002"],
                },
            ],
        }
        return (
            "# 2026-08-12 技术进展报告\n\n## 一、工程目标与完成结果\n完成部署。\n\n"
            "## 九、环境观察私有账本\n```json\n"
            + json.dumps(payload, ensure_ascii=False) + "\n```\n"
        )

    def _normalized(self, observation: dict) -> dict:
        if observation["assetClass"] == "service":
            return {
                **observation,
                "kind": "dashboard", "state": "active", "changeType": "deployed",
                "host": "local workstation", "location": "local",
                "endpoint": "http://127.0.0.1:3036/health", "port": "3036",
                "path": "/srv/private/dashboard", "version": "v2", "project": "",
                "confidence": "high",
            }
        return {
            **observation,
            "kind": "report", "state": "verified", "changeType": "verified",
            "host": "", "location": "", "endpoint": "", "port": "",
            "path": "/srv/private/technical.md", "version": "",
            "project": "", "confidence": "high",
        }

    def test_technical_output_writes_observations_without_catalog_authority(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")
            result = parse_technical_environment_output(
                self._output(), business_date="2026-08-12", evidence_by_ref=self._evidence()
            )
            self.assertNotIn("identityMode", result.assets[0])
            self.assertNotIn("state", result.assets[0])
            self.assertNotIn("127.0.0.1", result.report_markdown)
            path = write_environment_observation_ledger(
                paths, business_date="2026-08-12", observations=result.assets,
                evidence_by_ref=self._evidence(),
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("127.0.0.1", path.read_text(encoding="utf-8"))
            reopened = read_environment_observation_ledger(paths, "2026-08-12")
            self.assertIsNotNone(reopened)
            self.assertEqual(len(reopened.observations), 1)
            self.assertEqual(result.rejected_asset_count, 1)
            self.assertTrue(environment_observation_ledger_ready(paths, "2026-08-12"))
            self.assertFalse(environment_asset_ledger_ready(paths, "2026-08-12"))

    def test_technical_output_repairs_only_trailing_json_commas(self):
        raw = self._output()
        repaired_input = raw.replace(
            '"evidenceRefs": ["E000001"]}',
            '"evidenceRefs": ["E000001"],}',
        ).replace(
            ']\n```',
            ',]\n```',
        )
        result = parse_technical_environment_output(
            repaired_input,
            business_date="2026-08-12",
            evidence_by_ref=self._evidence(),
        )
        self.assertEqual(len(result.assets), 1)

    def test_technical_output_does_not_rewrite_commas_inside_strings(self):
        raw = self._output().replace(
            "Dashboard deployment passed its health check.",
            "Dashboard text includes literal comma,} and comma,] sequences.",
        )
        result = parse_technical_environment_output(
            raw,
            business_date="2026-08-12",
            evidence_by_ref=self._evidence(),
        )
        self.assertIn("comma,}", result.assets[0]["summary"])
        self.assertIn("comma,]", result.assets[0]["summary"])

    def test_technical_output_rejects_ambiguous_json_repairs_and_duplicate_keys(self):
        cases = {
            "single-quotes": self._output().replace(
                '"schema": "actanara.environment-observations.v2"',
                "'schema': 'actanara.environment-observations.v2'",
            ),
            "comment": self._output().replace(
                '"observations": [',
                '// observations\n"observations": [',
            ),
            "duplicate": self._output().replace(
                '"businessDate": "2026-08-12"',
                '"businessDate": "2026-08-12", "businessDate": "2026-08-12"',
            ),
        }
        for label, raw in cases.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                EnvironmentAssetError, "not strict JSON"
            ):
                parse_technical_environment_output(
                    raw,
                    business_date="2026-08-12",
                    evidence_by_ref=self._evidence(),
                )

    def test_reconciled_authority_projects_and_is_the_only_rag_source(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")
            result = parse_technical_environment_output(
                self._output(), business_date="2026-08-12", evidence_by_ref=self._evidence()
            )
            bound = [
                bind_environment_asset_authority(
                    self._normalized(item), business_date="2026-08-12", evidence_by_ref=self._evidence(),
                    infrastructure_catalog={}, identity_mode="new" if item["assetClass"] == "service" else "",
                )
                for item in result.assets
            ]
            projection = apply_environment_asset_projection(paths, business_date="2026-08-12", assets=bound)
            write_environment_asset_ledger(paths, business_date="2026-08-12", assets=bound)
            self.assertEqual(projection["entities"], 1)
            self.assertEqual(projection["deliverables"], 0)
            chunks, _sources = _collect_environment_assets(SimpleNamespace(runtime_home=paths.home))
            self.assertEqual({item["sourceSet"] for item in chunks}, {"environment-state"})
            with connect(paths, read_only=True) as connection:
                sources = {row["source"] for row in connection.execute("SELECT source FROM infrastructure_events")}
            self.assertEqual(sources, {"environment-reconciliation"})

    def test_existing_identity_is_reopened_and_unknown_identity_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")
            apply_infrastructure_updates(paths, "2026-08-11", [
                {"entityType": "service", "name": "managed dashboard", "kind": "dashboard"}
            ])
            catalog = infrastructure_entity_catalog(paths)
            entity_id = next(iter(catalog))
            observation = parse_technical_environment_output(
                self._output(), business_date="2026-08-12", evidence_by_ref=self._evidence()
            ).assets[0]
            bound = bind_environment_asset_authority(
                self._normalized(observation), business_date="2026-08-12", evidence_by_ref=self._evidence(),
                infrastructure_catalog=catalog, identity_mode="existing", existing_entity_id=entity_id,
            )
            self.assertEqual(bound["existingEntityId"], entity_id)
            with self.assertRaises(EnvironmentAssetError):
                bind_environment_asset_authority(
                    self._normalized(observation), business_date="2026-08-12", evidence_by_ref=self._evidence(),
                    infrastructure_catalog=catalog, identity_mode="existing", existing_entity_id="infra-missing",
                )

    def test_unknown_evidence_and_ciphertext_tamper_fail_closed(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")
            partial = parse_technical_environment_output(
                self._output(), business_date="2026-08-12",
                evidence_by_ref={"E000002": self._evidence()["E000002"]},
            )
            self.assertEqual(len(partial.assets), 0)
            self.assertEqual(partial.rejected_asset_count, 2)
            path = write_environment_asset_ledger(paths, business_date="2026-08-12", assets=())
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["ciphertext"] = envelope["ciphertext"][:-2] + "AA"
            path.write_text(json.dumps(envelope), encoding="utf-8")
            path.chmod(0o600)
            self.assertIsNone(read_environment_asset_ledger(paths, "2026-08-12"))

    def test_engineering_deliverable_cannot_enter_infrastructure_authority(self):
        forged = {
            "assetClass": "deliverable", "name": "daily technical report",
            "summary": "The report was written and verified.",
            "evidenceRefs": ["E000002"], "kind": "report", "state": "verified",
            "changeType": "verified", "host": "", "location": "", "endpoint": "",
            "port": "", "path": "/srv/private/technical.md", "version": "",
            "project": "", "confidence": "high",
        }
        with self.assertRaisesRegex(EnvironmentAssetError, "invalid classification"):
            bind_environment_asset_authority(
                forged,
                business_date="2026-08-12",
                evidence_by_ref=self._evidence(),
                infrastructure_catalog={},
            )

    def test_first_key_creation_is_safe_across_concurrent_ledgers(self):
        with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}), tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(write_environment_asset_ledger, paths, business_date=value, assets=())
                    for value in ("2026-08-12", "2026-08-13")
                ]
                for future in futures:
                    future.result()
            self.assertEqual(read_environment_asset_ledger(paths, "2026-08-12"), ())
            self.assertEqual(read_environment_asset_ledger(paths, "2026-08-13"), ())


if __name__ == "__main__":
    unittest.main()

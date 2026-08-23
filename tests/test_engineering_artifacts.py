from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_foundation.engineering_artifacts import (
    apply_engineering_artifact_reconciliation,
    engineering_artifact_catalog,
)
from data_foundation.paths import initialize_home


class EngineeringArtifactTests(unittest.TestCase):
    def _paths(self, tmp: str):
        return initialize_home(Path(tmp) / "Actanara")

    def _decision(self, *, action: str, existing_id: str = "", locator: str = "/workspace/tools/check.py"):
        return {
            "candidateId": "A000001",
            "action": action,
            "existingArtifactId": existing_id,
            "name": "check.py",
            "artifactType": "diagnostic-script",
            "summary": "Reusable diagnostic script was verified.",
            "evidenceRefs": ["E000001"],
            "reason": "It is a durable, locatable engineering output.",
            "confidence": "high",
            "normalized": {
                "status": "verified", "version": "", "locator": locator,
                "project": "Actanara",
            },
        }

    def test_existing_artifact_updates_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            evidence = {"E000001": "Actanara verified check.py at /workspace/tools/check.py"}
            first = apply_engineering_artifact_reconciliation(
                paths, business_date="2026-08-21",
                decisions=[self._decision(action="create_new")], catalog_actions=[],
                evidence_by_ref=evidence,
            )
            artifact_id = next(iter(engineering_artifact_catalog(paths)))
            second = apply_engineering_artifact_reconciliation(
                paths, business_date="2026-08-22",
                decisions=[self._decision(action="update_existing", existing_id=artifact_id)],
                catalog_actions=[], evidence_by_ref=evidence,
            )

            self.assertEqual(first["accepted"], 1)
            self.assertEqual(second["accepted"], 1)
            self.assertEqual(len(engineering_artifact_catalog(paths)), 1)

    def test_unsupported_locator_and_commit_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            unsupported = apply_engineering_artifact_reconciliation(
                paths, business_date="2026-08-22",
                decisions=[self._decision(action="create_new", locator="/invented/path.py")],
                catalog_actions=[],
                evidence_by_ref={"E000001": "Actanara verified check.py"},
            )
            commit = self._decision(action="create_new", locator="")
            commit.update({"name": "abc123 commit", "artifactType": "git-commit"})
            blocked = apply_engineering_artifact_reconciliation(
                paths, business_date="2026-08-22", decisions=[commit], catalog_actions=[],
                evidence_by_ref={"E000001": "Actanara verified abc123 commit"},
            )

            self.assertEqual(unsupported["accepted"], 0)
            self.assertEqual(blocked["accepted"], 0)
            self.assertEqual(engineering_artifact_catalog(paths), {})

    def test_high_confidence_archive_action_requires_current_accepted_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            evidence = {"E000001": "Actanara verified check.py at /workspace/tools/check.py"}
            apply_engineering_artifact_reconciliation(
                paths, business_date="2026-08-21",
                decisions=[self._decision(action="create_new")], catalog_actions=[],
                evidence_by_ref=evidence,
            )
            artifact_id = next(iter(engineering_artifact_catalog(paths)))
            result = apply_engineering_artifact_reconciliation(
                paths,
                business_date="2026-08-22",
                decisions=[self._decision(action="update_existing", existing_id=artifact_id)],
                catalog_actions=[{
                    "action": "archive_existing", "artifactId": artifact_id,
                    "canonicalArtifactId": "", "status": "",
                    "candidateIds": ["A000001"], "reason": "Evidence confirms replacement.",
                    "confidence": "high",
                }],
                evidence_by_ref=evidence,
            )

            self.assertEqual(result["catalogActions"], 1)
            self.assertEqual(engineering_artifact_catalog(paths)[artifact_id]["status"], "archived")


if __name__ == "__main__":
    unittest.main()

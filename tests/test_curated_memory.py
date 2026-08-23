from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentic_rag.rag_settings import resolve_rag_settings
from agentic_rag.rag_v2_indexer import _collect_curated_core
from data_foundation.infrastructure import apply_infrastructure_updates
from data_foundation.engineering_artifacts import apply_engineering_artifact_reconciliation
from data_foundation.paths import initialize_home


class CuratedMemoryTests(unittest.TestCase):
    def test_curated_core_projects_registered_skills_and_current_infrastructure(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            skill_path = paths.home / "assets" / "skills" / "diagnose-asymmetric-paths" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text(
                """---
name: diagnose-asymmetric-paths
description: Use controlled two-ended observations when paths behave asymmetrically.
---

# Diagnose asymmetric paths

Compare both directions and verify at both ends.
""",
                encoding="utf-8",
            )
            apply_infrastructure_updates(
                paths,
                "2026-08-22",
                [
                    {
                        "entityType": "service",
                        "name": "managed dashboard",
                        "kind": "dashboard",
                        "status": "active",
                    }
                ],
            )
            apply_engineering_artifact_reconciliation(
                paths,
                business_date="2026-08-22",
                decisions=[{
                    "candidateId": "A000001",
                    "action": "create_new",
                    "existingArtifactId": "",
                    "name": "diagnose-paths.sh",
                    "artifactType": "diagnostic-script",
                    "summary": "Reusable path diagnostics script.",
                    "evidenceRefs": ["E000001"],
                    "reason": "Reusable verified engineering output.",
                    "confidence": "high",
                    "normalized": {
                        "status": "verified", "version": "",
                        "locator": "/workspace/tools/diagnose-paths.sh", "project": "Actanara",
                    },
                }],
                catalog_actions=[],
                evidence_by_ref={
                    "E000001": (
                        "Actanara verified diagnose-paths.sh at "
                        "/workspace/tools/diagnose-paths.sh"
                    ),
                },
            )

            chunks, sources = _collect_curated_core(resolve_rag_settings(paths))

            self.assertEqual({item["sourceSet"] for item in chunks}, {"curated-core"})
            self.assertEqual(
                {item["provenance"]["assetType"] for item in chunks},
                {"registered-skill", "current-infrastructure", "current-engineering-artifact"},
            )
            self.assertTrue(any("diagnose-asymmetric-paths" in item["text"] for item in chunks))
            self.assertTrue(any("managed dashboard" in item["text"] for item in chunks))
            self.assertTrue(any("diagnose-paths.sh" in item["text"] for item in chunks))
            self.assertEqual({item["sourceSet"] for item in sources}, {"curated-core"})


if __name__ == "__main__":
    unittest.main()

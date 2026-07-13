import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "agentic_rag"))

from agentic_rag.rag_performance_profile import (
    CANDIDATE_85K_CHUNKS,
    CANDIDATE_85K_PROFILE,
    CANDIDATE_85K_TARGET_ID,
    run_candidate_85k_profile,
)


class RagCandidate85kProfileTests(unittest.TestCase):
    def test_named_candidate_profile_refuses_reduced_cardinality(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "exactly 85000 real chunks"):
            run_candidate_85k_profile(Path(tmp), chunk_count=100)

    @unittest.skipUnless(os.getenv("OPEN_NOVA_RUN_SLOW_TESTS") == "1", "slow candidate profile not requested")
    def test_real_candidate_85k_index_load_search_quality_and_resources(self):
        with tempfile.TemporaryDirectory(prefix="open-nova-rag-85k-") as tmp:
            report = run_candidate_85k_profile(Path(tmp))

        print("RAG_CANDIDATE_85K " + json.dumps(report, sort_keys=True), flush=True)
        self.assertEqual(report["profile"], CANDIDATE_85K_PROFILE)
        self.assertEqual(report["validationClass"], "real-index-candidate")
        self.assertEqual(report["expectedChunks"], CANDIDATE_85K_CHUNKS)
        self.assertEqual(report["actualChunks"], CANDIDATE_85K_CHUNKS)
        self.assertGreater(report["indexBytes"], 1_000_000)
        self.assertEqual(len(report["indexSha256"]), 64)
        self.assertFalse(report["timedOut"])
        self.assertEqual(report["qualityStatus"], "strong")
        self.assertEqual(report["topResultId"], CANDIDATE_85K_TARGET_ID)
        self.assertLessEqual(report["searchSeconds"], report["searchBudgetSeconds"])
        self.assertGreater(report["peakRssMB"], 0)
        self.assertTrue(report["passed"])


if __name__ == "__main__":
    unittest.main()

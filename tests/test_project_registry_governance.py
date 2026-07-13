import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from data_foundation.db import migrate, seed_projects
from data_foundation.db import connect
from data_foundation.paths import initialize_home
from data_foundation.project_registry import (
    confirm_project_candidate,
    discover_project_candidates,
    project_registry_status,
    reject_project_candidate,
    write_project_candidates,
)

FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


class ProjectRegistryGovernanceTests(unittest.TestCase):
    def test_readonly_status_reports_registry_and_seeded_db_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_clock = root / "TokenClock"
            nested = root / "TokenClock" / "plugin"
            token_clock.mkdir()
            nested.mkdir(parents=True)
            paths = initialize_home(root / "NovaDiary")
            migrate(paths)
            registry = {
                "version": 1,
                "projects": [
                    {
                        "canonical_name": "TokenClock",
                        "canonical_root": str(token_clock),
                        "enabled": True,
                        "aliases": ["Token Clock"],
                    },
                    {
                        "canonical_name": "TokenClock Plugin",
                        "canonical_root": str(nested),
                        "enabled": True,
                    },
                    {
                        "canonical_name": "Relative",
                        "canonical_root": "relative/path",
                        "enabled": False,
                    },
                ],
            }
            (paths.config_dir / "projects-registry.json").write_text(json.dumps(registry), encoding="utf-8")
            seed_projects(paths, [registry["projects"][0]])

            status = project_registry_status(paths)

        self.assertEqual(status["parseStatus"], "ok")
        self.assertEqual(status["counts"]["projects"], 3)
        self.assertEqual(status["counts"]["enabledProjects"], 2)
        self.assertEqual(status["counts"]["candidates"], 0)
        self.assertEqual(status["counts"]["dbProjects"], 1)
        self.assertEqual(status["authority"]["writesAllowed"], "confirmation-required")
        self.assertEqual(status["authority"]["candidateCreationAllowed"], "structured-cwd-only")
        self.assertIn("initial_cwd", status["authority"]["attribution"])
        issues = {issue["code"] for issue in status["issues"]}
        self.assertIn("overlapping-roots", issues)
        self.assertIn("relative-root", issues)
        self.assertEqual(status["enabledProjects"][0]["canonicalName"], "TokenClock")
        self.assertEqual(status["dbProjects"][0]["aliases"], ["Token Clock"])

    def test_missing_registry_is_attention_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            (paths.config_dir / "projects-registry.json").unlink()

            status = project_registry_status(paths)

        self.assertEqual(status["parseStatus"], "missing")
        self.assertEqual(status["status"], "attention")
        self.assertEqual(status["counts"]["projects"], 0)

    def test_candidate_discovery_uses_structured_cwd_and_skips_confirmed_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "existing"
            candidate = root / "candidate" / "subdir"
            transient = root / "candidate" / "node_modules" / "pkg"
            existing.mkdir()
            candidate.mkdir(parents=True)
            transient.mkdir(parents=True)
            (root / "candidate" / ".git").mkdir()
            paths = initialize_home(root / "NovaDiary")
            migrate(paths)
            (paths.config_dir / "projects-registry.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "projects": [
                            {
                                "canonical_name": "Existing",
                                "canonical_root": str(existing),
                                "enabled": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _insert_session(paths, "codex", str(candidate))
            _insert_session(paths, "claude-code", str(candidate))
            _insert_session(paths, "codex", str(existing))
            _insert_session(paths, "codex", str(transient))

            candidates = discover_project_candidates(paths)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["proposed_canonical_name"], "candidate")
        self.assertEqual(candidates[0]["proposed_canonical_root"], str(root / "candidate"))
        self.assertEqual(candidates[0]["observation_count"], 2)
        self.assertEqual(candidates[0]["tools"], ["claude-code", "codex"])
        self.assertEqual(candidates[0]["evidence"][0]["trust"], "high")

    def test_candidate_confirm_writes_registry_only_after_exact_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_root = root / "new-project"
            candidate_root.mkdir()
            (candidate_root / "package.json").write_text("{}", encoding="utf-8")
            paths = initialize_home(root / "NovaDiary")
            migrate(paths)
            _insert_session(paths, "codex", str(candidate_root))
            candidates = discover_project_candidates(paths)
            created = write_project_candidates(paths, candidates, operator="tester")
            candidate_id = created["candidates"][0]["candidate_id"]

            with self.assertRaises(ValueError):
                confirm_project_candidate(
                    paths,
                    candidate_id,
                    operator="tester",
                    confirmation="confirm wrong",
                    canonical_name="New Project",
                )
            result = confirm_project_candidate(
                paths,
                candidate_id,
                operator="tester",
                confirmation=f"confirm {candidate_id}",
                canonical_name="New Project",
                aliases=["NP"],
                reason="fixture confirmation",
            )
            status = project_registry_status(paths)

            with connect(paths, read_only=True) as connection:
                db_count = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]

        self.assertEqual(result["project"]["canonical_name"], "New Project")
        self.assertEqual(result["candidate"]["status"], "confirmed")
        self.assertEqual(status["counts"]["projects"], 1)
        self.assertEqual(status["counts"]["dbProjects"], 0)
        self.assertEqual(db_count, 0)
        self.assertEqual(status["candidates"][0]["status"], "confirmed")

    def test_candidate_reject_preserves_audit_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_root = root / "scratch"
            candidate_root.mkdir()
            paths = initialize_home(root / "NovaDiary")
            migrate(paths)
            _insert_session(paths, "codex", str(candidate_root))
            created = write_project_candidates(paths, discover_project_candidates(paths), operator="tester")
            candidate_id = created["candidates"][0]["candidate_id"]

            rejected = reject_project_candidate(
                paths,
                candidate_id,
                operator="tester",
                confirmation=f"reject {candidate_id}",
                reason="scratch path",
            )
            registry = json.loads((paths.config_dir / "projects-registry.json").read_text(encoding="utf-8"))

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(registry["audit"][-1]["action"], "candidate-rejected")
        self.assertEqual(registry["candidates"][0]["rejection_reason"], "scratch path")

    @unittest.skipUnless(FASTAPI_AVAILABLE, "Dashboard runtime dependency fastapi is not installed in this interpreter")
    def test_foundation_ops_project_registry_router_uses_service_facade(self):
        from app.routers import foundation_ops as ops_router

        with patch.object(ops_router.foundation_ops, "get_project_registry_status", return_value={"ok": True}) as status:
            response = asyncio.run(ops_router.api_foundation_project_registry_status())

        self.assertEqual(response, {"ok": True})
        status.assert_called_once_with()

def _insert_session(paths, tool_key: str, cwd: str) -> None:
    with connect(paths) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO tool_sources(
                tool_key, display_name, adapter_version, capabilities_json,
                enabled, created_at, updated_at
            ) VALUES (?, ?, 'test', '{}', 1, '2026-05-19T00:00:00+08:00', '2026-05-19T00:00:00+08:00')
            """,
            (tool_key, tool_key),
        )
        connection.execute(
            """
            INSERT INTO sessions(tool_key, external_session_key, started_at, last_active_at, initial_cwd, metadata_json)
            VALUES (?, ?, '2026-05-19T00:00:00+08:00', '2026-05-19T00:01:00+08:00', ?, '{}')
            """,
            (tool_key, f"{tool_key}:{cwd}", cwd),
        )


if __name__ == "__main__":
    unittest.main()

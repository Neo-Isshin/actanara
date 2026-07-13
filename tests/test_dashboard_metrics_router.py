import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))


class DashboardMetricsRouterTests(unittest.TestCase):
    def test_cron_status_messages_are_profile_aware_display_copy(self):
        router = (ROOT / "src" / "dashboard" / "app" / "routers" / "metrics.py").read_text(encoding="utf-8")

        self.assertIn("def _cron_status_message", router)
        self.assertIn('"msg": _cron_status_message("missing")', router)
        self.assertIn("_CRON_STATUS_TEXT", router)
        self.assertIn('"速查表不存在"', router)
        self.assertIn('"有任务失败"', router)
        self.assertIn('"全部正常"', router)
        self.assertIn('"Quick-reference table not found"', router)
        self.assertIn('"Some scheduled jobs failed"', router)
        self.assertIn('"All scheduled jobs normal"', router)
        self.assertIn('"status": "unknown"', router)
        self.assertIn('"status": "error" if failed else "ok"', router)


if __name__ == "__main__":
    unittest.main()

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))


from app.routers import metrics
from app.services import token_clock


class DashboardTokenClockConcurrencyTests(unittest.TestCase):
    def test_slow_scan_leaves_event_loop_available_for_another_async_route(self):
        async def scenario():
            loop_thread = threading.get_ident()
            scan_started = threading.Event()
            probe_completed = threading.Event()
            release_scan = threading.Event()
            watcher_timed_out = threading.Event()
            scanner_threads = []

            def slow_scan():
                scanner_threads.append(threading.get_ident())
                scan_started.set()
                if not release_scan.wait(timeout=2):
                    raise TimeoutError("test scan was not released")
                return {"status": "ready"}

            def release_after_probe():
                if not scan_started.wait(timeout=1) or not probe_completed.wait(timeout=0.5):
                    watcher_timed_out.set()
                release_scan.set()

            watcher = threading.Thread(target=release_after_probe, daemon=True)
            watcher.start()
            with patch.object(token_clock, "get_token_clock_data", side_effect=slow_scan):
                token_task = asyncio.create_task(metrics.api_token_clock())
                while not scan_started.is_set():
                    await asyncio.sleep(0)

                with patch.object(
                    metrics.tokens,
                    "compute_summary",
                    return_value={"today": {}, "week": {}, "summary": {"total": 0, "count": 0}},
                ):
                    other_response = await asyncio.wait_for(metrics.api_tokens(), timeout=0.2)
                probe_completed.set()
                token_result = await asyncio.wait_for(token_task, timeout=1)
            watcher.join(timeout=1)

            self.assertEqual(other_response["dashboardState"]["status"], "empty")
            self.assertEqual(token_result, {"status": "ready"})
            self.assertFalse(watcher_timed_out.is_set())
            self.assertEqual(len(scanner_threads), 1)
            self.assertNotEqual(scanner_threads[0], loop_thread)

        asyncio.run(scenario())

    def test_snapshot_builder_is_serialized_across_worker_threads(self):
        active = 0
        max_active = 0
        state_lock = threading.Lock()
        first_started = threading.Event()
        release_first = threading.Event()

        def build_snapshot():
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
                is_first = not first_started.is_set()
                first_started.set()
            if is_first and not release_first.wait(timeout=1):
                raise TimeoutError("first snapshot was not released")
            with state_lock:
                active -= 1
            return {"status": "ready"}

        with patch.object(token_clock, "_get_token_clock_data_unlocked", side_effect=build_snapshot):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(token_clock.get_token_clock_data)
                self.assertTrue(first_started.wait(timeout=1))
                second = pool.submit(token_clock.get_token_clock_data)
                self.assertFalse(second.done())
                release_first.set()
                self.assertEqual(first.result(timeout=1), {"status": "ready"})
                self.assertEqual(second.result(timeout=1), {"status": "ready"})

        self.assertEqual(max_active, 1)


if __name__ == "__main__":
    unittest.main()

import asyncio
import unittest
from unittest.mock import patch

from dashboard.app.services import scheduler


class DashboardBackupSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_tick_checks_foundation_and_backup_independently(self):
        stop = asyncio.Event()
        calls = []

        async def fake_to_thread(function, *args, **kwargs):
            calls.append(function)
            if len(calls) == 2:
                stop.set()
            return {"status": "ok"}

        previous = scheduler._stop_event
        scheduler._stop_event = stop
        try:
            with (
                patch.object(scheduler.asyncio, "to_thread", side_effect=fake_to_thread),
                patch.object(scheduler.logger, "exception"),
            ):
                await scheduler._scheduler_loop()
        finally:
            scheduler._stop_event = previous

        self.assertEqual(calls, [scheduler.run_due_snapshot_refresh, scheduler.backups.run_due_backup])

    async def test_backup_tick_failure_does_not_escape_or_repeat_after_stop(self):
        stop = asyncio.Event()
        calls = []

        async def fake_to_thread(function, *args, **kwargs):
            calls.append(function)
            if function is scheduler.backups.run_due_backup:
                stop.set()
                raise RuntimeError("isolated backup failure")
            return {"status": "ok"}

        previous = scheduler._stop_event
        scheduler._stop_event = stop
        try:
            with (
                patch.object(scheduler.asyncio, "to_thread", side_effect=fake_to_thread),
                patch.object(scheduler.logger, "exception"),
            ):
                await scheduler._scheduler_loop()
        finally:
            scheduler._stop_event = previous

        self.assertEqual(calls, [scheduler.run_due_snapshot_refresh, scheduler.backups.run_due_backup])


if __name__ == "__main__":
    unittest.main()

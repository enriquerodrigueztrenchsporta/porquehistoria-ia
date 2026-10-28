import json
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from instagram_pipeline.schedule import in_publication_window, target_for, wait_for_publication
from instagram_pipeline.state import Ledger, StateError


class ScheduleTests(unittest.TestCase):
    def test_summer_and_winter_are_20_local(self):
        self.assertEqual(target_for(date(2026, 7, 1)).astimezone(timezone.utc).hour, 18)
        self.assertEqual(target_for(date(2026, 12, 1)).astimezone(timezone.utc).hour, 19)

    def test_dst_transition_day(self):
        self.assertEqual(target_for(date(2026, 3, 29)).astimezone(timezone.utc).hour, 18)
        self.assertEqual(target_for(date(2026, 10, 25)).astimezone(timezone.utc).hour, 19)

    def test_does_not_publish_before_or_long_after_target(self):
        day = date(2026, 10, 28)
        self.assertFalse(in_publication_window(datetime(2026, 10, 28, 18, 59, tzinfo=timezone.utc), day))
        self.assertTrue(in_publication_window(datetime(2026, 10, 28, 19, 1, tzinfo=timezone.utc), day))
        self.assertFalse(in_publication_window(datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc), day))

    def test_wait_never_sleeps_more_than_30_seconds(self):
        ticks = iter([datetime(2026, 10, 28, 18, 59, tzinfo=timezone.utc),
                      datetime(2026, 10, 28, 19, 0, tzinfo=timezone.utc),
                      datetime(2026, 10, 28, 19, 0, tzinfo=timezone.utc)])
        sleeps = []
        wait_for_publication(date(2026, 10, 28), now=lambda: next(ticks), sleep=sleeps.append)
        self.assertEqual(sleeps, [30])


class LedgerTests(unittest.TestCase):
    def test_ledger_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            ledger = Ledger(path)
            ledger.data["days"]["2026-10-28"] = {"journal": {"state": "publish_requested"}}
            ledger.save()
            self.assertEqual(Ledger(path).data["days"]["2026-10-28"]["journal"]["state"], "publish_requested")

    def test_credentials_cannot_be_committed(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(Path(folder) / "state.json")
            ledger.data["access_token"] = "fake"
            with self.assertRaises(StateError):
                ledger.save()

    def test_remote_conflict_fails_before_local_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            ledger = Ledger(path)
            ledger.repository, ledger.token, ledger.sha = "owner/repo", "not-a-secret", "initial"
            with patch.object(ledger, "_request", side_effect=StateError("conflict")):
                with self.assertRaises(StateError):
                    ledger.save()
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()

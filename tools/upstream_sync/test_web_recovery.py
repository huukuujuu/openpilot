import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import recover_web as recovery
from schedule_watch import notice


class WebRecoveryTest(unittest.TestCase):
  def test_mixed_conflict_stops_before_build(self):
    with patch.object(recovery, "git") as git:
      git.return_value.returncode = 1
      git.return_value.stdout = "tree\n" + "\n".join(sorted(recovery.FILES)) + "\nsource.js\n\nmessages"
      with self.assertRaisesRegex(ValueError, "confined"):
        recovery.conflict_tree("base", "source")
      self.assertEqual(git.call_count, 1)

  def test_removed_bundle_stops_recovery(self):
    with patch.object(recovery, "git") as git:
      git.side_effect = [
        type("Result", (), {"returncode": 1, "stdout": "tree\n" + sorted(recovery.FILES)[0] + "\n\nmessages"}),
        type("Result", (), {"stdout": ""}),
      ]
      with self.assertRaisesRegex(ValueError, "Missing"):
        recovery.conflict_tree("base", "source")

  def test_monitor_boundary(self):
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    self.assertTrue(notice([], now))
    for age, stale in [(179, False), (180, True), (181, True)]:
      self.assertEqual(bool(notice([{"created_at": (now - timedelta(minutes=age)).isoformat()}], now)), stale)

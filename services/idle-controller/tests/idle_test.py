import time
import unittest

from idle_controller.main import should_stop


class IdleTests(unittest.TestCase):
    def test_stop_when_idle_over_threshold(self):
        now = int(time.time())
        self.assertTrue(should_stop(last_active_ts=now - 1900, idle_minutes=30, now_ts=now))

    def test_keep_when_recently_active(self):
        now = int(time.time())
        self.assertFalse(should_stop(last_active_ts=now - 120, idle_minutes=30, now_ts=now))


if __name__ == "__main__":
    unittest.main()

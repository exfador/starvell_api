import tempfile
import unittest
from pathlib import Path

from tg_bot_exfa.instance_lock import InstanceLock, InstanceLockError


class InstanceLockTests(unittest.TestCase):
    def test_second_instance_is_refused_until_first_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.lock"
            first = InstanceLock(path)
            second = InstanceLock(path)
            first.acquire(timeout=0)
            try:
                with self.assertRaises(InstanceLockError):
                    second.acquire(timeout=0.2, poll_interval=0.05)
            finally:
                first.release()

            second.acquire(timeout=0)
            second.release()

    def test_release_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = InstanceLock(Path(directory) / "nested" / "bot.lock")
            lock.acquire(timeout=0)
            lock.release()
            lock.release()


if __name__ == "__main__":
    unittest.main()

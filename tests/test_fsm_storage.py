import tempfile
import unittest
from pathlib import Path

from aiogram.fsm.storage.base import StorageKey

from tg_bot_exfa.storage.fsm import SQLiteStorage


class SQLiteFsmStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_state_and_data_survive_storage_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.sqlite3"
            key = StorageKey(bot_id=1, chat_id=2, user_id=3)
            first = SQLiteStorage(path)
            await first.init()
            await first.set_state(key, "Flow:step")
            await first.set_data(key, {"secret": "value"})

            second = SQLiteStorage(path)
            await second.init()

            self.assertEqual(await second.get_state(key), "Flow:step")
            self.assertEqual(await second.get_data(key), {"secret": "value"})

    async def test_update_data_merges_without_losing_existing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(Path(directory) / "bot.sqlite3")
            await storage.init()
            key = StorageKey(bot_id=1, chat_id=2, user_id=3)
            await storage.set_data(key, {"first": 1})

            updated = await storage.update_data(key, {"second": 2})

            self.assertEqual(updated, {"first": 1, "second": 2})


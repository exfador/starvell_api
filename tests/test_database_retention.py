import tempfile
import time
import unittest
from pathlib import Path

import aiosqlite

from tg_bot_exfa.storage.db import Database


class DatabaseRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "bot.sqlite3"
        self.db = Database(str(self.path))
        await self.db.init()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_prunes_only_completed_runtime_history(self):
        now = int(time.time())
        old = now - 200 * 86400
        recent = now - 10 * 86400
        async with aiosqlite.connect(self.path) as conn:
            await conn.executemany(
                "INSERT INTO orders_notified(order_id, created_at) VALUES(?, ?)",
                [("old", old), ("recent", recent)],
            )
            await conn.executemany(
                "INSERT INTO notification_receipts(event_kind, event_id, user_id, created_at) VALUES(?, ?, ?, ?)",
                [("chat", "old", 1, old), ("chat", "recent", 1, recent)],
            )
            await conn.execute(
                "INSERT INTO order_deliveries(order_id, product, quantity, item_ids, item_values, state, updated_at) "
                "VALUES('done-old', 'p', 1, '[]', '[]', 'owner_notified', ?)",
                (old,),
            )
            await conn.execute(
                "INSERT INTO order_deliveries(order_id, product, quantity, item_ids, item_values, state, updated_at) "
                "VALUES('pending-old', 'p', 1, '[]', '[]', 'sending', ?)",
                (old,),
            )
            await conn.commit()

        deleted = await self.db.prune_runtime_state(retention_days=90, now=now)

        self.assertEqual(deleted["orders_notified"], 1)
        self.assertEqual(deleted["notification_receipts"], 1)
        self.assertEqual(deleted["order_deliveries"], 1)
        async with aiosqlite.connect(self.path) as conn:
            notified = await (await conn.execute("SELECT order_id FROM orders_notified ORDER BY order_id")).fetchall()
            deliveries = await (await conn.execute("SELECT order_id FROM order_deliveries ORDER BY order_id")).fetchall()
        self.assertEqual(notified, [("recent",)])
        self.assertEqual(deliveries, [("pending-old",)])


if __name__ == "__main__":
    unittest.main()

import asyncio
import stat
import tempfile
import unittest
from pathlib import Path

from tg_bot_exfa.storage.db import Database


class AutodeliveryDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_items_are_only_removed_after_explicit_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.sqlite3"))
            await database.init()
            self.assertEqual(stat.S_IMODE(Path(database.path).stat().st_mode), 0o600)
            await database.add_autodelivery_items("Robux", ["code-1", "code-2"])

            items = await database.peek_autodelivery_items("Robux", limit=2)

            self.assertEqual([item["value"] for item in items], ["code-1", "code-2"])
            self.assertEqual(await database.count_autodelivery("Robux"), 2)

            deleted = await database.delete_autodelivery_items([items[0]["id"]])

            self.assertEqual(deleted, 1)
            self.assertEqual(await database.count_autodelivery("Robux"), 1)

    async def test_password_rotation_revokes_other_authorized_users(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.sqlite3"))
            await database.init()
            for user_id in (1, 2):
                await database.get_user(user_id)
                await database.set_authorized(user_id, True)

            revoked = await database.revoke_authorizations(except_user_id=1)

            self.assertEqual(revoked, 1)
            self.assertTrue((await database.get_user(1))["authorized"])
            self.assertFalse((await database.get_user(2))["authorized"])

    async def test_reservation_requires_the_full_order_quantity(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.sqlite3"))
            await database.init()
            await database.add_autodelivery_items("Robux", ["only-code"])

            reservation = await database.reserve_order_delivery("order-1", "Robux", 2)

            self.assertEqual(reservation["state"], "insufficient")
            self.assertEqual(reservation["available"], 1)
            self.assertEqual(await database.count_autodelivery("Robux"), 1)
            self.assertIsNone(await database.get_order_delivery("order-1"))

    async def test_two_processes_cannot_reserve_the_same_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "bot.sqlite3")
            first = Database(path)
            second = Database(path)
            await first.init()
            await first.add_autodelivery_items("Robux", ["code-1"])

            reservations = await asyncio.gather(
                first.reserve_order_delivery("order-1", "Robux", 1),
                second.reserve_order_delivery("order-2", "Robux", 1),
            )

            states = sorted(result["state"] for result in reservations)
            self.assertEqual(states, ["insufficient", "reserved"])
            self.assertEqual(await first.count_autodelivery("Robux"), 0)

    async def test_successful_delivery_consumes_only_reserved_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.sqlite3"))
            await database.init()
            await database.add_autodelivery_items("Robux", ["code-1", "code-2"])
            await database.reserve_order_delivery("order-1", "Robux", 1)
            self.assertTrue(await database.mark_order_delivery_sending("order-1", "chat-1"))

            deleted = await database.mark_order_delivery_sent("order-1")

            self.assertEqual(deleted, 1)
            self.assertEqual(await database.count_autodelivery("Robux"), 1)
            delivery = await database.get_order_delivery("order-1")
            self.assertEqual(delivery["state"], "buyer_sent")

    async def test_product_deletion_does_not_destroy_an_in_flight_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.sqlite3"))
            await database.init()
            await database.add_autodelivery_items("Robux", ["reserved", "free"])
            await database.reserve_order_delivery("order-1", "Robux", 1)

            deleted = await database.delete_autodelivery_product("Robux")

            self.assertEqual(deleted, 1)
            self.assertEqual(await database.list_autodelivery_products(), [])
            self.assertTrue(await database.mark_order_delivery_sending("order-1", "chat-1"))
            self.assertEqual(await database.mark_order_delivery_sent("order-1"), 1)

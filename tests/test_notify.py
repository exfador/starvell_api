import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import tg_bot_exfa.app as app
from tg_bot_exfa.notify import (
    NotificationDeliveryError,
    _deliver_notification,
    _notification_bot,
    send_order_completed_notification,
)
from tg_bot_exfa.storage.db import Database


class NotificationBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        app.app_context = None

    async def test_reuses_running_bot_session(self):
        shared_bot = object()
        app.app_context = SimpleNamespace(bot=shared_bot)

        bot, owns_bot = _notification_bot(SimpleNamespace(token="unused"))

        self.assertIs(bot, shared_bot)
        self.assertFalse(owns_bot)

    async def test_completed_order_uses_order_notification_preference(self):
        fake_bot = SimpleNamespace(send_message=AsyncMock())
        order = {
            "id": "order-1",
            "user": {"username": "buyer"},
            "offerDetails": {"game": {"name": "game"}, "category": {"name": "category"}},
        }

        with (
            patch("tg_bot_exfa.notify.load_config", return_value=SimpleNamespace(token="token")),
            patch("tg_bot_exfa.notify._notification_bot", return_value=(fake_bot, False)),
            patch("tg_bot_exfa.notify._close_owned_bot", AsyncMock()),
            patch("tg_bot_exfa.notify._recipients", AsyncMock(return_value=[(1, "ru")])) as recipients,
        ):
            delivered = await send_order_completed_notification(order)

        recipients.assert_awaited_once_with("notify_orders")
        self.assertEqual(delivered, 1)

    async def test_successful_recipient_is_not_spammed_when_another_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.sqlite3"))
            await database.init()
            app.app_context = SimpleNamespace(db=database)
            calls: list[int] = []
            fail_second = True

            async def send_one(user_id, language):
                nonlocal fail_second
                calls.append(user_id)
                if user_id == 2 and fail_second:
                    fail_second = False
                    raise RuntimeError("blocked")

            with self.assertRaises(NotificationDeliveryError):
                await _deliver_notification(
                    "order_created",
                    "order-1",
                    [(1, "ru"), (2, "ru")],
                    send_one,
                )
            delivered = await _deliver_notification(
                "order_created",
                "order-1",
                [(1, "ru"), (2, "ru")],
                send_one,
            )

            self.assertEqual(delivered, 2)
            self.assertEqual(calls, [1, 2, 2])

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramForbiddenError

import tg_bot_exfa.app as app
from tg_bot_exfa.notify import (
    NotificationDeliveryError,
    _deliver_notification,
    _notification_bot,
    _send_text,
    send_bump_summary,
    send_order_completed_notification,
    send_order_notification,
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

    async def test_autodelivery_warning_is_translated_and_escaped(self):
        fake_bot = SimpleNamespace(send_message=AsyncMock())
        order = {"id": "order-1", "user": {"username": "buyer"}, "offerDetails": {}}

        with (
            patch("tg_bot_exfa.notify.load_config", return_value=SimpleNamespace(token="token")),
            patch("tg_bot_exfa.notify._notification_bot", return_value=(fake_bot, False)),
            patch("tg_bot_exfa.notify._close_owned_bot", AsyncMock()),
            patch("tg_bot_exfa.notify._recipients", AsyncMock(return_value=[(1, "ru"), (2, "en")])),
        ):
            await send_order_notification(
                order,
                event_id="order-1:autodelivery-insufficient",
                ad_warning=("<Robux>", "ad_fail_insufficient", {"required": 2, "available": "<1>"}),
            )

        ru_text = fake_bot.send_message.await_args_list[0].args[1]
        en_text = fake_bot.send_message.await_args_list[1].args[1]
        self.assertIn("Автовыдача не выполнена", ru_text)
        self.assertIn("<code>&lt;Robux&gt;</code>", ru_text)
        self.assertIn("в наличии <code>&lt;1&gt;</code>", ru_text)
        self.assertIn("Autodelivery failed", en_text)
        self.assertIn("need <code>2</code>", en_text)

    async def test_opted_out_recipients_count_as_delivered(self):
        send_one = AsyncMock()
        with patch("tg_bot_exfa.notify._recipients_authorized", AsyncMock(return_value=[(1, "ru")])):
            delivered = await _deliver_notification("chat", "message-1", [], send_one)

        self.assertEqual(delivered, 0)
        send_one.assert_not_awaited()

    async def test_without_authorized_users_event_stays_pending(self):
        with (
            patch("tg_bot_exfa.notify._recipients_authorized", AsyncMock(return_value=[])),
            self.assertRaises(NotificationDeliveryError),
        ):
            await _deliver_notification("chat", "message-1", [], AsyncMock())

    async def test_recipient_who_blocked_the_bot_does_not_block_others(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.sqlite3"))
            await database.init()
            app.app_context = SimpleNamespace(db=database)
            blocked = TelegramForbiddenError(method=SimpleNamespace(), message="bot was blocked by the user")
            send_one = AsyncMock(side_effect=[blocked, None])

            delivered = await _deliver_notification("order_created", "order-1", [(1, "ru"), (2, "ru")], send_one)
            repeated = await _deliver_notification("order_created", "order-1", [(1, "ru"), (2, "ru")], send_one)

            self.assertEqual(delivered, 1)
            self.assertEqual(repeated, 2)
            self.assertEqual(send_one.await_count, 2)

    async def test_text_over_telegram_limit_is_sent_whole_as_document(self):
        bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
        text = "<b>Заказ</b>\n" + "код-&amp;\n" * 1000

        await _send_text(bot, 1, text, filename="order-1.txt")

        bot.send_message.assert_not_awaited()
        kwargs = bot.send_document.await_args.kwargs
        document = bot.send_document.await_args.args[1]
        self.assertEqual(document.filename, "order-1.txt")
        self.assertIn("код-&\n", document.data.decode("utf-8"))
        self.assertNotIn("<b>", document.data.decode("utf-8"))
        self.assertLessEqual(len(kwargs["caption"]), 1024)
        self.assertIsNone(kwargs["parse_mode"])

    async def test_short_text_is_sent_as_message(self):
        bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())

        await _send_text(bot, 1, "<b>ok</b>")

        bot.send_message.assert_awaited_once()
        bot.send_document.assert_not_awaited()

    async def test_bump_summary_is_one_escaped_message(self):
        fake_bot = SimpleNamespace(send_message=AsyncMock())
        lots = [{"title": f"<Lot {index}>", "url": f"https://starvell.com/offers/{index}"} for index in range(35)]
        with (
            patch("tg_bot_exfa.notify.load_config", return_value=SimpleNamespace(token="token")),
            patch("tg_bot_exfa.notify._notification_bot", return_value=(fake_bot, False)),
            patch("tg_bot_exfa.notify._close_owned_bot", AsyncMock()),
            patch("tg_bot_exfa.notify._recipients", AsyncMock(return_value=[(1, "ru")])),
        ):
            delivered = await send_bump_summary(lots)

        self.assertEqual(delivered, 1)
        text = fake_bot.send_message.await_args.args[1]
        self.assertIn("(35)", text)
        self.assertIn("&lt;Lot 0&gt;", text)
        self.assertIn("…и ещё 5", text)

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

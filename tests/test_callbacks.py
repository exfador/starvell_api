import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import tg_bot_exfa.handlers.callbacks as callbacks
from tg_bot_exfa.handlers.callbacks import _parse_autodelivery_values, _send_reply_image_from_state


class AutodeliveryImportTests(unittest.TestCase):
    def test_colons_inside_a_code_are_preserved(self):
        values = _parse_autodelivery_values("login:password\ncode:2")

        self.assertEqual(values, ["login:password", "code", "code"])

    def test_huge_repeat_is_rejected_before_expansion(self):
        with self.assertRaisesRegex(ValueError, "repeat"):
            _parse_autodelivery_values("code:1000000000")


class StatsSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        callbacks._stats_cache = None

    async def test_documented_order_list_is_used_and_cached(self):
        orders = [{"id": "1", "createdAt": "2026-10-01T10:00:00Z"}]
        with (
            patch("tg_bot_exfa.handlers.callbacks.fetch_seller_orders_all", AsyncMock(return_value=orders)) as fresh,
            patch("tg_bot_exfa.handlers.callbacks.fetch_sells_all", AsyncMock()) as legacy,
        ):
            first = await callbacks._sells_for_stats("session")
            second = await callbacks._sells_for_stats("session")

        self.assertEqual(first, orders)
        self.assertIs(second, first)
        fresh.assert_awaited_once()
        legacy.assert_not_awaited()

    async def test_falls_back_to_sells_pages_when_order_list_is_unusable(self):
        legacy_orders = [{"id": "1", "createdAt": "2026-10-01T10:00:00Z"}]
        for failure in (AsyncMock(side_effect=RuntimeError("404")), AsyncMock(return_value=[{"id": "1"}])):
            callbacks._stats_cache = None
            with (
                patch("tg_bot_exfa.handlers.callbacks.fetch_seller_orders_all", failure),
                patch("tg_bot_exfa.handlers.callbacks.fetch_sells_all", AsyncMock(return_value=legacy_orders)),
                self.assertLogs("exfador.handlers", level="WARNING"),
            ):
                self.assertEqual(await callbacks._sells_for_stats("session"), legacy_orders)


class TextReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_formatted_telegram_reply_reaches_starvell_as_plain_text(self):
        message = SimpleNamespace(
            text="Ссылка: https://x.com/?a=1&b=2",
            html_text='Ссылка: <a href="https://x.com/?a=1&amp;b=2">https://x.com/?a=1&amp;b=2</a>',
            entities=[object()],
            chat=SimpleNamespace(id=10),
            from_user=SimpleNamespace(id=1),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"reply_chat_id": "chat-1"}))
        context = SimpleNamespace(
            db=SimpleNamespace(get_user=AsyncMock(return_value={"language": "ru"})),
            config=SimpleNamespace(default_language="ru"),
        )
        send = AsyncMock(return_value=(True, None, "chat-1"))
        callbacks = __import__("tg_bot_exfa.handlers.callbacks", fromlist=["app"])
        with (
            patch("tg_bot_exfa.handlers.callbacks._send_reply_from_state", send),
            patch.object(callbacks.app, "app_context", context),
        ):
            await callbacks.handle_chat_reply_text(message, state)

        self.assertEqual(send.await_args.args[3], "Ссылка: https://x.com/?a=1&b=2")


class ImageReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_caption_is_sent_as_text_after_image(self):
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "reply_chat_id": "chat-1",
                    "notification_chat_id": 10,
                    "notification_message_id": 20,
                    "original_text": "original",
                    "original_kind": "text",
                    "original_lang": "ru",
                }
            ),
            clear=AsyncMock(),
        )
        bot = SimpleNamespace(
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
        )
        image_send = AsyncMock(return_value={"success": True})
        text_send = AsyncMock(return_value={"success": True})
        mark_read = AsyncMock(return_value={})
        context = SimpleNamespace(config=SimpleNamespace(watermark_on=False))
        with (
            patch("tg_bot_exfa.handlers.callbacks.mark_chat_read", mark_read),
            patch("tg_bot_exfa.handlers.callbacks.load_osnova_config", return_value={"SESSION_COOKIE": "session"}),
            patch(
                "tg_bot_exfa.handlers.callbacks.fetch_homepage_data",
                AsyncMock(return_value={"authorized": True, "user": {"id": 1}, "my_games": "14"}),
            ),
            patch("tg_bot_exfa.handlers.callbacks.send_chat_image", image_send),
            patch("tg_bot_exfa.handlers.callbacks.send_chat_message", text_send),
            patch.object(__import__("tg_bot_exfa.handlers.callbacks", fromlist=["app"]).app, "app_context", context),
        ):
            success, error, chat_id = await _send_reply_image_from_state(
                bot,
                state,
                "ru",
                b"image",
                "image.png",
                "image/png",
                "caption",
                10,
                20,
                1,
            )

        self.assertTrue(success)
        self.assertIsNone(error)
        self.assertEqual(chat_id, "chat-1")
        self.assertIsNone(image_send.await_args.kwargs["content"])
        text_send.assert_awaited_once_with("session", "chat-1", "caption", my_games_cookie="14")
        mark_read.assert_awaited_once_with("session", "chat-1", my_games_cookie="14")
        state.clear.assert_awaited_once()


class MarkReadTests(unittest.IsolatedAsyncioTestCase):
    def _callback(self):
        return SimpleNamespace(
            data="chat:read:chat-7",
            from_user=SimpleNamespace(id=1),
            answer=AsyncMock(),
        )

    def _context(self):
        return SimpleNamespace(
            db=SimpleNamespace(get_user=AsyncMock(return_value={"language": "en"})),
            config=SimpleNamespace(default_language="ru"),
        )

    async def test_button_marks_chat_read_and_confirms(self):
        callback = self._callback()
        mark_read = AsyncMock(return_value={})
        with (
            patch("tg_bot_exfa.handlers.callbacks.mark_chat_read", mark_read),
            patch("tg_bot_exfa.handlers.callbacks.load_osnova_config", return_value={"SESSION_COOKIE": "session"}),
            patch.object(callbacks.app, "app_context", self._context()),
        ):
            await callbacks.mark_chat_read_from_notification(callback)

        mark_read.assert_awaited_once_with("session", "chat-7")
        callback.answer.assert_awaited_once_with("Chat marked as read")

    async def test_failure_is_shown_as_alert(self):
        callback = self._callback()
        with (
            patch("tg_bot_exfa.handlers.callbacks.mark_chat_read", AsyncMock(side_effect=RuntimeError("403"))),
            patch("tg_bot_exfa.handlers.callbacks.load_osnova_config", return_value={"SESSION_COOKIE": "session"}),
            patch.object(callbacks.app, "app_context", self._context()),
            self.assertLogs("exfador.handlers", level="WARNING"),
        ):
            await callbacks.mark_chat_read_from_notification(callback)

        self.assertIn("403", callback.answer.await_args.args[0])
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_failed_mark_never_fails_the_reply(self):
        with (
            patch("tg_bot_exfa.handlers.callbacks.mark_chat_read", AsyncMock(side_effect=RuntimeError("down"))),
            self.assertLogs("exfador.handlers", level="WARNING"),
        ):
            await callbacks._mark_read_quietly("session", "chat-7")

    def test_chat_notification_offers_mark_read(self):
        markup = callbacks.kb.chat_notification(lambda key: key, "chat-7", "https://starvell.com/chat/chat-7").as_markup()
        data = [button.callback_data for row in markup.inline_keyboard for button in row]

        self.assertIn("chat:read:chat-7", data)

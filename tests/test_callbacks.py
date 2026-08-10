import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tg_bot_exfa.handlers.callbacks import _parse_autodelivery_values, _send_reply_image_from_state


class AutodeliveryImportTests(unittest.TestCase):
    def test_colons_inside_a_code_are_preserved(self):
        values = _parse_autodelivery_values("login:password\ncode:2")

        self.assertEqual(values, ["login:password", "code", "code"])

    def test_huge_repeat_is_rejected_before_expansion(self):
        with self.assertRaisesRegex(ValueError, "repeat"):
            _parse_autodelivery_values("code:1000000000")


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
        context = SimpleNamespace(config=SimpleNamespace(watermark_on=False))
        with (
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
        state.clear.assert_awaited_once()

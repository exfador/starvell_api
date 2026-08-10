import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import CallbackQuery, Chat, Message, User

import tg_bot_exfa.app as app
from tg_bot_exfa.middleware import AuthorizedPrivateMiddleware, PrivateChatMiddleware


def make_message(chat_type: str = "private") -> Message:
    user = User(id=42, is_bot=False, first_name="Admin")
    chat = Chat(id=42, type=chat_type)
    return Message(message_id=1, date=datetime.now(UTC), chat=chat, from_user=user, text="test")


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        app.app_context = None

    async def test_authorized_private_event_reaches_handler(self):
        database = SimpleNamespace(get_user=AsyncMock(return_value={"authorized": 1}))
        app.app_context = SimpleNamespace(db=database)
        handler = AsyncMock(return_value="ok")
        event = make_message()

        result = await AuthorizedPrivateMiddleware()(handler, event, {})

        self.assertEqual(result, "ok")
        handler.assert_awaited_once()

    async def test_unauthorized_callback_is_rejected(self):
        database = SimpleNamespace(get_user=AsyncMock(return_value={"authorized": 0}))
        app.app_context = SimpleNamespace(db=database)
        handler = AsyncMock()
        message = make_message()
        callback = CallbackQuery(
            id="callback-1",
            from_user=message.from_user,
            chat_instance="instance",
            message=message,
            data="settings:change_token",
        )
        answer = AsyncMock()

        with patch.object(CallbackQuery, "answer", answer):
            await AuthorizedPrivateMiddleware()(handler, callback, {})

        handler.assert_not_awaited()
        answer.assert_awaited_once()

    async def test_group_authentication_is_rejected(self):
        event = make_message("group")
        handler = AsyncMock()
        answer = AsyncMock()

        with patch.object(Message, "answer", answer):
            await PrivateChatMiddleware()(handler, event, {})

        handler.assert_not_awaited()
        answer.assert_awaited_once()

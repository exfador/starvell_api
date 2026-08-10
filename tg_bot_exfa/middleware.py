from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

import tg_bot_exfa.app as app


class AuthorizedPrivateMiddleware(BaseMiddleware):
    """Reject privileged Telegram events outside an authorized private chat."""

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        is_callback = isinstance(event, CallbackQuery)
        message = event.message if is_callback else event
        chat = getattr(message, "chat", None)
        user = getattr(event, "from_user", None)
        context = app.app_context
        allowed = False
        if context is not None and user is not None and getattr(chat, "type", None) == "private":
            record = await context.db.get_user(user.id)
            allowed = bool(record.get("authorized"))
        if allowed:
            return await handler(event, data)
        if is_callback:
            await event.answer("Доступ запрещён. Откройте бота в личном чате и выполните /start.", show_alert=True)
        elif isinstance(event, Message):
            await event.answer("Доступ запрещён. Используйте /start в личном чате.")
        return None


class PrivateChatMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict[str, Any]) -> Any:
        if getattr(getattr(event, "chat", None), "type", None) == "private":
            return await handler(event, data)
        await event.answer("Этот бот управляется только в личном чате.")
        return None


def protect_router(router) -> None:
    router.message.outer_middleware(AuthorizedPrivateMiddleware())
    router.callback_query.outer_middleware(AuthorizedPrivateMiddleware())

import logging

import aiohttp

from api.cookies import build_cookies, capture_cookies
from api.http_headers import api_headers
from api.rate_limiter import throttle


async def fetch_chat_messages(
    session_cookie: str,
    chat_id: str,
    limit: int = 50,
    my_games_cookie: str | None = None,
    interlocutor_id: int | None = None,
) -> list[dict]:
    headers = api_headers("https://starvell.com/chat")
    cookies = build_cookies(session_cookie, my_games_cookie=my_games_cookie)
    timeout = aiohttp.ClientTimeout(total=20)

    if interlocutor_id is not None:
        url = "https://starvell.com/api/bff/chat-page"
        payload = {
            "interlocutorId": int(interlocutor_id),
            "messagesListDto": {"chatId": chat_id, "limit": limit},
        }
        async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
            await throttle()
            async with session.post(url, json=payload) as resp:
                capture_cookies(session.cookie_jar)
                resp.raise_for_status()
                data = await resp.json()
                if isinstance(data, dict):
                    items = (data.get("messagesListResult") or {}).get("items")
                    if isinstance(items, list):
                        return items
                return []

    logging.getLogger("exfador.monitor").warning(
        "fetch_chat_messages called without interlocutor_id; /api/messages/list is deprecated (404), returning []"
    )
    return []

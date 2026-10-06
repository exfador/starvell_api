from collections.abc import Mapping

from api.http_client import request_json
from api.response import response_items

MESSAGES_PAGE_LIMIT = 50
_HEADERS = {
    "accept": "*/*",
    "accept-language": "ru,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://starvell.com",
    "referer": "https://starvell.com/chat",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
}


def _list_result(data) -> Mapping:
    if isinstance(data, Mapping):
        if isinstance(data.get("items"), list):
            return data
        for key in ("messagesListResult", "result", "data"):
            container = data.get(key)
            if isinstance(container, Mapping) and isinstance(container.get("items"), list):
                return container
    return {}


async def fetch_chat_messages_page(
    session_cookie: str,
    chat_id: str,
    limit: int = MESSAGES_PAGE_LIMIT,
    my_games_cookie: str | None = None,
    interlocutor_id: int | None = None,
    before_id: str | None = None,
) -> dict:
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    dto = {"chatId": chat_id, "limit": min(MESSAGES_PAGE_LIMIT, max(1, int(limit)))}
    if before_id:
        dto["beforeId"] = str(before_id)
    if interlocutor_id is not None and not before_id:
        url = "https://starvell.com/api/bff/chat-page"
        payload = {"interlocutorId": int(interlocutor_id), "messagesListDto": dto}
    else:
        url = "https://starvell.com/api/messages/list-v2"
        payload = dto
    data = await request_json("POST", url, headers=_HEADERS, cookies=cookies, timeout=20, json=payload)
    items = response_items(data, "fetch chat messages")
    page = _list_result(data)
    cursor = page.get("nextCursor")
    return {
        "items": items,
        "has_more_before": bool(page.get("hasMoreBefore")) and bool(cursor),
        "next_cursor": str(cursor) if cursor else None,
    }


async def fetch_chat_messages(
    session_cookie: str,
    chat_id: str,
    limit: int = MESSAGES_PAGE_LIMIT,
    my_games_cookie: str | None = None,
    interlocutor_id: int | None = None,
) -> list[dict]:
    page = await fetch_chat_messages_page(
        session_cookie,
        chat_id,
        limit=limit,
        my_games_cookie=my_games_cookie,
        interlocutor_id=interlocutor_id,
    )
    return page["items"]

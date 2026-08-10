from aiohttp import ClientResponseError

from api.http_client import request_json
from api.next_data import get_build_id, reset_build_id
from api.response import normalize_page_collection


async def fetch_chats(session_cookie: str, my_games_cookie: str | None = None) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "referer": "https://starvell.com/chat",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
        "x-nextjs-data": "1",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    last_exc = None
    for attempt in range(2):
        build_id = await get_build_id(session_cookie)
        url = f"https://starvell.com/_next/data/{build_id}/chat.json"
        try:
            data = await request_json(
                "GET", url, headers=headers, cookies=cookies, timeout=20, retry_safe=True
            )
            return normalize_page_collection(data, "chats", "fetch chats")
        except ClientResponseError as exc:
            last_exc = exc
            if exc.status == 404 and attempt == 0:
                reset_build_id()
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("Unable to fetch chat list")







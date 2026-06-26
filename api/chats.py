import aiohttp
from aiohttp import ClientResponseError

from api.cookies import build_cookies, capture_cookies
from api.http_headers import next_data_headers, api_headers
from api.next_data import get_build_id, reset_build_id
from api.rate_limiter import throttle


async def fetch_chats(session_cookie: str, my_games_cookie: str | None = None) -> dict:
    headers = next_data_headers("https://starvell.com/chat")
    cookies = build_cookies(session_cookie, my_games_cookie=my_games_cookie)
    timeout = aiohttp.ClientTimeout(total=20)
    last_exc = None
    for attempt in range(2):
        build_id = await get_build_id(session_cookie)
        url = f"https://starvell.com/_next/data/{build_id}/chat.json"
        async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
            try:
                await throttle()
                async with session.get(url) as resp:
                    capture_cookies(session.cookie_jar)
                    resp.raise_for_status()
                    data = await resp.json()
                    return data
            except ClientResponseError as exc:
                last_exc = exc
                if exc.status == 404 and attempt == 0:
                    reset_build_id()
                    continue
                raise
    if last_exc:
        raise last_exc
    raise RuntimeError("Unable to fetch chat list")


async def mark_chat_read(session_cookie: str, chat_id: str, my_games_cookie: str | None = None) -> bool:
    headers = api_headers(f"https://starvell.com/chat/{chat_id}")
    cookies = build_cookies(session_cookie, my_games_cookie=my_games_cookie)
    payload = {"chatId": chat_id}
    url = "https://starvell.com/api/chats/read"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, json=payload) as resp:
            capture_cookies(session.cookie_jar)
            return 200 <= resp.status < 300


async def send_typing(
    session_cookie: str,
    chat_id: str,
    is_typing: bool = True,
    my_games_cookie: str | None = None,
) -> bool:
    headers = api_headers(f"https://starvell.com/chat/{chat_id}")
    cookies = build_cookies(session_cookie, my_games_cookie=my_games_cookie)
    payload = {"chatId": chat_id, "isTyping": bool(is_typing)}
    url = "https://starvell.com/api/chats/send-typing"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, json=payload) as resp:
            capture_cookies(session.cookie_jar)
            return 200 <= resp.status < 300









import json
import secrets
import string
import aiohttp

from api.cookies import build_cookies, capture_cookies
from api.http_headers import api_headers
from api.rate_limiter import throttle


def _client_socket_id() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(20))


async def send_chat_message(session_cookie: str, chat_id: str, content: str, my_games_cookie: str | None = None) -> dict:
    headers = api_headers(f"https://starvell.com/chat/{chat_id}")
    cookies = build_cookies(session_cookie, my_games_cookie=my_games_cookie)
    payload = {"chatId": chat_id, "content": content, "clientSocketId": _client_socket_id()}
    url = "https://starvell.com/api/messages/send"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, json=payload) as resp:
            capture_cookies(session.cookie_jar)
            response_text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}: {response_text}")
            try:
                return json.loads(response_text)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Invalid response from server") from exc


async def send_chat_image(
    session_cookie: str,
    chat_id: str,
    image_bytes: bytes,
    filename: str = "image.png",
    content_type: str = "image/png",
    content: str | None = None,
    sid_cookie: str | None = None,
    my_games_cookie: str | None = None,
) -> dict:

    headers = api_headers(f"https://starvell.com/chat/{chat_id}", json=False)
    cookies = build_cookies(session_cookie, sid_cookie=sid_cookie, my_games_cookie=my_games_cookie)

    form = aiohttp.FormData()
    form.add_field("image", image_bytes, filename=filename, content_type=content_type)
    if isinstance(content, str) and content.strip():
        form.add_field("content", content.strip())

    url = f"https://starvell.com/api/messages/send-with-image?chatId={chat_id}"
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, data=form) as resp:
            capture_cookies(session.cookie_jar)
            response_text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}: {response_text}")
            try:
                return json.loads(response_text)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Invalid response from server") from exc

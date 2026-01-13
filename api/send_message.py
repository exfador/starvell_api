import json
import aiohttp

from api.rate_limiter import throttle


async def send_chat_message(session_cookie: str, chat_id: str, content: str, my_games_cookie: str | None = None) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://starvell.com",
        "referer": f"https://starvell.com/chat/{chat_id}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    payload = {"chatId": chat_id, "content": content}
    url = "https://starvell.com/api/messages/send"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, json=payload) as resp:
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

    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "origin": "https://starvell.com",
        "referer": f"https://starvell.com/chat/{chat_id}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if sid_cookie:
        cookies["sid"] = sid_cookie
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie

    form = aiohttp.FormData()
    form.add_field("image", image_bytes, filename=filename, content_type=content_type)
    if isinstance(content, str) and content.strip():
        form.add_field("content", content.strip())

    url = f"https://starvell.com/api/messages/send-with-image?chatId={chat_id}"
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, data=form) as resp:
            response_text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}: {response_text}")
            try:
                return json.loads(response_text)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Invalid response from server") from exc

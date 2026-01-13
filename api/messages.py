import aiohttp

from api.rate_limiter import throttle


async def fetch_chat_messages(
    session_cookie: str,
    chat_id: str,
    limit: int = 50,
    my_games_cookie: str | None = None,
) -> list[dict]:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://starvell.com",
        "referer": "https://starvell.com/chat",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    payload = {"chatId": chat_id, "limit": limit}
    timeout = aiohttp.ClientTimeout(total=20)
    url = "https://starvell.com/api/messages/list"
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if isinstance(data, list):
                return data
            return []


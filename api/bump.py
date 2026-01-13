import aiohttp

from api.rate_limiter import throttle


async def bump_categories(
    session_cookie: str,
    sid_cookie: str | None,
    game_id: int,
    category_ids: list[int],
    referer: str | None = None,
    my_games_cookie: str | None = None,
) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://starvell.com",
        "referer": referer or "https://starvell.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    if sid_cookie:
        cookies["sid"] = sid_cookie
    payload = {"gameId": game_id, "categoryIds": category_ids}
    url = "https://starvell.com/api/offers/bump"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, json=payload) as resp:
            txt = await resp.text()
            ct = resp.headers.get("Content-Type", "").lower()
            ok = 200 <= resp.status < 300
            data: dict
            try:
                if "application/json" in ct:
                    parsed = await resp.json()
                    data = {
                        "success": ok,
                        "status": resp.status,
                        "json": parsed,
                    }
                else:
                    data = {}
            except Exception:
                data = {}
            if not data:
                data = {
                    "success": ok,
                    "status": resp.status,
                    "raw": (txt or "")[:2000],
                }
    return {
        "request": {"gameId": game_id, "categoryIds": category_ids},
        "response": data,
    }



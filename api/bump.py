import aiohttp


async def bump_categories(session_cookie: str, sid_cookie: str | None, game_id: int, category_ids: list[int], referer: str | None = None) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://starvell.com",
        "referer": referer or "https://starvell.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {
        "session": session_cookie,
        "starvell.theme": "dark",
        "starvell.time_zone": "Europe/Moscow",
        "starvell.my_games": "10,1,11",
    }
    if sid_cookie:
        cookies["sid"] = sid_cookie
    payload = {"gameId": game_id, "categoryIds": category_ids}
    url = "https://starvell.com/api/offers/bump"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            txt = await resp.text()
            ct = resp.headers.get("Content-Type", "").lower()
            data: dict
            try:
                if "application/json" in ct:
                    data = await resp.json()
                else:
                    data = {}
            except Exception:
                data = {}
            if not data:
                data = {
                    "success": 200 <= resp.status < 300,
                    "status": resp.status,
                    "raw": txt,
                }
    return {
        "request": {"gameId": game_id, "categoryIds": category_ids},
        "response": data,
    }



import aiohttp

from api.cookies import build_cookies, capture_cookies
from api.http_headers import api_headers
from api.rate_limiter import throttle


async def bump_categories(
    session_cookie: str,
    sid_cookie: str | None,
    game_id: int,
    category_ids: list[int],
    referer: str | None = None,
    my_games_cookie: str | None = None,
) -> dict:
    headers = api_headers(referer or "https://starvell.com/")
    cookies = build_cookies(session_cookie, sid_cookie=sid_cookie, my_games_cookie=my_games_cookie)
    payload = {"gameId": game_id, "categoryIds": category_ids}
    url = "https://starvell.com/api/offers/bump"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, json=payload) as resp:
            capture_cookies(session.cookie_jar)
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



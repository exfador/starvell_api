import aiohttp
from aiohttp import ClientResponseError

from api.cookies import build_cookies, capture_cookies
from api.http_headers import next_data_headers
from api.next_data import get_build_id, reset_build_id
from api.rate_limiter import throttle


async def fetch_offer_detail(
    session_cookie: str,
    offer_id: int,
    sid_cookie: str | None = None,
    my_games_cookie: str | None = None,
) -> dict:
    headers = next_data_headers("https://starvell.com/")
    cookies = build_cookies(session_cookie, sid_cookie=sid_cookie, my_games_cookie=my_games_cookie)
    timeout = aiohttp.ClientTimeout(total=20)
    last_exc = None
    for attempt in range(2):
        build_id = await get_build_id(session_cookie)
        url = f"https://starvell.com/_next/data/{build_id}/offers/{offer_id}.json?offer_id={offer_id}"
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
    raise RuntimeError("Unable to fetch offer detail")



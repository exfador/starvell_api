import aiohttp
from aiohttp import ClientResponseError

from api.cookies import build_cookies, capture_cookies
from api.http_headers import next_data_headers
from api.next_data import get_build_id, reset_build_id
from api.rate_limiter import throttle


async def fetch_homepage_data(session_cookie: str, my_games_cookie: str | None = None) -> dict:
    headers = next_data_headers("https://starvell.com/")
    cookies = build_cookies(session_cookie, my_games_cookie=my_games_cookie)
    timeout = aiohttp.ClientTimeout(total=20)
    last_error = None
    data = None
    sid_cookie = None
    for attempt in range(2):
        build_id = await get_build_id(session_cookie)
        url = f"https://starvell.com/_next/data/{build_id}/index.json"
        async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
            try:
                await throttle()
                async with session.get(url) as resp:
                    capture_cookies(session.cookie_jar)
                    resp.raise_for_status()
                    data = await resp.json()
            except ClientResponseError as exc:
                last_error = exc
                if exc.status == 404 and attempt == 0:
                    reset_build_id()
                    continue
                raise
            try:
                jar_cookies = session.cookie_jar.filter_cookies("https://starvell.com")
                c = jar_cookies.get("sid")
                if c is not None:
                    sid_cookie = c.value
            except Exception:
                sid_cookie = None
            break
    if last_error:
        raise last_error
    if data is None:
        raise RuntimeError("Unable to fetch homepage data")
    page_props = data.get("pageProps", {})
    my_games_from_cookie = None
    try:
        jar_cookies = session.cookie_jar.filter_cookies("https://starvell.com")
        c_mg = jar_cookies.get("starvell.my_games")
        if c_mg is not None:
            my_games_from_cookie = c_mg.value
    except Exception:
        my_games_from_cookie = None

    result = {
        "authorized": bool(page_props.get("user")),
        "user": page_props.get("user"),
        "sid": page_props.get("sid") or sid_cookie,
        "my_games": page_props.get("my_games") or my_games_from_cookie or my_games_cookie,
        "currentTheme": page_props.get("currentTheme"),
        "_sentryTraceData": page_props.get("_sentryTraceData"),
        "_sentryBaggage": page_props.get("_sentryBaggage"),
        "__N_SSP": data.get("__N_SSP"),
    }
    return result



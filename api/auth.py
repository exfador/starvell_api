import aiohttp
from aiohttp import ClientResponseError

from api.next_data import get_build_id, reset_build_id
from api.rate_limiter import throttle


async def fetch_homepage_data(session_cookie: str, my_games_cookie: str | None = None) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "referer": "https://starvell.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
        "x-nextjs-data": "1",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
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



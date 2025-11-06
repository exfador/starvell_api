import aiohttp
from aiohttp import ClientResponseError, ContentTypeError

from api.next_data import get_build_id, reset_build_id


async def fetch_sells(session_cookie: str) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "referer": "https://starvell.com/account/orders",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
        "x-nextjs-data": "1",
    }
    cookies = {
        "session": session_cookie,
        "starvell.theme": "dark",
        "starvell.time_zone": "Europe/Moscow",
        "starvell.my_games": "1,10,11",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    last_exc = None
    for attempt in range(2):
        build_id = await get_build_id(session_cookie)
        url = f"https://starvell.com/_next/data/{build_id}/account/sells.json"
        async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
            try:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    return await resp.json()
            except ClientResponseError as exc:
                last_exc = exc
                if exc.status == 404 and attempt == 0:
                    reset_build_id()
                    continue
                raise
    if last_exc:
        raise last_exc
    raise RuntimeError("Unable to fetch sells list")


async def refund_order(session_cookie: str, order_id: str, sid_cookie: str | None = None) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://starvell.com",
        "referer": f"https://starvell.com/order/{order_id}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {
        "session": session_cookie,
        "starvell.theme": "dark",
        "starvell.time_zone": "Europe/Moscow",
        "starvell.my_games": "1,10,11",
    }
    if sid_cookie:
        cookies["sid"] = sid_cookie
    timeout = aiohttp.ClientTimeout(total=20)
    url = "https://starvell.com/api/orders/refund"
    payload = {"orderId": order_id}
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            try:
                ct = resp.headers.get("Content-Type", "")
                if "application/json" in ct.lower():
                    return await resp.json()
                text = await resp.text()
                return {"status": resp.status, "text": text}
            except ContentTypeError:
                try:
                    text = await resp.text()
                except Exception:
                    text = ""
                return {"status": resp.status, "text": text}



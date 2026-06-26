import aiohttp
from aiohttp import ClientResponseError, ContentTypeError

from api.cookies import build_cookies, capture_cookies
from api.http_headers import next_data_headers, api_headers
from api.next_data import get_build_id, reset_build_id
from api.rate_limiter import throttle


async def fetch_sells(session_cookie: str, page: int | None = None, my_games_cookie: str | None = None) -> dict:
    headers = next_data_headers("https://starvell.com/account/orders")
    cookies = build_cookies(session_cookie, my_games_cookie=my_games_cookie)
    timeout = aiohttp.ClientTimeout(total=20)
    last_exc = None
    for attempt in range(2):
        build_id = await get_build_id(session_cookie)
        url = f"https://starvell.com/_next/data/{build_id}/account/sells.json"
        if isinstance(page, int) and page > 1:
            url += f"?page={page}"
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
    raise RuntimeError("Unable to fetch sells list")


async def fetch_created_count(session_cookie: str, my_games_cookie: str | None = None) -> dict:
    headers = api_headers("https://starvell.com/account/orders", json=False, origin=False)
    cookies = build_cookies(session_cookie, my_games_cookie=my_games_cookie)
    timeout = aiohttp.ClientTimeout(total=20)
    url = "https://starvell.com/api/orders/created-count"
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.get(url) as resp:
            capture_cookies(session.cookie_jar)
            resp.raise_for_status()
            data = await resp.json()
            return data if isinstance(data, dict) else {}


async def fetch_orders_list(
    session_cookie: str,
    status: str | None = "CREATED",
    user_type: str = "seller",
    limit: int = 20,
    offset: int = 0,
    my_games_cookie: str | None = None,
) -> list[dict]:
    headers = api_headers("https://starvell.com/account/orders")
    cookies = build_cookies(session_cookie, my_games_cookie=my_games_cookie)
    timeout = aiohttp.ClientTimeout(total=20)
    url = "https://starvell.com/api/orders/list"
    with_party = "buyer" if user_type == "seller" else "seller"
    order_filter: dict = {"userType": user_type}
    if status:
        order_filter["status"] = status
    payload = {
        "filter": order_filter,
        "with": {with_party: True},
        "limit": int(limit),
        "offset": int(offset),
    }
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, json=payload) as resp:
            capture_cookies(session.cookie_jar)
            resp.raise_for_status()
            data = await resp.json()
            return data if isinstance(data, list) else []


async def fetch_order_detail(
    session_cookie: str,
    order_id: str,
    my_games_cookie: str | None = None,
) -> dict:
    headers = next_data_headers(f"https://starvell.com/order/{order_id}")
    cookies = build_cookies(session_cookie, my_games_cookie=my_games_cookie)
    timeout = aiohttp.ClientTimeout(total=20)
    last_exc = None
    for attempt in range(2):
        build_id = await get_build_id(session_cookie)
        url = f"https://starvell.com/_next/data/{build_id}/order/{order_id}.json?order_id={order_id}"
        async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
            try:
                await throttle()
                async with session.get(url) as resp:
                    capture_cookies(session.cookie_jar)
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
    raise RuntimeError("Unable to fetch order detail")


async def fetch_sells_all(session_cookie: str, max_pages: int = 200) -> list[dict]:
    items: list[dict] = []
    seen_ids: set[str] = set()
    limit = 50
    offset = 0
    for _ in range(max_pages):
        try:
            batch = await fetch_orders_list(
                session_cookie, status=None, user_type="seller", limit=limit, offset=offset
            )
        except Exception:
            break
        if not batch:
            break
        new = 0
        for o in batch:
            oid = str((o or {}).get("id") or "")
            if oid and oid in seen_ids:
                continue
            if oid:
                seen_ids.add(oid)
            items.append(o)
            new += 1
        if len(batch) < limit or new == 0:
            break
        offset += limit
    return items


async def refund_order(
    session_cookie: str,
    order_id: str,
    sid_cookie: str | None = None,
    my_games_cookie: str | None = None,
) -> dict:
    headers = api_headers(f"https://starvell.com/order/{order_id}")
    cookies = build_cookies(session_cookie, sid_cookie=sid_cookie, my_games_cookie=my_games_cookie)
    timeout = aiohttp.ClientTimeout(total=20)
    url = "https://starvell.com/api/orders/refund"
    payload = {"orderId": order_id}
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.post(url, json=payload) as resp:
            capture_cookies(session.cookie_jar)
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



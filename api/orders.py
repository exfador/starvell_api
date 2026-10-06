from aiohttp import ClientResponseError

from api.http_client import request_json, request_text
from api.next_data import get_build_id, reset_build_id
from api.response import StarvellResponseError, ensure_success, normalize_page_collection


async def fetch_sells(session_cookie: str, page: int | None = None, my_games_cookie: str | None = None) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "referer": "https://starvell.com/account/sells",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
        "x-nextjs-data": "1",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    last_exc = None
    for attempt in range(2):
        build_id = await get_build_id(session_cookie)
        url = f"https://starvell.com/_next/data/{build_id}/account/sells.json"
        if isinstance(page, int) and page > 1:
            url += f"?page={page}"
        try:
            data = await request_json(
                "GET", url, headers=headers, cookies=cookies, timeout=20, retry_safe=True
            )
            return normalize_page_collection(data, "orders", "fetch sells")
        except ClientResponseError as exc:
            last_exc = exc
            if exc.status == 404 and attempt == 0:
                reset_build_id()
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("Unable to fetch sells list")


async def fetch_sells_all(
    session_cookie: str,
    max_pages: int = 200,
    my_games_cookie: str | None = None,
) -> list[dict]:
    items: list[dict] = []
    page = 1
    seen_ids: set[str] = set()
    while page <= max_pages:
        data = await fetch_sells(
            session_cookie,
            page=page if page > 1 else None,
            my_games_cookie=my_games_cookie,
        )
        page_props = (data or {}).get("pageProps", {})
        orders = page_props.get("orders") or []
        if not orders:
            break
        added = False
        for o in orders:
            try:
                oid = str((o or {}).get("id") or "")
                if oid and oid in seen_ids:
                    continue
                if oid:
                    seen_ids.add(oid)
                items.append(o)
                added = True
            except Exception:
                items.append(o)
                added = True
        if not added:
            break
        page += 1
    return items


SELLER_ORDERS_PAGE_LIMIT = 20


def _order_list(data) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        ensure_success(data, "list seller orders")
        for key in ("data", "items", "orders"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict) and isinstance(value.get("items"), list):
                return [item for item in value["items"] if isinstance(item, dict)]
    raise StarvellResponseError("list seller orders: response has no order list")


async def fetch_seller_orders(
    session_cookie: str,
    offset: int = 0,
    limit: int = SELLER_ORDERS_PAGE_LIMIT,
    status: str | None = None,
    my_games_cookie: str | None = None,
) -> list[dict]:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://starvell.com",
        "referer": "https://starvell.com/account/sells",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    order_filter: dict = {"userType": "seller"}
    if status:
        order_filter["status"] = status
    payload = {
        "filter": order_filter,
        "limit": max(1, int(limit)),
        "offset": max(0, int(offset)),
        "with": {"buyer": True},
    }
    data = await request_json(
        "POST",
        "https://starvell.com/api/orders/list",
        headers=headers,
        cookies=cookies,
        timeout=20,
        json=payload,
        retry_safe=True,
    )
    return _order_list(data)


async def fetch_seller_orders_all(
    session_cookie: str,
    max_pages: int = 200,
    my_games_cookie: str | None = None,
) -> list[dict]:
    items: list[dict] = []
    seen_ids: set[str] = set()
    for page in range(max(1, int(max_pages))):
        orders = await fetch_seller_orders(
            session_cookie,
            offset=page * SELLER_ORDERS_PAGE_LIMIT,
            my_games_cookie=my_games_cookie,
        )
        fresh = [order for order in orders if str(order.get("id") or "") not in seen_ids]
        seen_ids.update(str(order.get("id") or "") for order in fresh)
        items.extend(fresh)
        if len(orders) < SELLER_ORDERS_PAGE_LIMIT or not fresh:
            break
    return items


async def refund_order(
    session_cookie: str,
    order_id: str,
    sid_cookie: str | None = None,
    my_games_cookie: str | None = None,
) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://starvell.com",
        "referer": f"https://starvell.com/order/{order_id}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    if sid_cookie:
        cookies["sid"] = sid_cookie
    url = "https://starvell.com/api/orders/refund"
    payload = {"orderId": order_id}
    response = await request_text(
        "POST", url, headers=headers, cookies=cookies, timeout=20, json=payload
    )
    if "application/json" not in response.headers.get("Content-Type", "").lower():
        raise RuntimeError("refund order returned a non-JSON response")
    return ensure_success(response.json(), "refund order")

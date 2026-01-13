import aiohttp
from aiohttp import ClientResponseError

from api.next_data import get_build_id, reset_build_id
from api.rate_limiter import throttle


def _maybe_int(v):
    try:
        return int(v)
    except Exception:
        return None


async def find_user_lots(
    session_cookie: str,
    sid_cookie: str,
    user_id: int,
    my_games_cookie: str | None = None,
) -> dict:

    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "referer": f"https://starvell.com/users/{user_id}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
        "x-nextjs-data": "1",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}

    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    if sid_cookie:
        cookies["sid"] = sid_cookie

    timeout = aiohttp.ClientTimeout(total=20)
    last_exc = None
    data = None
    for attempt in range(2):
        build_id = await get_build_id(session_cookie)
        url = f"https://starvell.com/_next/data/{build_id}/users/{user_id}.json?user_id={user_id}"
        async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
            try:
                await throttle()
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    break
            except ClientResponseError as exc:
                last_exc = exc
                if exc.status == 404 and attempt == 0:
                    reset_build_id()
                    continue
                raise

    if data is None and last_exc:
        raise last_exc
    if data is None:
        return {"lots": [], "my_games": my_games_cookie}

    page_props = (data or {}).get("pageProps", {})

    user_profile_offers = page_props.get("userProfileOffers")
    if not user_profile_offers:
        user_profile_offers = (page_props.get("bff") or {}).get("userProfileOffers")

    lots: list[dict] = []

    categories = user_profile_offers or page_props.get("categoriesWithOffers") or []
    if not isinstance(categories, list):
        return {"lots": lots, "my_games": my_games_cookie}

    seen_game_ids: set[int] = set()
    for category in categories:
        if not isinstance(category, dict):
            continue
        category_id = _maybe_int(category.get("id"))
        category_slug = str(category.get("slug") or "").strip() or None
        game_id = _maybe_int(category.get("gameId") or (category.get("game") or {}).get("id"))
        if isinstance(game_id, int):
            seen_game_ids.add(game_id)
        game_slug = str((category.get("game") or {}).get("slug") or "").strip() or None
        category_url = None
        if game_slug and category_slug:
            category_url = f"https://starvell.com/{game_slug}/{category_slug}/trade"

        offers = category.get("offers") or []
        if not isinstance(offers, list):
            continue
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            offer_id = _maybe_int(offer.get("id"))
            price = offer.get("price")
            availability = offer.get("availability")
            brief = (
                (offer.get("descriptions") or {}).get("rus", {}).get("briefDescription")
                or (offer.get("descriptions") or {}).get("rus", {}).get("description")
            )
            title = str(brief).strip() if brief else None
            lots.append(
                {
                    "id": offer_id,
                    "title": title,
                    "availability": availability,
                    "price": price,
                    "url": f"https://starvell.com/offers/{offer_id}" if offer_id else None,
                    "category_id": category_id,
                    "game_id": game_id,
                    "category_url": category_url,
                }
            )

    derived_my_games = None
    if seen_game_ids:
        derived_my_games = ",".join(str(x) for x in sorted(seen_game_ids))
    return {"lots": lots, "my_games": derived_my_games or my_games_cookie}



import logging
from urllib.parse import quote

from aiohttp import ClientResponseError

from api.auth_constants import AUTH_ATTEMPTS, AUTH_HEADERS, AUTH_TIMEOUT
from api.auth_cookies import authentication_cookies
from api.http_client import request_json
from api.next_data import get_build_id, reset_build_id
from api.response import StarvellResponseError, page_props as get_page_props


def _maybe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _identifier(value):
    numeric = _maybe_int(value)
    if numeric is not None:
        return numeric
    return str(value or "").strip() or None


def _profile_categories(props: dict) -> tuple[list, bool]:
    containers = [props]
    if isinstance(props.get("bff"), dict):
        containers.append(props["bff"])
    for container in containers:
        catalog = container.get("catalogUserProfileOffersResult")
        if isinstance(catalog, dict) and "categories" in catalog:
            if not isinstance(catalog["categories"], list):
                raise StarvellResponseError("find user lots: invalid category list")
            return catalog["categories"], bool(catalog.get("hasMore"))
        for key in ("userProfileOffers", "categoriesWithOffers"):
            if key not in container:
                continue
            value = container[key]
            if not isinstance(value, list):
                raise StarvellResponseError("find user lots: invalid category list")
            return value, False
    raise StarvellResponseError(f"find user lots: response has no category list (keys: {', '.join(sorted(props)[:20])})")


async def find_user_lots(
    session_cookie: str,
    sid_cookie: str,
    user_id: int,
    username: str | None = None,
    my_games_cookie: str | None = None,
) -> dict:
    profile_name = str(username or user_id).strip().lower()
    if not profile_name:
        raise ValueError("find user lots: username is missing")
    cookies = authentication_cookies(session_cookie, my_games_cookie)
    if sid_cookie:
        cookies["sid"] = sid_cookie
    data = await _fetch_profile(cookies, profile_name)
    categories, has_more = _profile_categories(get_page_props(data, "find user lots"))
    if has_more:
        logging.getLogger("exfador.lots").warning(
            "profile_offers_partial username=%s categories=%s: Starvell returned only the first part of the lot list",
            profile_name,
            len(categories),
        )
    lots, game_ids = _collect_lots(categories)
    derived_games = ",".join(str(game_id) for game_id in sorted(game_ids))
    return {"lots": lots, "my_games": derived_games or my_games_cookie, "partial": has_more}


async def _fetch_profile(cookies: dict, name: str) -> dict:
    encoded_name = quote(name, safe="")
    headers = {**AUTH_HEADERS, "referer": f"https://starvell.com/profile/{encoded_name}"}
    for attempt in range(AUTH_ATTEMPTS):
        build_id = await get_build_id(cookies.get("session", ""))
        try:
            return await request_json(
                "GET",
                f"https://starvell.com/_next/data/{build_id}/profile/{encoded_name}.json",
                headers=headers,
                cookies=cookies,
                timeout=AUTH_TIMEOUT,
                retry_safe=True,
                allow_redirects=False,
                params={"username": name},
            )
        except ClientResponseError as error:
            if error.status == 404 and attempt + 1 < AUTH_ATTEMPTS:
                reset_build_id()
                continue
            raise
    raise StarvellResponseError("Unable to fetch user lots")


def _collect_lots(categories: list) -> tuple[list[dict], set[int]]:
    lots, game_ids = [], set()
    for category in categories:
        if not isinstance(category, dict):
            raise StarvellResponseError("find user lots: invalid category")
        category_lots, game_id = _category_lots(category)
        lots.extend(category_lots)
        if game_id is not None:
            game_ids.add(game_id)
    return lots, game_ids


def _category_lots(category: dict) -> tuple[list[dict], int | None]:
    game = category.get("game") or {}
    game_id = _maybe_int(category.get("gameId") or game.get("id"))
    game_slug = str(game.get("slug") or "").strip()
    category_slug = str(category.get("slug") or "").strip()
    context = {
        "category_id": _maybe_int(category.get("id")),
        "game_id": game_id,
        "category_url": f"https://starvell.com/{game_slug}/{category_slug}" if game_slug and category_slug else None,
        "offer_type": category.get("offerType"),
    }
    offers = category.get("offers") or []
    if not isinstance(offers, list):
        raise StarvellResponseError("find user lots: invalid offers")
    return [_offer_lot(offer, context) for offer in offers], game_id


def _offer_lot(offer: dict, context: dict) -> dict:
    if not isinstance(offer, dict):
        raise StarvellResponseError("find user lots: invalid offer")
    offer_id = _identifier(offer.get("publicId") or offer.get("id"))
    description = (offer.get("descriptions") or {}).get("rus") or {}
    title = description.get("briefDescription") or description.get("description")
    return {
        **context,
        "id": offer_id,
        "title": str(title).strip() if title else None,
        "availability": offer.get("availability"),
        "price": offer.get("price"),
        "url": f"https://starvell.com/offers/{offer_id}" if offer_id else None,
    }

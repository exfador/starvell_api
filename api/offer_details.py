from aiohttp import ClientResponseError
from typing import Any
from urllib.parse import quote

from api.http_client import request_json
from api.next_data import get_build_id, reset_build_id
from api.response import StarvellResponseError, normalize_page_object, page_props


def offer_context(data: Any) -> tuple[dict, dict, dict]:
    props = page_props(data, "read offer detail")
    offer = props.get("offer")
    if not isinstance(offer, dict):
        raise StarvellResponseError("read offer detail: response has no offer object")
    game = offer.get("game")
    category = offer.get("category")
    return offer, game if isinstance(game, dict) else {}, category if isinstance(category, dict) else {}


async def fetch_offer_detail(
    session_cookie: str,
    offer_id: int | str,
    sid_cookie: str | None = None,
    my_games_cookie: str | None = None,
) -> dict:
    offer_id_text = str(offer_id or "").strip()
    if not offer_id_text:
        raise ValueError("fetch offer detail: offer id is missing")
    encoded_offer_id = quote(offer_id_text, safe="")
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "referer": f"https://starvell.com/offers/{encoded_offer_id}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
        "x-nextjs-data": "1",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    if sid_cookie:
        cookies["sid"] = sid_cookie
    last_exc = None
    for attempt in range(2):
        build_id = await get_build_id(session_cookie)
        url = f"https://starvell.com/_next/data/{build_id}/offers/{encoded_offer_id}.json"
        try:
            data = await request_json(
                "GET",
                url,
                headers=headers,
                cookies=cookies,
                timeout=20,
                retry_safe=True,
                params={"offer_id": offer_id_text},
            )
            return normalize_page_object(data, "offer", "fetch offer detail")
        except ClientResponseError as exc:
            last_exc = exc
            if exc.status == 404 and attempt == 0:
                reset_build_id()
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("Unable to fetch offer detail")

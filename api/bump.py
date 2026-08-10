import json

from api.http_client import request_text
from api.response import StarvellResponseError, ensure_success


def normalize_bump_response(status: int, content_type: str, text: str) -> dict:
    http_success = 200 <= status < 300
    if "application/json" not in content_type.lower():
        return {
            "success": http_success,
            "status": status,
            "raw": (text or "")[:2000],
        }
    try:
        parsed = json.loads(text)
        ensure_success(parsed, "bump offers")
    except (json.JSONDecodeError, StarvellResponseError) as exc:
        return {
            "success": False,
            "status": status,
            "raw": (text or "")[:2000],
            "error": str(exc),
        }
    return {
        "success": http_success,
        "status": status,
        "json": parsed,
    }


async def bump_categories(
    session_cookie: str,
    sid_cookie: str | None,
    game_id: int,
    category_ids: list[int],
    referer: str | None = None,
    my_games_cookie: str | None = None,
) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://starvell.com",
        "referer": referer or "https://starvell.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 YaBrowser/25.8.0.0 Safari/537.36",
    }
    cookies = {"session": session_cookie, "starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    if sid_cookie:
        cookies["sid"] = sid_cookie
    payload = {"gameId": game_id, "categoryIds": category_ids}
    url = "https://starvell.com/api/offers/bump"
    response = await request_text(
        "POST",
        url,
        headers=headers,
        cookies=cookies,
        timeout=20,
        json=payload,
        raise_for_status=False,
    )
    data = normalize_bump_response(
        response.status,
        response.headers.get("Content-Type", "").lower(),
        response.text,
    )
    return {
        "request": {"gameId": game_id, "categoryIds": category_ids},
        "response": data,
    }

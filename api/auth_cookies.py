from http.cookies import CookieError, SimpleCookie

from api.auth_constants import AUTH_COOKIE_NAMES, AUTH_MAX_COOKIE_LENGTH


def authentication_cookies(raw_session: str, my_games_cookie: str | None = None) -> dict[str, str]:
    raw = (raw_session or "").strip()
    if len(raw) > AUTH_MAX_COOKIE_LENGTH or any(ord(character) < 32 for character in raw):
        raise ValueError("Invalid session cookie")
    cookies = {"starvell.theme": "dark", "starvell.time_zone": "Europe/Moscow"}
    if is_cookie_header(raw):
        cookies.update(parse_authentication_header(raw))
    elif raw:
        cookies["session"] = raw
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    return cookies


def parse_authentication_header(raw: str) -> dict[str, str]:
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except CookieError as error:
        raise ValueError("Invalid cookie header") from error
    cookies = {key: item.value for key, item in jar.items() if key in AUTH_COOKIE_NAMES}
    if not cookies.get("session"):
        raise ValueError("Cookie header must contain session")
    return cookies


def normalize_request_cookies(cookies: dict[str, str] | None) -> dict[str, str] | None:
    if not cookies or not cookies.get("session"):
        return cookies
    raw = cookies["session"].strip()
    if len(raw) > AUTH_MAX_COOKIE_LENGTH or any(ord(character) < 32 for character in raw):
        raise ValueError("Invalid session cookie")
    if not is_cookie_header(raw):
        return {**cookies, "session": raw}
    return {**cookies, **parse_authentication_header(raw)}


def is_cookie_header(raw: str) -> bool:
    return ";" in raw or ("=" in raw and raw.split("=", 1)[0] in AUTH_COOKIE_NAMES)

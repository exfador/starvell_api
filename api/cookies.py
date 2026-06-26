_DEFAULT_COOKIES = {
    "starvell.theme": "dark",
    "starvell.time_zone": "Europe/Moscow",
}

_dynamic_cookies: dict[str, str] = {}


def parse_cookie_string(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in (raw or "").split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def build_cookies(
    raw_session: str | None,
    sid_cookie: str | None = None,
    my_games_cookie: str | None = None,
) -> dict[str, str]:
    cookies = dict(_DEFAULT_COOKIES)
    raw = (raw_session or "").strip()
    if "=" in raw:
        cookies.update(parse_cookie_string(raw))
    elif raw:
        cookies["session"] = raw
    cookies.update(_dynamic_cookies)
    if sid_cookie:
        cookies["sid"] = sid_cookie
    if my_games_cookie:
        cookies["starvell.my_games"] = my_games_cookie
    return {k: v for k, v in cookies.items() if v not in (None, "")}


def capture_cookies(jar) -> None:
    try:
        for morsel in jar:
            name = getattr(morsel, "key", None)
            value = getattr(morsel, "value", None)
            if not name or value in (None, ""):
                continue
            if name.startswith("__ddg"):
                _dynamic_cookies[name] = value
    except Exception:
        pass


def get_dynamic_cookies() -> dict[str, str]:
    return dict(_dynamic_cookies)


def reset_dynamic_cookies() -> None:
    _dynamic_cookies.clear()

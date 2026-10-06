import logging

from aiohttp import ClientResponseError

from api.auth_constants import AUTH_ATTEMPTS, AUTH_DATA_URL, AUTH_HEADERS, AUTH_PROFILE_URL, AUTH_TIMEOUT
from api.auth_cookies import authentication_cookies
from api.http_client import request_text, request_json
from api.next_data import get_build_id, reset_build_id
from api.response import page_props as get_page_props
from api.response import ensure_success, StarvellResponseError


async def fetch_homepage_data(session_cookie: str, my_games_cookie: str | None = None) -> dict:
    cookies = authentication_cookies(session_cookie, my_games_cookie)
    response = await fetch_homepage_response(cookies)
    data = response.json()
    page_props = get_page_props(data, "fetch homepage")
    user = page_props.get("user")
    source = "homepage"
    if not isinstance(user, dict) or not user.get("id"):
        source = "profile"
        user = await fetch_current_user(cookies)
    logging.getLogger("exfador.auth").info("auth_check source=%s authorized=%s", source, bool(user))
    return {
        "authorized": bool(user),
        "user": user,
        "sid": page_props.get("sid") or response.cookies.get("sid"),
        "my_games": page_props.get("my_games") or response.cookies.get("starvell.my_games") or my_games_cookie,
        "currentTheme": page_props.get("currentTheme"),
        "_sentryTraceData": page_props.get("_sentryTraceData"),
        "_sentryBaggage": page_props.get("_sentryBaggage"),
        "__N_SSP": data.get("__N_SSP"),
    }


async def fetch_homepage_response(cookies: dict):
    for attempt in range(AUTH_ATTEMPTS):
        build_id = await get_build_id(cookies.get("session", ""))
        try:
            return await request_text(
                "GET",
                AUTH_DATA_URL.format(build_id=build_id),
                headers=AUTH_HEADERS,
                cookies=cookies,
                timeout=AUTH_TIMEOUT,
                retry_safe=True,
                allow_redirects=False,
            )
        except ClientResponseError as error:
            if error.status == 404 and attempt + 1 < AUTH_ATTEMPTS:
                reset_build_id()
                continue
            logging.getLogger("exfador.auth").warning("auth_homepage_http status=%s", error.status)
            raise
    raise StarvellResponseError("Unable to fetch homepage data")


async def fetch_current_user(cookies: dict) -> dict | None:
    try:
        result = await request_json(
            "GET",
            AUTH_PROFILE_URL,
            cookies=cookies,
            headers={"accept": "application/json"},
            retry_safe=True,
            allow_redirects=False,
        )
    except ClientResponseError as error:
        logging.getLogger("exfador.auth").warning("auth_profile_http status=%s", error.status)
        if error.status == 401:
            return None
        raise
    result = ensure_success(result, "fetch current profile")
    user = result.get("user", result)
    if not isinstance(user, dict) or not user.get("id"):
        logging.getLogger("exfador.auth").warning("auth_profile_invalid_shape")
        raise StarvellResponseError("Current profile response has no user identity")
    return user

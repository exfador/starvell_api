USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
SEC_CH_UA = '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"'
ACCEPT_LANGUAGE = "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
ORIGIN = "https://starvell.com"

_CLIENT_HINTS = {
    "sec-ch-ua": SEC_CH_UA,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def next_data_headers(referer: str = "https://starvell.com/") -> dict:
    return {
        "accept": "*/*",
        "accept-language": ACCEPT_LANGUAGE,
        "priority": "u=1, i",
        "referer": referer,
        **_CLIENT_HINTS,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": USER_AGENT,
        "x-nextjs-data": "1",
    }


def api_headers(referer: str = "https://starvell.com/", json: bool = True, origin: bool = True) -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": ACCEPT_LANGUAGE,
        "priority": "u=1, i",
        "referer": referer,
        **_CLIENT_HINTS,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": USER_AGENT,
    }
    if origin:
        headers["origin"] = ORIGIN
    if json:
        headers["content-type"] = "application/json"
    return headers


def page_headers(referer: str | None = None) -> dict:
    headers = {
        "accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "accept-language": ACCEPT_LANGUAGE,
        "priority": "u=0, i",
        **_CLIENT_HINTS,
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin" if referer else "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": USER_AGENT,
    }
    if referer:
        headers["referer"] = referer
    return headers

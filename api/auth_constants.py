AUTH_COOKIE_NAMES = frozenset({"session", "sid", "starvell.my_games", "starvell.theme", "starvell.time_zone"})
AUTH_MAX_COOKIE_LENGTH = 8192
AUTH_ATTEMPTS = 2
AUTH_TIMEOUT = 20
AUTH_PROFILE_URL = "https://starvell.com/api/profiles/me"
AUTH_DATA_URL = "https://starvell.com/_next/data/{build_id}/index.json"
AUTH_HEADERS = {
    "accept": "*/*",
    "accept-language": "ru,en;q=0.9",
    "referer": "https://starvell.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "x-nextjs-data": "1",
}

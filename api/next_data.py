import asyncio
import json
import re
import time
from typing import Optional

import aiohttp

from api.rate_limiter import throttle


_cached_build_id: Optional[str] = None
_cached_at: float = 0.0
_lock = asyncio.Lock()
_TTL_SECONDS = 1800


def reset_build_id() -> None:
    global _cached_build_id, _cached_at
    _cached_build_id = None
    _cached_at = 0.0


async def get_build_id(session_cookie: str) -> str:
    global _cached_build_id, _cached_at
    async with _lock:
        if _cached_build_id and (time.time() - _cached_at) < _TTL_SECONDS:
            return _cached_build_id
        build_id = await _fetch_build_id(session_cookie)
        _cached_build_id = build_id
        _cached_at = time.time()
        return build_id


async def _fetch_build_id(session_cookie: str) -> str:
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "ru,en;q=0.9",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    }
    cookies = {
        "session": session_cookie,
        "starvell.theme": "dark",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout) as session:
        await throttle()
        async with session.get("https://starvell.com/") as resp:
            resp.raise_for_status()
            html = await resp.text()
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not match:
        raise RuntimeError("Unable to locate __NEXT_DATA__ script")
    data = json.loads(match.group(1))
    build_id = data.get("buildId")
    if not build_id:
        raise RuntimeError("buildId not found in __NEXT_DATA__")
    return str(build_id)


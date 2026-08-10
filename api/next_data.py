import asyncio
import json
import time
from html.parser import HTMLParser
from typing import Optional

from api.http_client import request_text


_cached_build_id: Optional[str] = None
_cached_at: float = 0.0
_lock = asyncio.Lock()
_TTL_SECONDS = 1800


def reset_build_id() -> None:
    global _cached_build_id, _cached_at
    _cached_build_id = None
    _cached_at = 0.0


class _NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._inside_next_data = False
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self._inside_next_data = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._inside_next_data:
            self._inside_next_data = False

    def handle_data(self, data: str) -> None:
        if self._inside_next_data:
            self._chunks.append(data)

    @property
    def payload(self) -> str:
        return "".join(self._chunks).strip()


def extract_build_id(html: str) -> str:
    parser = _NextDataParser()
    parser.feed(html)
    if not parser.payload:
        raise RuntimeError("Unable to locate __NEXT_DATA__ script")
    try:
        data = json.loads(parser.payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Invalid __NEXT_DATA__ payload") from exc
    build_id = data.get("buildId") if isinstance(data, dict) else None
    if not build_id:
        raise RuntimeError("buildId not found in __NEXT_DATA__")
    return str(build_id)


async def get_build_id(session_cookie: str) -> str:
    global _cached_build_id, _cached_at
    async with _lock:
        if _cached_build_id and (time.monotonic() - _cached_at) < _TTL_SECONDS:
            return _cached_build_id
        build_id = await _fetch_build_id(session_cookie)
        _cached_build_id = build_id
        _cached_at = time.monotonic()
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
    response = await request_text(
        "GET",
        "https://starvell.com/",
        headers=headers,
        cookies=cookies,
        timeout=20,
        retry_safe=True,
    )
    return extract_build_id(response.text)

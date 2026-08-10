from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from api.rate_limiter import throttle


RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    text: str
    cookies: dict[str, str]

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Invalid JSON response from Starvell") from exc


_session: aiohttp.ClientSession | None = None
_session_loop: asyncio.AbstractEventLoop | None = None
_session_lock = asyncio.Lock()


async def get_http_session() -> aiohttp.ClientSession:
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    async with _session_lock:
        if _session is not None and (_session.closed or _session_loop is not loop):
            await _session.close()
            _session = None
        if _session is None:
            connector = aiohttp.TCPConnector(limit=20, limit_per_host=12, ttl_dns_cache=300)
            _session = aiohttp.ClientSession(
                connector=connector,
                cookie_jar=aiohttp.DummyCookieJar(),
                timeout=aiohttp.ClientTimeout(total=60, connect=10),
            )
            _session_loop = loop
        return _session


async def close_http_session() -> None:
    global _session, _session_loop
    async with _session_lock:
        if _session is not None and not _session.closed:
            await _session.close()
        _session = None
        _session_loop = None


def _retry_delay(headers: dict[str, str], attempt: int) -> float:
    raw = next(
        (str(value).strip() for key, value in headers.items() if key.lower() == "retry-after"),
        "",
    )
    if raw:
        try:
            return min(30.0, max(0.0, float(raw)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw).timestamp()
                return min(30.0, max(0.0, retry_at - __import__("time").time()))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(8.0, 0.5 * (2**attempt))


def _error_detail(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            raw = str(parsed.get("message") or parsed.get("error") or parsed.get("detail") or "")
    except json.JSONDecodeError:
        pass
    return " ".join(raw.split())[:200]


async def request_text(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    timeout: float = 20,
    retry_safe: bool = False,
    raise_for_status: bool = True,
    **kwargs: Any,
) -> HttpResponse:
    attempts = 3 if retry_safe else 1
    last_error: BaseException | None = None
    for attempt in range(attempts):
        await throttle()
        try:
            session = await get_http_session()
            request_timeout = aiohttp.ClientTimeout(total=timeout, connect=min(10, timeout))
            async with session.request(
                method,
                url,
                headers=headers,
                cookies=cookies,
                timeout=request_timeout,
                **kwargs,
            ) as response:
                text = await response.text()
                response_headers = dict(response.headers)
                if retry_safe and response.status in RETRYABLE_STATUSES and attempt + 1 < attempts:
                    await asyncio.sleep(_retry_delay(response_headers, attempt))
                    continue
                if raise_for_status:
                    try:
                        response.raise_for_status()
                    except aiohttp.ClientResponseError as exc:
                        detail = _error_detail(text)
                        if detail:
                            exc.message = f"{exc.message}; Starvell: {detail}"
                        raise
                return HttpResponse(
                    status=response.status,
                    headers=response_headers,
                    text=text,
                    cookies={name: morsel.value for name, morsel in response.cookies.items()},
                )
        except (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as exc:
            last_error = exc
            if not retry_safe or attempt + 1 >= attempts:
                raise
            await asyncio.sleep(_retry_delay({}, attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Starvell request did not produce a response")


async def request_json(method: str, url: str, **kwargs: Any) -> Any:
    return (await request_text(method, url, **kwargs)).json()

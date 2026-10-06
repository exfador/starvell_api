from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import aiohttp

from api.auth_cookies import normalize_request_cookies
from api.rate_limiter import throttle
from api.transport_constants import (
    CONNECT_TIMEOUT,
    DNS_CACHE_SECONDS,
    HOST_POOL_LIMIT,
    HTTPS_PORT,
    INITIAL_RETRY_DELAY,
    MAX_BACKOFF_DELAY,
    MAX_RESPONSE_BYTES,
    MAX_RETRY_DELAY,
    POOL_LIMIT,
    RESPONSE_CHUNK_SIZE,
    RETRY_ATTEMPTS,
    RETRYABLE_STATUSES,
    SESSION_TIMEOUT,
    STARVELL_HOST,
)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    text: str
    cookies: dict[str, str]

    def json(self) -> object:
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
            connector = aiohttp.TCPConnector(
                limit=POOL_LIMIT, limit_per_host=HOST_POOL_LIMIT, ttl_dns_cache=DNS_CACHE_SECONDS
            )
            _session = aiohttp.ClientSession(
                connector=connector,
                cookie_jar=aiohttp.DummyCookieJar(),
                timeout=aiohttp.ClientTimeout(total=SESSION_TIMEOUT, connect=CONNECT_TIMEOUT),
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
    raw = next((str(value).strip() for key, value in headers.items() if key.lower() == "retry-after"), "")
    if not raw:
        return min(MAX_BACKOFF_DELAY, INITIAL_RETRY_DELAY * (2**attempt))
    try:
        return min(MAX_RETRY_DELAY, max(0.0, float(raw)))
    except ValueError:
        return _date_retry_delay(raw, attempt)


def _date_retry_delay(raw: str, attempt: int) -> float:
    try:
        return min(MAX_RETRY_DELAY, max(0.0, parsedate_to_datetime(raw).timestamp() - time.time()))
    except (TypeError, ValueError, OverflowError):
        return min(MAX_BACKOFF_DELAY, INITIAL_RETRY_DELAY * (2**attempt))


def _error_detail(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw.startswith("<"):
        return "HTML page instead of an API response (anti-bot protection or maintenance)"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return " ".join(raw.split())[:200]
    if not isinstance(parsed, dict):
        return " ".join(raw.split())[:200]
    message = parsed.get("message") or parsed.get("error") or parsed.get("detail") or ""
    if isinstance(message, list):
        message = "; ".join(str(item) for item in message)
    data = parsed.get("data")
    code = data.get("code") if isinstance(data, dict) else parsed.get("code")
    detail = " ".join(str(message).split())
    if code:
        detail = f"{detail} [{code}]" if detail else str(code)
    return detail[:200]


def _validate_target(url: str, cookies: dict | None, kwargs: dict) -> None:
    target = urlsplit(url)
    trusted = target.scheme == "https" and target.hostname == STARVELL_HOST and target.port in (None, HTTPS_PORT)
    if cookies and not trusted:
        raise ValueError("Session cookies may only be sent to https://starvell.com")
    if kwargs.pop("allow_redirects", False):
        raise ValueError("Authenticated redirects are not supported")


async def request_text(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    timeout: float = 20,
    retry_safe: bool = False,
    raise_for_status: bool = True,
    **kwargs: object,
) -> HttpResponse:
    _validate_target(url, cookies, kwargs)
    options = {
        **kwargs,
        "headers": headers,
        "cookies": normalize_request_cookies(cookies),
        "timeout": aiohttp.ClientTimeout(total=timeout, connect=min(CONNECT_TIMEOUT, timeout)),
        "allow_redirects": False,
    }
    attempts = RETRY_ATTEMPTS if retry_safe else 1
    return await _request_with_retries(method, url, options, attempts, raise_for_status)


async def _request_with_retries(
    method: str, url: str, options: dict, attempts: int, should_raise: bool
) -> HttpResponse:
    for attempt in range(attempts):
        can_retry = attempt + 1 < attempts
        try:
            response = await _request_once(method, url, options, should_raise, can_retry)
        except (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError, asyncio.TimeoutError):
            if not can_retry:
                raise
            await asyncio.sleep(_retry_delay({}, attempt))
            continue
        if can_retry and response.status in RETRYABLE_STATUSES:
            await asyncio.sleep(_retry_delay(response.headers, attempt))
            continue
        return response
    raise RuntimeError("Starvell request did not produce a response")


async def _request_once(method: str, url: str, options: dict, should_raise: bool, can_retry: bool) -> HttpResponse:
    await throttle()
    session = await get_http_session()
    async with session.request(method, url, **options) as response:
        text = await _read_bounded_text(response)
        if 300 <= response.status < 400:
            raise RuntimeError("Unexpected redirect from Starvell")
        if should_raise and not (can_retry and response.status in RETRYABLE_STATUSES):
            try:
                response.raise_for_status()
            except aiohttp.ClientResponseError as exc:
                detail = _error_detail(text)
                if detail:
                    exc.message = f"{exc.message}; Starvell: {detail}"
                raise
        return HttpResponse(
            status=response.status,
            headers=dict(response.headers),
            text=text,
            cookies={name: morsel.value for name, morsel in response.cookies.items()},
        )


async def _read_bounded_text(response) -> str:
    if response.content_length and response.content_length > MAX_RESPONSE_BYTES:
        raise RuntimeError("Starvell response exceeds the size limit")
    body = bytearray()
    async for chunk in response.content.iter_chunked(RESPONSE_CHUNK_SIZE):
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RuntimeError("Starvell response exceeds the size limit")
    return body.decode(response.charset or "utf-8")


async def request_json(method: str, url: str, **kwargs: object) -> object:
    return (await request_text(method, url, **kwargs)).json()

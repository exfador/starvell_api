import asyncio
import os
import threading
import time


def _effective_rpm(default: int = 40) -> int:

    raw = os.getenv("STARVELL_MAX_PER_MINUTE", "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except Exception:
        return default
    if v <= 0:
        return default
    return min(v, 40)


STARVELL_MAX_PER_MINUTE: int = _effective_rpm(40)
MIN_INTERVAL_SECONDS: float = 60.0 / float(STARVELL_MAX_PER_MINUTE)


class _AsyncMinIntervalLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = float(min_interval_seconds)
        self._lock = asyncio.Lock()
        self._next_allowed: float = 0.0

    async def wait(self) -> None:

        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            if self._next_allowed <= 0.0:
                self._next_allowed = now
            delay = self._next_allowed - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = loop.time()
            self._next_allowed = now + self._min_interval


class _SyncMinIntervalLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = float(min_interval_seconds)
        self._lock = threading.Lock()
        self._next_allowed: float = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._next_allowed <= 0.0:
                self._next_allowed = now
            delay = self._next_allowed - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


_async_limiter = _AsyncMinIntervalLimiter(MIN_INTERVAL_SECONDS)
_sync_limiter = _SyncMinIntervalLimiter(MIN_INTERVAL_SECONDS)


async def throttle() -> None:
    await _async_limiter.wait()


def throttle_sync() -> None:
    _sync_limiter.wait()




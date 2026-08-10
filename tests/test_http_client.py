import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import ClientResponseError, web

from api.http_client import _retry_delay, close_http_session, get_http_session, request_json


class HttpClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = 0
        application = web.Application()
        application.router.add_get("/retry", self._retry_handler)
        application.router.add_post("/mutation", self._mutation_handler)
        self.runner = web.AppRunner(application)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        socket = self.site._server.sockets[0]
        self.base_url = f"http://127.0.0.1:{socket.getsockname()[1]}"

    async def asyncTearDown(self):
        await close_http_session()
        await self.runner.cleanup()

    async def _retry_handler(self, request):
        self.calls += 1
        if self.calls == 1:
            return web.json_response({"error": "slow down"}, status=429, headers={"Retry-After": "0"})
        return web.json_response({"ok": True})

    async def _mutation_handler(self, request):
        self.calls += 1
        return web.json_response({"error": "temporary"}, status=503)

    async def test_safe_get_honors_retry_after(self):
        with patch("api.http_client.throttle", AsyncMock()):
            result = await request_json("GET", f"{self.base_url}/retry", retry_safe=True)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.calls, 2)

    async def test_mutating_post_is_not_retried(self):
        with (
            patch("api.http_client.throttle", AsyncMock()),
            self.assertRaises(ClientResponseError),
        ):
            await request_json("POST", f"{self.base_url}/mutation", json={"x": 1})

        self.assertEqual(self.calls, 1)

    async def test_session_is_reused_within_the_runtime_loop(self):
        first = await get_http_session()
        second = await get_http_session()

        self.assertIs(first, second)

    def test_retry_after_header_is_case_insensitive(self):
        self.assertEqual(_retry_delay({"retry-after": "7"}, 0), 7.0)

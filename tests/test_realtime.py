import asyncio
import unittest
import urllib.error
from email.message import Message
from unittest.mock import AsyncMock, patch

import socketio
from aiohttp import web

from tg_bot_exfa import realtime


class FakeStarvellSocketServer:
    def __init__(self, session: str = "good-session"):
        self.session = session
        self.server = socketio.AsyncServer(async_mode="aiohttp", cors_allowed_origins="*")
        self.app = web.Application()
        self.server.attach(self.app)
        self.handshakes: list[dict] = []
        for namespace in realtime.NAMESPACES:
            self.server.on("connect", self._connect, namespace=namespace)

    async def _connect(self, sid, environ, auth=None):
        self.handshakes.append(environ)
        if f"session={self.session}" not in environ.get("HTTP_COOKIE", ""):
            raise socketio.exceptions.ConnectionRefusedError("Unauthorized")

    async def __aenter__(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *exc):
        await self.runner.cleanup()


async def _wait_until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


class RealtimeConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_events_wake_pollers_and_confirm_the_stream(self):
        config = {"SESSION_COOKIE": "good-session"}
        events = realtime.RealtimeSignals()
        async with FakeStarvellSocketServer() as server:
            with patch.object(realtime, "SESSION_CHECK_SECONDS", 0.05):
                task = asyncio.create_task(
                    realtime._connect_once("good-session", lambda: config, events, origin=server.url)
                )
                await _wait_until(lambda: events.connected)
                self.assertTrue(events.chats.is_set() and events.orders.is_set(), "connect must wake both pollers")
                events.chats.clear()
                events.orders.clear()
                self.assertFalse(events.confirmed)

                await server.server.emit("typing", {"chatId": "c-1"}, namespace=realtime.CHATS_NAMESPACE)
                await server.server.emit("message_created", {"chatId": "c-1"}, namespace=realtime.CHATS_NAMESPACE)
                await _wait_until(events.chats.is_set)
                self.assertTrue(events.confirmed)
                self.assertFalse(events.orders.is_set())

                await server.server.emit("sale_update", {"delta": 1}, namespace=realtime.NOTIFICATIONS_NAMESPACE)
                await _wait_until(events.orders.is_set)

                config["SESSION_COOKIE"] = "rotated-session"
                outcome = await asyncio.wait_for(task, 5)

        self.assertEqual(outcome, "session_changed")
        self.assertFalse(events.connected)
        self.assertFalse(events.confirmed)
        handshake = server.handshakes[0]
        self.assertIn("session=good-session", handshake["HTTP_COOKIE"])
        self.assertIn("starvell.theme=dark", handshake["HTTP_COOKIE"])
        self.assertEqual(handshake.get("HTTP_ORIGIN"), "https://starvell.com")

    async def test_rejected_session_is_reported_as_unauthorized(self):
        events = realtime.RealtimeSignals()
        async with FakeStarvellSocketServer() as server:
            outcome = await realtime._connect_once(
                "expired-session", lambda: {"SESSION_COOKIE": "expired-session"}, events, origin=server.url
            )

        self.assertEqual(outcome, "unauthorized")
        self.assertFalse(events.connected)

    async def test_unreachable_server_is_a_plain_failure(self):
        events = realtime.RealtimeSignals()
        with patch.object(realtime, "CONNECT_TIMEOUT_SECONDS", 1):
            outcome = await realtime._connect_once(
                "s", lambda: {"SESSION_COOKIE": "s"}, events, origin="http://127.0.0.1:9"
            )

        self.assertEqual(outcome, "failed")

    async def test_probe_reports_accepted_and_rejected_namespaces(self):
        async with FakeStarvellSocketServer() as server:
            accepted = await realtime.probe("good-session", origin=server.url)
            rejected = await realtime.probe("bad-session", origin=server.url)

        self.assertEqual(accepted["connected"], sorted(realtime.NAMESPACES))
        self.assertEqual(rejected["connected"], [])
        self.assertTrue(any("Unauthorized" in item for item in rejected["rejected"]))


class RealtimeLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_or_missing_session_never_connects(self):
        for config in ({"REALTIME_ENABLED": False, "SESSION_COOKIE": "s"}, {}):
            connect = AsyncMock()
            with (
                patch.object(realtime, "_connect_once", connect),
                patch.object(realtime.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)),
                self.assertRaises(asyncio.CancelledError),
            ):
                await realtime.run_realtime(lambda: config, realtime.RealtimeSignals())
            connect.assert_not_awaited()

    async def test_failures_back_off_and_unauthorized_waits_long(self):
        waits: list[float] = []

        async def fake_sleep(seconds):
            waits.append(seconds)
            if len(waits) == 4:
                raise asyncio.CancelledError

        outcomes = AsyncMock(side_effect=["failed", "failed", "unauthorized", "closed"])
        with (
            patch.object(realtime, "_connect_once", outcomes),
            patch.object(realtime.asyncio, "sleep", fake_sleep),
            self.assertRaises(asyncio.CancelledError),
        ):
            await realtime.run_realtime(lambda: {"SESSION_COOKIE": "s"}, realtime.RealtimeSignals())

        self.assertEqual(waits, [5, 10, realtime.UNAUTHORIZED_RETRY_SECONDS, realtime.RETRY_MIN_SECONDS])


class SocketOriginTests(unittest.TestCase):
    def test_qrator_uses_port_8443_like_the_site(self):
        with patch.object(realtime.urllib.request, "urlopen", return_value=_Context({"server": "QRATOR"})):
            self.assertEqual(realtime.socket_origin(), "https://starvell.com:8443")
        with patch.object(realtime.urllib.request, "urlopen", return_value=_Context({"server": "nginx"})):
            self.assertEqual(realtime.socket_origin(), "https://starvell.com")

    def test_error_page_still_reveals_the_proxy(self):
        headers = Message()
        headers["server"] = "QRATOR"
        error = urllib.error.HTTPError("https://starvell.com/", 403, "Forbidden", headers, None)
        with patch.object(realtime.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(realtime.socket_origin(), "https://starvell.com:8443")
        with patch.object(realtime.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertEqual(realtime.socket_origin(), "https://starvell.com")


class _Context:
    def __init__(self, headers):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp

from api.auth import fetch_homepage_data
from api.bump import bump_categories
from api.chats import fetch_chats
from api.find_lots_user import find_user_lots
from api.http_client import HttpResponse, request_text
from api.messages import fetch_chat_messages
from api.next_data import _fetch_build_id
from api.offer_details import fetch_offer_detail
from api.orders import fetch_sells, refund_order
from api.send_message import send_chat_image, send_chat_message


class _FakeResponse:
    def __init__(self, status, text="", headers=None, cookies=None):
        self.status = status
        self._text = text
        self.headers = headers or {}
        self.cookies = {
            name: SimpleNamespace(value=value)
            for name, value in (cookies or {}).items()
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                SimpleNamespace(real_url="https://starvell.com/test"),
                (),
                status=self.status,
                message="contract failure",
            )


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class ApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_homepage_request_propagates_session_and_response_cookies(self):
        response = HttpResponse(
            200,
            {"Content-Type": "application/json"},
            json.dumps({"pageProps": {"user": {"id": 7}}}),
            {"sid": "sid-value", "starvell.my_games": "1,2"},
        )
        request = AsyncMock(return_value=response)
        with (
            patch("api.auth.get_build_id", AsyncMock(return_value="build")),
            patch("api.auth.request_text", request),
        ):
            result = await fetch_homepage_data("session-value", my_games_cookie="9,10")

        self.assertTrue(result["authorized"])
        self.assertEqual(result["sid"], "sid-value")
        self.assertEqual(result["my_games"], "1,2")
        self.assertEqual(
            request.await_args.kwargs["cookies"],
            {
                "session": "session-value",
                "starvell.theme": "dark",
                "starvell.time_zone": "Europe/Moscow",
                "starvell.my_games": "9,10",
            },
        )
        self.assertTrue(request.await_args.kwargs["retry_safe"])

    async def test_chat_bff_payload_and_limit_are_normalized(self):
        request = AsyncMock(return_value={"messagesListResult": {"items": []}})
        with patch("api.messages.request_json", request):
            await fetch_chat_messages(
                "session",
                "chat-1",
                limit=100000,
                my_games_cookie="2,3",
                interlocutor_id=42,
            )

        args = request.await_args
        self.assertEqual(args.args[:2], ("POST", "https://starvell.com/api/bff/chat-page"))
        self.assertEqual(args.kwargs["json"]["interlocutorId"], 42)
        self.assertEqual(args.kwargs["json"]["messagesListDto"]["limit"], 100)
        self.assertEqual(args.kwargs["cookies"]["starvell.my_games"], "2,3")
        self.assertNotIn("retry_safe", args.kwargs)

    async def test_sells_page_is_encoded_in_next_data_url(self):
        request = AsyncMock(return_value={"pageProps": {"orders": []}})
        with (
            patch("api.orders.get_build_id", AsyncMock(return_value="build")),
            patch("api.orders.request_json", request),
        ):
            await fetch_sells("session", page=3, my_games_cookie="4,5")

        self.assertIn("?page=3", request.await_args.args[1])
        self.assertEqual(request.await_args.kwargs["cookies"]["starvell.my_games"], "4,5")
        self.assertTrue(request.await_args.kwargs["retry_safe"])

    async def test_build_id_html_get_uses_session_cookie_and_safe_retry(self):
        response = HttpResponse(
            200,
            {"Content-Type": "text/html"},
            '<script id="__NEXT_DATA__">{"buildId":"build-7"}</script>',
            {},
        )
        request = AsyncMock(return_value=response)
        with patch("api.next_data.request_text", request):
            build_id = await _fetch_build_id("session-value")

        self.assertEqual(build_id, "build-7")
        self.assertEqual(request.await_args.args, ("GET", "https://starvell.com/"))
        self.assertEqual(
            request.await_args.kwargs["cookies"],
            {"session": "session-value", "starvell.theme": "dark"},
        )
        self.assertTrue(request.await_args.kwargs["retry_safe"])

    async def test_message_mutation_uses_expected_payload_without_safe_retry(self):
        response = HttpResponse(
            200,
            {"Content-Type": "application/json"},
            json.dumps({"success": True}),
            {},
        )
        request = AsyncMock(return_value=response)
        with patch("api.send_message.request_text", request):
            await send_chat_message("session", "chat-1", "hello", my_games_cookie="6")

        self.assertEqual(request.await_args.kwargs["json"], {"chatId": "chat-1", "content": "hello"})
        self.assertEqual(request.await_args.kwargs["cookies"]["starvell.my_games"], "6")
        self.assertNotIn("retry_safe", request.await_args.kwargs)

    async def test_refund_rejects_success_http_with_unexpected_body(self):
        response = HttpResponse(200, {"Content-Type": "text/html"}, "ok", {})
        with (
            patch("api.orders.request_text", AsyncMock(return_value=response)),
            self.assertRaisesRegex(RuntimeError, "non-JSON"),
        ):
            await refund_order("session", "order-1")

    async def test_chat_collection_is_normalized(self):
        request = AsyncMock(return_value={"pageProps": {"chats": [{"id": "chat-1"}]}})
        with (
            patch("api.chats.get_build_id", AsyncMock(return_value="build")),
            patch("api.chats.request_json", request),
        ):
            result = await fetch_chats("session")

        self.assertEqual(result["pageProps"]["chats"][0]["id"], "chat-1")

    async def test_lot_categories_are_converted_to_stable_fields(self):
        payload = {
            "pageProps": {
                "userProfileOffers": [
                    {
                        "id": "3",
                        "slug": "currency",
                        "gameId": "2",
                        "game": {"slug": "roblox"},
                        "offers": [
                            {
                                "id": "107615",
                                "publicId": "e7e3c17c-734a-44f9-9244-f8bb43329fd0",
                                "price": 100,
                                "descriptions": {"rus": {"briefDescription": "Robux"}},
                            }
                        ],
                    }
                ]
            }
        }
        request = AsyncMock(return_value=payload)
        with (
            patch("api.find_lots_user.get_build_id", AsyncMock(return_value="build")),
            patch("api.find_lots_user.request_json", request),
        ):
            result = await find_user_lots(
                "session",
                "sid",
                42,
                username="seller name",
                my_games_cookie="8",
            )

        self.assertEqual(
            result["lots"][0]["id"],
            "e7e3c17c-734a-44f9-9244-f8bb43329fd0",
        )
        self.assertEqual(result["lots"][0]["category_id"], 3)
        self.assertEqual(result["lots"][0]["game_id"], 2)
        self.assertEqual(result["my_games"], "2")
        self.assertIn("/profile/seller%20name.json", request.await_args.args[1])
        self.assertEqual(request.await_args.kwargs["params"], {"username": "seller name"})
        self.assertEqual(request.await_args.kwargs["cookies"]["sid"], "sid")
        self.assertEqual(request.await_args.kwargs["cookies"]["starvell.my_games"], "8")
        self.assertTrue(request.await_args.kwargs["retry_safe"])

    async def test_offer_detail_uses_offer_next_route(self):
        request = AsyncMock(return_value={"pageProps": {"offer": {"id": 7}}})
        with (
            patch("api.offer_details.get_build_id", AsyncMock(return_value="build")),
            patch("api.offer_details.request_json", request),
        ):
            result = await fetch_offer_detail("session", 7, sid_cookie="sid", my_games_cookie="8")

        self.assertIn("/offers/7.json", request.await_args.args[1])
        self.assertEqual(request.await_args.kwargs["params"], {"offer_id": "7"})
        self.assertEqual(result["pageProps"]["offer"]["id"], 7)
        self.assertEqual(request.await_args.kwargs["cookies"]["sid"], "sid")
        self.assertEqual(request.await_args.kwargs["cookies"]["starvell.my_games"], "8")
        self.assertTrue(request.await_args.kwargs["retry_safe"])

    async def test_bump_preserves_request_and_response_envelope(self):
        response = HttpResponse(
            200,
            {"Content-Type": "application/json"},
            json.dumps({"success": True}),
            {},
        )
        request = AsyncMock(return_value=response)
        with patch("api.bump.request_text", request):
            result = await bump_categories("session", "sid", 2, [3, 4], my_games_cookie="8")

        self.assertEqual(result["request"], {"gameId": 2, "categoryIds": [3, 4]})
        self.assertTrue(result["response"]["success"])
        self.assertEqual(request.await_args.kwargs["json"], {"gameId": 2, "categoryIds": [3, 4]})
        self.assertEqual(request.await_args.kwargs["cookies"]["sid"], "sid")
        self.assertEqual(request.await_args.kwargs["cookies"]["starvell.my_games"], "8")
        self.assertFalse(request.await_args.kwargs["raise_for_status"])
        self.assertNotIn("retry_safe", request.await_args.kwargs)

    async def test_bump_429_is_exposed_as_failure_without_mutation_retry(self):
        response = HttpResponse(
            429,
            {"Content-Type": "application/json"},
            json.dumps({"success": False, "message": "rate limited"}),
            {},
        )
        request = AsyncMock(return_value=response)
        with patch("api.bump.request_text", request):
            result = await bump_categories("session", "sid", 2, [3])

        self.assertFalse(result["response"]["success"])
        self.assertEqual(result["response"]["status"], 429)
        request.assert_awaited_once()
        self.assertNotIn("retry_safe", request.await_args.kwargs)

    async def test_image_mutation_uses_query_multipart_and_all_cookies_without_retry(self):
        response = HttpResponse(
            200,
            {"Content-Type": "application/json"},
            json.dumps({"success": True}),
            {},
        )
        request = AsyncMock(return_value=response)
        with patch("api.send_message.request_text", request):
            await send_chat_image(
                "session",
                "chat-1",
                b"image-bytes",
                filename="proof.jpg",
                content_type="image/jpeg",
                content=" caption ",
                sid_cookie="sid",
                my_games_cookie="4,5",
            )

        args = request.await_args
        self.assertEqual(args.args, ("POST", "https://starvell.com/api/messages/send-with-image"))
        self.assertEqual(args.kwargs["params"], {"chatId": "chat-1"})
        self.assertEqual(args.kwargs["cookies"]["sid"], "sid")
        self.assertEqual(args.kwargs["cookies"]["starvell.my_games"], "4,5")
        self.assertEqual(args.kwargs["timeout"], 60)
        self.assertNotIn("retry_safe", args.kwargs)
        fields = {
            field[0]["name"]: (dict(field[0]), field[2])
            for field in args.kwargs["data"]._fields
        }
        self.assertEqual(fields["image"][0]["filename"], "proof.jpg")
        self.assertEqual(fields["image"][1], b"image-bytes")
        self.assertEqual(fields["content"][1], "caption")

    async def test_refund_payload_cookies_and_no_retry_contract(self):
        response = HttpResponse(
            200,
            {"Content-Type": "application/json; charset=utf-8"},
            json.dumps({"success": True}),
            {},
        )
        request = AsyncMock(return_value=response)
        with patch("api.orders.request_text", request):
            result = await refund_order(
                "session",
                "order-1",
                sid_cookie="sid",
                my_games_cookie="4,5",
            )

        self.assertTrue(result["success"])
        self.assertEqual(request.await_args.args, ("POST", "https://starvell.com/api/orders/refund"))
        self.assertEqual(request.await_args.kwargs["json"], {"orderId": "order-1"})
        self.assertEqual(request.await_args.kwargs["cookies"]["sid"], "sid")
        self.assertEqual(request.await_args.kwargs["cookies"]["starvell.my_games"], "4,5")
        self.assertNotIn("retry_safe", request.await_args.kwargs)

    async def test_stale_next_build_get_resets_and_retries_once(self):
        stale = aiohttp.ClientResponseError(
            SimpleNamespace(real_url="https://starvell.com/_next/data/stale/chat.json"),
            (),
            status=404,
            message="stale build",
        )
        request = AsyncMock(
            side_effect=[stale, {"pageProps": {"chats": [{"id": "chat-1"}]}}]
        )
        build_id = AsyncMock(side_effect=["stale", "fresh"])
        with (
            patch("api.chats.request_json", request),
            patch("api.chats.get_build_id", build_id),
            patch("api.chats.reset_build_id") as reset,
        ):
            result = await fetch_chats("session")

        self.assertEqual(result["pageProps"]["chats"], [{"id": "chat-1"}])
        self.assertEqual(request.await_count, 2)
        self.assertIn("/_next/data/stale/chat.json", request.await_args_list[0].args[1])
        self.assertIn("/_next/data/fresh/chat.json", request.await_args_list[1].args[1])
        self.assertTrue(all(call.kwargs["retry_safe"] for call in request.await_args_list))
        reset.assert_called_once_with()

    async def test_safe_transport_retries_429_and_retryable_5xx(self):
        session = _FakeSession(
            [
                _FakeResponse(429, "rate limited", {"Retry-After": "0"}),
                _FakeResponse(500, "unavailable"),
                _FakeResponse(200, "ok", cookies={"sid": "new-sid"}),
            ]
        )
        with (
            patch("api.http_client.get_http_session", AsyncMock(return_value=session)),
            patch("api.http_client.throttle", AsyncMock()) as throttle,
            patch("api.http_client.asyncio.sleep", AsyncMock()) as sleep,
        ):
            response = await request_text(
                "GET",
                "https://starvell.com/test",
                cookies={"session": "session"},
                retry_safe=True,
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.cookies, {"sid": "new-sid"})
        self.assertEqual(len(session.requests), 3)
        self.assertEqual(throttle.await_count, 3)
        self.assertEqual(sleep.await_count, 2)
        self.assertTrue(all(item[0] == "GET" for item in session.requests))
        self.assertTrue(
            all(item[2]["cookies"] == {"session": "session"} for item in session.requests)
        )

    async def test_auth_and_non_retryable_server_errors_are_not_replayed(self):
        for status in (401, 403):
            with self.subTest(status=status):
                session = _FakeSession([_FakeResponse(status, "rejected")])
                with (
                    patch("api.http_client.get_http_session", AsyncMock(return_value=session)),
                    patch("api.http_client.throttle", AsyncMock()),
                    patch("api.http_client.asyncio.sleep", AsyncMock()) as sleep,
                ):
                    with self.assertRaises(aiohttp.ClientResponseError) as raised:
                        await request_text(
                            "GET",
                            "https://starvell.com/test",
                            retry_safe=True,
                        )

                self.assertEqual(raised.exception.status, status)
                self.assertIn("rejected", str(raised.exception))
                self.assertEqual(len(session.requests), 1)
                sleep.assert_not_awaited()

    async def test_mutation_429_is_not_blindly_retried(self):
        session = _FakeSession([_FakeResponse(429, "rate limited")])
        with (
            patch("api.http_client.get_http_session", AsyncMock(return_value=session)),
            patch("api.http_client.throttle", AsyncMock()) as throttle,
            patch("api.http_client.asyncio.sleep", AsyncMock()) as sleep,
        ):
            with self.assertRaises(aiohttp.ClientResponseError) as raised:
                await request_text(
                    "POST",
                    "https://starvell.com/api/messages/send",
                    json={"chatId": "chat-1", "content": "hello"},
                )

        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(len(session.requests), 1)
        self.assertEqual(throttle.await_count, 1)
        sleep.assert_not_awaited()

    def test_malformed_json_is_rejected_at_transport_boundary(self):
        response = HttpResponse(
            200,
            {"Content-Type": "application/json"},
            "not-json",
            {},
        )

        with self.assertRaisesRegex(RuntimeError, "Invalid JSON response from Starvell"):
            response.json()

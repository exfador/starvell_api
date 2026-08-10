import unittest
from unittest.mock import patch

from api.orders import fetch_sells_all


class FetchSellsAllTests(unittest.IsolatedAsyncioTestCase):
    async def test_stops_when_a_page_contains_only_seen_orders(self):
        calls = []

        async def fake_fetch_sells(session_cookie, page=None, my_games_cookie=None):
            calls.append((session_cookie, page, my_games_cookie))
            return {"pageProps": {"orders": [{"id": "order-1"}]}}

        with patch("api.orders.fetch_sells", fake_fetch_sells):
            orders = await fetch_sells_all("session", max_pages=200, my_games_cookie="games")

        self.assertEqual(orders, [{"id": "order-1"}])
        self.assertEqual(calls, [("session", None, "games"), ("session", 2, "games")])

    async def test_propagates_a_fetch_failure(self):
        async def fake_fetch_sells(session_cookie, page=None, my_games_cookie=None):
            raise RuntimeError("upstream unavailable")

        with patch("api.orders.fetch_sells", fake_fetch_sells):
            with self.assertRaisesRegex(RuntimeError, "upstream unavailable"):
                await fetch_sells_all("session")

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tg_bot_exfa.monitor import (
    AutodeliveryPending,
    _check_chats,
    _check_orders,
    _deliver_autodelivery_codes,
    _fetch_offer_details_bounded,
    _fetch_recent_orders,
    _remember_seen,
    _safe_interval,
    _seen_bucket,
    start_monitor,
)
from tg_bot_exfa.storage.db import Database


class FakeAutodeliveryDatabase:
    def __init__(self):
        self.delivery = {
            "order_id": "order-1",
            "product": "Robux",
            "quantity": 1,
            "item_ids": [7],
            "item_values": ["code-7"],
            "state": "reserved",
        }
        self.mark_order_delivery_sending = AsyncMock(return_value=True)
        self.mark_order_delivery_sent = AsyncMock(return_value=1)
        self.mark_order_delivery_error = AsyncMock()

    async def reserve_order_delivery(self, order_id, product, quantity):
        return self.delivery

    async def get_order_delivery(self, order_id):
        return self.delivery


class AutodeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_send_is_not_retried_automatically(self):
        database = FakeAutodeliveryDatabase()
        order = {"id": "order-1", "quantity": 1, "user": {"id": 42}}

        with (
            patch(
                "tg_bot_exfa.monitor.fetch_chats",
                AsyncMock(
                    return_value={
                        "pageProps": {
                            "chats": [{"id": "chat-1", "participants": [{"id": "42"}]}]
                        }
                    }
                ),
            ),
            patch(
                "tg_bot_exfa.monitor.send_chat_message",
                AsyncMock(side_effect=RuntimeError("network down")),
            ),
        ):
            with self.assertRaises(AutodeliveryPending):
                await _deliver_autodelivery_codes("session", database, order, "Robux")

        database.mark_order_delivery_sent.assert_not_awaited()
        database.mark_order_delivery_error.assert_awaited_once()

    async def test_successful_send_acknowledges_exact_inventory_rows(self):
        database = FakeAutodeliveryDatabase()
        order = {"id": "order-1", "quantity": 1, "user": {"id": "42"}}
        send = AsyncMock(return_value={"success": True})

        with (
            patch(
                "tg_bot_exfa.monitor.fetch_chats",
                AsyncMock(
                    return_value={
                        "pageProps": {
                            "chats": [{"id": "chat-1", "participants": [{"id": 42}]}]
                        }
                    }
                ),
            ),
            patch("tg_bot_exfa.monitor.send_chat_message", send),
            patch(
                "tg_bot_exfa.monitor.load_config",
                return_value={"WATERMARK_ON": False},
            ),
        ):
            delivered = await _deliver_autodelivery_codes("session", database, order, "Robux")

        send.assert_awaited_once_with("session", "chat-1", "code-7")
        database.mark_order_delivery_sending.assert_awaited_once_with("order-1", "chat-1")
        database.mark_order_delivery_sent.assert_awaited_once_with("order-1")
        self.assertEqual(delivered, ("Robux", "code-7"))

    async def test_ambiguous_delivery_is_reconciled_from_chat_history(self):
        database = FakeAutodeliveryDatabase()
        database.delivery.update({"state": "sending", "chat_id": "chat-1"})
        order = {"id": "order-1", "quantity": 1, "user": {"id": 42}}
        with patch(
            "tg_bot_exfa.monitor.fetch_chat_messages",
            AsyncMock(return_value=[{"id": "message-1", "content": "code-7"}]),
        ):
            delivered = await _deliver_autodelivery_codes("session", database, order, "Robux")

        self.assertEqual(delivered, ("Robux", "code-7"))
        database.mark_order_delivery_sent.assert_awaited_once_with("order-1")


class SeenMessageTests(unittest.TestCase):
    def test_seen_message_cache_is_bounded(self):
        seen = {"old-chat": {"message-1"}}

        bucket = _seen_bucket(seen, "new-chat", max_chats=1)
        _remember_seen(bucket, "message-2", max_messages=1)
        _remember_seen(bucket, "message-3", max_messages=1)

        self.assertEqual(seen, {"new-chat": {"message-3"}})

    def test_invalid_poll_intervals_fall_back_and_clamp(self):
        self.assertEqual(_safe_interval("bad", 5), 5)
        self.assertEqual(_safe_interval(float("nan"), 5), 5)
        self.assertEqual(_safe_interval(-100, 5), 1)
        self.assertEqual(_safe_interval(99999, 5), 3600)


class OfferEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_offer_detail_batches_bound_in_flight_tasks(self):
        active = 0
        maximum_active = 0

        async def fetch(*args, **kwargs):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.001)
            active -= 1
            return {"pageProps": {"offer": {"id": args[1]}}}

        lots = [{"id": index} for index in range(12)]
        with patch("tg_bot_exfa.monitor.fetch_offer_detail", fetch):
            results = await _fetch_offer_details_bounded("session", lots, "sid", None, batch_size=5)

        self.assertEqual(len(results), 12)
        self.assertLessEqual(maximum_active, 5)


class MonitorLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_monitor_cancels_all_supervised_children(self):
        started = [asyncio.Event(), asyncio.Event()]
        stopped = [asyncio.Event(), asyncio.Event()]

        async def child(index, *args, **kwargs):
            started[index].set()
            try:
                await asyncio.Future()
            finally:
                stopped[index].set()

        with (
            patch("tg_bot_exfa.monitor._version_poll_loop", lambda *a, **kw: child(0)),
            patch("tg_bot_exfa.monitor._monitor_supervisor_loop", lambda *a, **kw: child(1)),
        ):
            task = asyncio.create_task(start_monitor())
            await asyncio.gather(*(event.wait() for event in started))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(all(event.is_set() for event in stopped))


class FakeChatDatabase:
    def __init__(self):
        self.set_last_notified_message = AsyncMock()

    async def get_last_notified_message(self, chat_id):
        return "message-0"


class ChatCursorTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_failure_stops_before_newer_messages(self):
        database = FakeChatDatabase()
        notify = AsyncMock(side_effect=RuntimeError("telegram unavailable"))
        chat_page = {
            "pageProps": {
                "user": {"id": 99},
                "chats": [
                    {
                        "id": "chat-1",
                        "unreadMessageCount": 2,
                        "participants": [
                            {"id": 99, "username": "seller"},
                            {"id": 42, "username": "buyer"},
                        ],
                        "lastMessage": {
                            "id": "message-2",
                            "authorId": 42,
                            "content": "second",
                            "metadata": {},
                        },
                    }
                ],
            }
        }
        messages = [
            {"id": "message-2", "authorId": 42, "content": "second", "metadata": {}},
            {"id": "message-1", "authorId": 42, "content": "first", "metadata": {}},
            {"id": "message-0", "authorId": 42, "content": "old", "metadata": {}},
        ]

        with (
            patch("tg_bot_exfa.monitor.fetch_chats", AsyncMock(return_value=chat_page)),
            patch("tg_bot_exfa.monitor.fetch_chat_messages", AsyncMock(return_value=messages)),
            patch("tg_bot_exfa.monitor.send_chat_notification", notify),
            patch("tg_bot_exfa.monitor.load_config", return_value={"WELCOME_ENABLED": False}),
        ):
            with self.assertLogs("exfador.monitor", level="WARNING"):
                await _check_chats("session", database, user_id=99)

        self.assertEqual(notify.await_count, 1)
        database.set_last_notified_message.assert_not_awaited()

    async def test_own_latest_message_advances_cursor_and_caps_fetch_limit(self):
        database = FakeChatDatabase()
        chat_page = {
            "pageProps": {
                "user": {"id": 99},
                "chats": [
                    {
                        "id": "chat-1",
                        "unreadMessageCount": 1000000,
                        "participants": [{"id": 99}, {"id": 42, "username": "buyer"}],
                        "lastMessage": {
                            "id": "message-2",
                            "authorId": 99,
                            "content": "seller reply",
                            "metadata": {},
                        },
                    }
                ],
            }
        }
        fetch_messages = AsyncMock(
            return_value=[
                {"id": "message-2", "authorId": 99, "content": "seller reply", "metadata": {}},
                {"id": "message-0", "authorId": 42, "content": "old", "metadata": {}},
            ]
        )
        notify = AsyncMock()
        with (
            patch("tg_bot_exfa.monitor.fetch_chats", AsyncMock(return_value=chat_page)),
            patch("tg_bot_exfa.monitor.fetch_chat_messages", fetch_messages),
            patch("tg_bot_exfa.monitor.send_chat_notification", notify),
            patch("tg_bot_exfa.monitor.load_config", return_value={"WELCOME_ENABLED": False}),
        ):
            await _check_chats("session", database, user_id=99)

        notify.assert_not_awaited()
        database.set_last_notified_message.assert_awaited_once_with("chat-1", "message-2")
        self.assertEqual(fetch_messages.await_args.kwargs["limit"], 100)

    async def test_auto_latest_message_does_not_hide_earlier_user_message(self):
        database = FakeChatDatabase()
        chat_page = {
            "pageProps": {
                "user": {"id": 99},
                "chats": [
                    {
                        "id": "chat-1",
                        "unreadMessageCount": 1,
                        "participants": [{"id": 99}, {"id": 42, "username": "buyer"}],
                        "lastMessage": {
                            "id": "message-auto",
                            "content": "system event",
                            "metadata": {"isAuto": True},
                        },
                    }
                ],
            }
        }
        messages = [
            {
                "id": "message-auto",
                "content": "system event",
                "metadata": {"isAuto": True},
            },
            {"id": "message-1", "authorId": 42, "content": "hello", "metadata": {}},
            {"id": "message-0", "authorId": 42, "content": "old", "metadata": {}},
        ]
        notify = AsyncMock(return_value=1)
        with (
            patch("tg_bot_exfa.monitor.fetch_chats", AsyncMock(return_value=chat_page)),
            patch("tg_bot_exfa.monitor.fetch_chat_messages", AsyncMock(return_value=messages)),
            patch("tg_bot_exfa.monitor.send_chat_notification", notify),
            patch("tg_bot_exfa.monitor.load_config", return_value={"WELCOME_ENABLED": False}),
        ):
            await _check_chats("session", database, user_id=99)

        notify.assert_awaited_once()
        self.assertEqual(notify.await_args.args[1], "hello")
        self.assertEqual(
            database.set_last_notified_message.await_args_list[-1].args,
            ("chat-1", "message-auto"),
        )

    async def test_auto_latest_message_does_not_advance_cursor_when_scan_fails(self):
        database = FakeChatDatabase()
        chat_page = {
            "pageProps": {
                "user": {"id": 99},
                "chats": [
                    {
                        "id": "chat-1",
                        "unreadMessageCount": 1,
                        "participants": [{"id": 99}, {"id": 42, "username": "buyer"}],
                        "lastMessage": {
                            "id": "message-auto",
                            "content": "system event",
                            "metadata": {"isAuto": True},
                        },
                    }
                ],
            }
        }
        fetch_messages = AsyncMock(side_effect=RuntimeError("network down"))
        notify = AsyncMock()
        with (
            patch("tg_bot_exfa.monitor.fetch_chats", AsyncMock(return_value=chat_page)),
            patch("tg_bot_exfa.monitor.fetch_chat_messages", fetch_messages),
            patch("tg_bot_exfa.monitor.send_chat_notification", notify),
            patch("tg_bot_exfa.monitor.load_config", return_value={"WELCOME_ENABLED": False}),
        ):
            with self.assertLogs("exfador.monitor", level="WARNING"):
                await _check_chats("session", database, user_id=99)

        fetch_messages.assert_awaited_once()
        notify.assert_not_awaited()
        database.set_last_notified_message.assert_not_awaited()

    async def test_empty_latest_message_advances_cursor_after_successful_scan(self):
        database = FakeChatDatabase()
        chat_page = {
            "pageProps": {
                "user": {"id": 99},
                "chats": [
                    {
                        "id": "chat-1",
                        "unreadMessageCount": 1,
                        "participants": [{"id": 99}, {"id": 42, "username": "buyer"}],
                        "lastMessage": {
                            "id": "message-empty",
                            "authorId": 42,
                            "content": "",
                            "metadata": {},
                        },
                    }
                ],
            }
        }
        messages = [
            {"id": "message-empty", "authorId": 42, "content": "", "metadata": {}},
            {"id": "message-0", "authorId": 42, "content": "old", "metadata": {}},
        ]
        notify = AsyncMock()
        with (
            patch("tg_bot_exfa.monitor.fetch_chats", AsyncMock(return_value=chat_page)),
            patch("tg_bot_exfa.monitor.fetch_chat_messages", AsyncMock(return_value=messages)),
            patch("tg_bot_exfa.monitor.send_chat_notification", notify),
            patch("tg_bot_exfa.monitor.load_config", return_value={"WELCOME_ENABLED": False}),
        ):
            await _check_chats("session", database, user_id=99)

        notify.assert_not_awaited()
        database.set_last_notified_message.assert_awaited_once_with(
            "chat-1",
            "message-empty",
        )

    async def test_welcome_message_renders_chat_template_variables(self):
        database = FakeChatDatabase()
        chat_page = {
            "pageProps": {
                "user": {"id": 99},
                "chats": [
                    {
                        "id": "chat-1",
                        "unreadMessageCount": 1,
                        "participants": [
                            {"id": 99, "username": "seller"},
                            {"id": 42, "username": "buyer"},
                        ],
                        "lastMessage": {
                            "id": "message-1",
                            "authorId": 42,
                            "content": "Need help",
                            "metadata": {},
                        },
                    }
                ],
            }
        }
        messages = [
            {"id": "message-1", "authorId": 42, "content": "Need help", "metadata": {}},
            {"id": "message-0", "authorId": 42, "content": "old", "metadata": {}},
        ]
        send_welcome = AsyncMock(return_value={"success": True})
        with (
            patch("tg_bot_exfa.monitor.fetch_chats", AsyncMock(return_value=chat_page)),
            patch("tg_bot_exfa.monitor.fetch_chat_messages", AsyncMock(return_value=messages)),
            patch("tg_bot_exfa.monitor.send_chat_message", send_welcome),
            patch("tg_bot_exfa.monitor.send_chat_notification", AsyncMock(return_value=1)),
            patch(
                "tg_bot_exfa.monitor.load_config",
                return_value={
                    "WELCOME_ENABLED": True,
                    "WELCOME_COOLDOWN_MINUTES": 10,
                    "WELCOME_TEXT": "Привет, {buyer}! chat=$chat_id msg={message_text}",
                    "WATERMARK_ON": False,
                },
            ),
        ):
            await _check_chats("session", database, user_id=99)

        send_welcome.assert_awaited_once_with(
            "session",
            "chat-1",
            "Привет, buyer! chat=chat-1 msg=Need help",
        )

    async def test_fresh_cursor_delivers_existing_unread_messages(self):
        class FreshDatabase(FakeChatDatabase):
            async def get_last_notified_message(self, chat_id):
                return None

        database = FreshDatabase()
        chat_page = {
            "pageProps": {
                "user": {"id": 99},
                "chats": [
                    {
                        "id": "chat-1",
                        "unreadMessageCount": 2,
                        "participants": [{"id": 99}, {"id": 42, "username": "buyer"}],
                        "lastMessage": {
                            "id": "message-2",
                            "authorId": 42,
                            "content": "second",
                            "metadata": {},
                        },
                    }
                ],
            }
        }
        messages = [
            {"id": "message-2", "authorId": 42, "content": "second", "metadata": {}},
            {"id": "message-1", "authorId": 42, "content": "first", "metadata": {}},
        ]
        notify = AsyncMock(return_value=1)
        with (
            patch("tg_bot_exfa.monitor.fetch_chats", AsyncMock(return_value=chat_page)),
            patch("tg_bot_exfa.monitor.fetch_chat_messages", AsyncMock(return_value=messages)),
            patch("tg_bot_exfa.monitor.send_chat_notification", notify),
            patch("tg_bot_exfa.monitor.load_config", return_value={"WELCOME_ENABLED": False}),
        ):
            await _check_chats("session", database, user_id=99)

        self.assertEqual(notify.await_count, 2)
        self.assertEqual(
            database.set_last_notified_message.await_args_list[-1].args,
            ("chat-1", "message-2"),
        )


class FakeOrderDatabase:
    def __init__(self):
        self.mark_order_notified = AsyncMock()

    async def is_order_notified(self, order_id):
        return False

    async def reserve_order_delivery(self, order_id, product, quantity):
        return None

    async def get_order_status(self, order_id):
        return None

    async def set_order_status(self, order_id, status):
        return None


class OrderRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_telegram_notification_does_not_suppress_retry(self):
        database = FakeOrderDatabase()
        order = {
            "id": "order-1",
            "status": "CREATED",
            "quantity": 1,
            "user": {"id": 42, "username": "buyer"},
            "offerDetails": {
                "descriptions": {"rus": {"briefDescription": "Robux"}},
                "game": {"name": "Roblox"},
                "category": {"name": "Currency"},
            },
        }

        with (
            patch(
                "tg_bot_exfa.monitor.fetch_sells",
                AsyncMock(return_value={"pageProps": {"orders": [order]}}),
            ),
            patch(
                "tg_bot_exfa.monitor.send_order_notification",
                AsyncMock(side_effect=RuntimeError("telegram unavailable")),
            ),
            patch("tg_bot_exfa.monitor.load_config", return_value={"DEBUG": False}),
        ):
            with self.assertLogs("exfador.monitor", level="WARNING"):
                await _check_orders("session", database)

        database.mark_order_notified.assert_not_awaited()

    async def test_owner_notification_retry_never_resends_buyer_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.sqlite3"))
            await database.init()
            await database.add_autodelivery_items("Robux", ["code-1"])
            order = {
                "id": "order-1",
                "status": "CREATED",
                "quantity": 1,
                "user": {"id": 42, "username": "buyer"},
                "offerDetails": {
                    "descriptions": {"rus": {"briefDescription": "Robux"}},
                    "game": {"name": "Roblox"},
                    "category": {"name": "Currency"},
                },
            }
            buyer_send = AsyncMock(return_value={"success": True})
            owner_send = AsyncMock(side_effect=[RuntimeError("telegram down"), None])
            with (
                patch(
                    "tg_bot_exfa.monitor.fetch_sells",
                    AsyncMock(return_value={"pageProps": {"orders": [order]}}),
                ),
                patch(
                    "tg_bot_exfa.monitor.fetch_chats",
                    AsyncMock(
                        return_value={
                            "pageProps": {
                                "chats": [
                                    {"id": "chat-1", "participants": [{"id": 42}]}
                                ]
                            }
                        }
                    ),
                ),
                patch("tg_bot_exfa.monitor.send_chat_message", buyer_send),
                patch("tg_bot_exfa.monitor.send_order_notification", owner_send),
                patch("tg_bot_exfa.monitor.load_config", return_value={"DEBUG": False, "WATERMARK_ON": False}),
            ):
                with self.assertLogs("exfador.monitor", level="WARNING"):
                    await _check_orders("session", database)
                await _check_orders("session", database)

            self.assertEqual(buyer_send.await_count, 1)
            self.assertEqual(owner_send.await_count, 2)
            self.assertTrue(await database.is_order_notified("order-1"))
            delivery = await database.get_order_delivery("order-1")
            self.assertEqual(delivery["state"], "owner_notified")

    async def test_completed_notification_failure_retries_before_status_commit(self):
        class CompletionDatabase:
            def __init__(self):
                self.status = "CREATED"
                self.set_order_status = AsyncMock(side_effect=self._set_status)

            async def _set_status(self, order_id, status):
                self.status = status

            async def get_order_status(self, order_id):
                return self.status

        database = CompletionDatabase()
        order = {"id": "order-1", "status": "COMPLETED"}
        completed = AsyncMock(side_effect=[RuntimeError("telegram down"), 1])
        with (
            patch(
                "tg_bot_exfa.monitor.fetch_sells",
                AsyncMock(return_value={"pageProps": {"orders": [order]}}),
            ),
            patch("tg_bot_exfa.monitor.send_order_completed_notification", completed),
        ):
            with self.assertLogs("exfador.monitor", level="WARNING"):
                await _check_orders("session", database)
            await _check_orders("session", database)

        self.assertEqual(completed.await_count, 2)
        database.set_order_status.assert_awaited_once_with("order-1", "COMPLETED")

    async def test_order_poll_includes_later_pages_and_stops_on_empty_page(self):
        fetch = AsyncMock(
            side_effect=[
                {"pageProps": {"orders": [{"id": "order-1"}]}},
                {"pageProps": {"orders": [{"id": "order-2"}]}},
                {"pageProps": {"orders": []}},
            ]
        )
        with patch("tg_bot_exfa.monitor.fetch_sells", fetch):
            orders = await _fetch_recent_orders("session", max_pages=5)

        self.assertEqual([order["id"] for order in orders], ["order-1", "order-2"])
        self.assertEqual(fetch.await_count, 3)
        self.assertEqual(fetch.await_args_list[1].kwargs["page"], 2)

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import tg_bot_exfa.monitor as monitor
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


def _single_page(messages):
    return AsyncMock(return_value={"items": messages, "has_more_before": False, "next_cursor": None})


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


class AuthNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        monitor._last_auth_notice = None

    async def test_auth_state_is_reported_only_when_it_changes(self):
        notify = AsyncMock()
        with patch("tg_bot_exfa.monitor.send_auth_notification", notify):
            for _ in range(3):
                await monitor._notify_auth_state(False)
            await monitor._notify_auth_state(True, {"id": 7})
            await monitor._notify_auth_state(True, {"id": 7})
            await monitor._notify_auth_state(True, {"id": 8})

        self.assertEqual(
            [call.args for call in notify.await_args_list],
            [(False, None), (True, {"id": 7}), (True, {"id": 8})],
        )

    async def test_failed_notice_is_retried_next_time(self):
        notify = AsyncMock(side_effect=[RuntimeError("telegram down"), None])
        with patch("tg_bot_exfa.monitor.send_auth_notification", notify):
            with self.assertLogs("exfador.monitor", level="WARNING"):
                await monitor._notify_auth_state(False)
            await monitor._notify_auth_state(False)

        self.assertEqual(notify.await_count, 2)


class RemoteAnnouncementTests(unittest.TestCase):
    def test_announcements_require_explicit_opt_in(self):
        with patch("tg_bot_exfa.monitor.load_config", return_value={}):
            self.assertFalse(monitor._remote_announcements_enabled())
        with patch("tg_bot_exfa.monitor.load_config", return_value={"REMOTE_ANNOUNCEMENTS_ENABLED": True}):
            self.assertTrue(monitor._remote_announcements_enabled())


class BumpTests(unittest.IsolatedAsyncioTestCase):
    async def test_targets_resolve_missing_ids_from_offer_pages(self):
        lots = [
            {"id": 1, "game_id": 2, "category_id": 3, "category_url": "https://starvell.com/roblox/robux"},
            {"id": "uuid-2", "title": "needs detail"},
        ]
        detail = {"pageProps": {"offer": {"id": 99, "publicId": "uuid-2", "gameId": 5, "categoryId": 6}}}
        with patch("tg_bot_exfa.monitor.fetch_offer_detail", AsyncMock(return_value=detail)) as fetch:
            enriched, targets, category_url = await monitor._bump_targets("session", "sid", lots, None)

        fetch.assert_awaited_once()
        self.assertEqual(targets, {2: {3}, 5: {6}})
        self.assertEqual(enriched[1]["game_id"], 5)
        self.assertEqual(enriched[1]["category_id"], 6)
        self.assertEqual(category_url, "https://starvell.com/roblox/robux")

    async def test_only_lot_categories_are_bumped(self):
        lots = [
            {"id": 1, "game_id": 2, "category_id": 3, "offer_type": "LOT"},
            {"id": 2, "game_id": 2, "category_id": 9, "offer_type": "GAME_CURRENCY"},
            {"id": 3, "game_id": 4, "category_id": 5},
        ]

        _, targets, _ = await monitor._bump_targets("session", "sid", lots, None)

        self.assertEqual(targets, {2: {3}, 4: {5}})

    async def test_bump_cycle_sends_one_summary_for_successful_categories(self):
        lots = {
            "lots": [
                {"id": 1, "game_id": 2, "category_id": 3, "title": "A"},
                {"id": 2, "game_id": 2, "category_id": 3, "title": "B"},
                {"id": 3, "game_id": 4, "category_id": 5, "title": "C"},
            ],
            "my_games": "2,4",
        }

        async def bump(session, sid, game_id, categories, referer, my_games_cookie=None):
            success = game_id == 2
            return {"request": {"gameId": game_id, "categoryIds": categories}, "response": {"success": success}}

        summary = AsyncMock(return_value=1)
        with (
            patch(
                "tg_bot_exfa.monitor.fetch_homepage_data",
                AsyncMock(return_value={"authorized": True, "user": {"id": 7, "username": "seller"}, "sid": "sid"}),
            ),
            patch("tg_bot_exfa.monitor.find_user_lots", AsyncMock(return_value=lots)),
            patch("tg_bot_exfa.monitor.bump_categories", side_effect=bump),
            patch("tg_bot_exfa.monitor.send_bump_summary", summary),
        ):
            my_games, authorized, retry_after = await monitor._bump_once({"SESSION_COOKIE": "session"}, None)

        self.assertTrue(authorized)
        self.assertEqual(my_games, "2,4")
        summary.assert_awaited_once()
        self.assertEqual([lot["title"] for lot in summary.await_args.args[0]], ["A", "B"])

    async def test_unauthorized_session_skips_bump(self):
        with (
            patch("tg_bot_exfa.monitor.fetch_homepage_data", AsyncMock(return_value={"authorized": False})),
            patch("tg_bot_exfa.monitor.find_user_lots", AsyncMock()) as lots,
        ):
            _, authorized, _ = await monitor._bump_once({"SESSION_COOKIE": "expired"}, None)

        self.assertFalse(authorized)
        lots.assert_not_awaited()


class RealtimePollingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        monitor.realtime.signals.reset()

    async def test_event_ends_the_wait_early(self):
        trigger = asyncio.Event()
        trigger.set()
        started = asyncio.get_running_loop().time()

        await monitor._wait_for_poll(trigger, 60, 60)

        self.assertLess(asyncio.get_running_loop().time() - started, 5)
        self.assertFalse(trigger.is_set())

    async def test_poll_relaxes_only_after_events_are_confirmed(self):
        timeouts = []

        async def fake_wait_for(awaitable, timeout):
            awaitable.close()
            timeouts.append(timeout)
            raise asyncio.TimeoutError

        with patch("tg_bot_exfa.monitor.asyncio.wait_for", fake_wait_for):
            await monitor._wait_for_poll(asyncio.Event(), 5, 30)
            monitor.realtime.signals.connected = True
            await monitor._wait_for_poll(asyncio.Event(), 5, 30)
            monitor.realtime.signals.confirmed = True
            await monitor._wait_for_poll(asyncio.Event(), 5, 30)

        self.assertEqual(timeouts, [5, 5, 30])

    def test_relaxed_interval_is_configurable_and_bounded(self):
        self.assertEqual(monitor._realtime_poll_interval({}), 30)
        self.assertEqual(monitor._realtime_poll_interval({"REALTIME_POLL_INTERVAL": 120}), 120)
        self.assertEqual(monitor._realtime_poll_interval({"REALTIME_POLL_INTERVAL": 1}), 5)


class BumpCooldownTests(unittest.TestCase):
    def test_cooldown_seconds_are_read_from_error_body(self):
        raw = '{"message": "too early", "data": {"code": "OFFERS_BUMP_COOLDOWN", "retryAfterSeconds": 754}}'

        self.assertEqual(monitor._bump_retry_after({"success": False, "raw": raw}), 754.0)
        self.assertIsNone(monitor._bump_retry_after({"success": False, "raw": '{"data": {"code": "OTHER"}}'}))
        self.assertIsNone(monitor._bump_retry_after({"success": False, "raw": "<html>"}))

    def test_next_attempt_follows_cooldown_within_bounds(self):
        self.assertEqual(monitor._next_bump_delay(1800, True, None), 1800)
        self.assertEqual(monitor._next_bump_delay(1800, True, 600), 605)
        self.assertEqual(monitor._next_bump_delay(1800, True, 7200), 1800)
        self.assertEqual(monitor._next_bump_delay(1800, True, 0), 60)
        self.assertEqual(monitor._next_bump_delay(1800, False, 600), 60)


class MessageScanTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_pages_back_until_the_stored_cursor(self):
        pages = AsyncMock(
            side_effect=[
                {"items": [{"id": f"m-{i}"} for i in range(60, 10, -1)], "has_more_before": True, "next_cursor": "m-11"},
                {"items": [{"id": f"m-{i}"} for i in range(10, 0, -1)], "has_more_before": True, "next_cursor": "m-1"},
            ]
        )
        with patch("tg_bot_exfa.monitor.fetch_chat_messages_page", pages):
            messages = await monitor._scan_new_messages("session", "chat-1", 42, "m-5", 0)

        self.assertEqual(pages.await_count, 2)
        self.assertEqual(pages.await_args_list[1].kwargs["before_id"], "m-11")
        self.assertIn({"id": "m-5"}, messages)

    async def test_scan_stops_at_the_page_limit(self):
        page = {"items": [{"id": "x"}], "has_more_before": True, "next_cursor": "x"}
        with patch("tg_bot_exfa.monitor.fetch_chat_messages_page", AsyncMock(return_value=page)) as pages:
            await monitor._scan_new_messages("session", "chat-1", None, "never-found", 0)

        self.assertEqual(pages.await_count, monitor.CHAT_SCAN_MAX_PAGES)


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
            patch("tg_bot_exfa.monitor.fetch_chat_messages_page", _single_page(messages)),
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
        fetch_messages = _single_page(
            [
                {"id": "message-2", "authorId": 99, "content": "seller reply", "metadata": {}},
                {"id": "message-0", "authorId": 42, "content": "old", "metadata": {}},
            ]
        )
        notify = AsyncMock()
        with (
            patch("tg_bot_exfa.monitor.fetch_chats", AsyncMock(return_value=chat_page)),
            patch("tg_bot_exfa.monitor.fetch_chat_messages_page", fetch_messages),
            patch("tg_bot_exfa.monitor.send_chat_notification", notify),
            patch("tg_bot_exfa.monitor.load_config", return_value={"WELCOME_ENABLED": False}),
        ):
            await _check_chats("session", database, user_id=99)

        notify.assert_not_awaited()
        database.set_last_notified_message.assert_awaited_once_with("chat-1", "message-2")
        self.assertEqual(fetch_messages.await_args.kwargs["limit"], 50)

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
            patch("tg_bot_exfa.monitor.fetch_chat_messages_page", _single_page(messages)),
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
            patch("tg_bot_exfa.monitor.fetch_chat_messages_page", fetch_messages),
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
            patch("tg_bot_exfa.monitor.fetch_chat_messages_page", _single_page(messages)),
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
            patch("tg_bot_exfa.monitor.fetch_chat_messages_page", _single_page(messages)),
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
            patch("tg_bot_exfa.monitor.fetch_chat_messages_page", _single_page(messages)),
            patch("tg_bot_exfa.monitor.send_chat_notification", notify),
            patch("tg_bot_exfa.monitor.load_config", return_value={"WELCOME_ENABLED": False}),
        ):
            await _check_chats("session", database, user_id=99)

        self.assertEqual(notify.await_count, 2)
        self.assertEqual(
            database.set_last_notified_message.await_args_list[-1].args,
            ("chat-1", "message-2"),
        )

    async def test_auto_read_marks_chat_after_successful_notification(self):
        database = FakeChatDatabase()
        chat_page = {
            "pageProps": {
                "user": {"id": 99},
                "chats": [
                    {
                        "id": "chat-1",
                        "unreadMessageCount": 1,
                        "participants": [{"id": 99}, {"id": 42, "username": "buyer"}],
                        "lastMessage": {"id": "message-1", "authorId": 42, "content": "hi", "metadata": {}},
                    }
                ],
            }
        }
        messages = [
            {"id": "message-1", "authorId": 42, "content": "hi", "metadata": {}},
            {"id": "message-0", "authorId": 42, "content": "old", "metadata": {}},
        ]
        for auto_read, notify_result, expected_calls in ((True, 1, 1), (False, 1, 0), (True, RuntimeError("tg"), 0)):
            mark_read = AsyncMock()
            notify = AsyncMock(side_effect=[notify_result]) if isinstance(notify_result, Exception) else AsyncMock(return_value=notify_result)
            with (
                patch("tg_bot_exfa.monitor.fetch_chats", AsyncMock(return_value=chat_page)),
                patch("tg_bot_exfa.monitor.fetch_chat_messages_page", _single_page(messages)),
                patch("tg_bot_exfa.monitor.send_chat_notification", notify),
                patch("tg_bot_exfa.monitor.mark_chat_read", mark_read),
                patch(
                    "tg_bot_exfa.monitor.load_config",
                    return_value={"WELCOME_ENABLED": False, "AUTO_READ_CHATS": auto_read},
                ),
            ):
                if isinstance(notify_result, Exception):
                    with self.assertLogs("exfador.monitor", level="WARNING"):
                        await _check_chats("session", database, user_id=99)
                else:
                    await _check_chats("session", database, user_id=99)
            self.assertEqual(mark_read.await_count, expected_calls, (auto_read, notify_result))

    async def test_fresh_cursor_notifies_only_unread_messages_not_history(self):
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
                        "unreadMessageCount": 1,
                        "participants": [{"id": 99}, {"id": 42, "username": "buyer"}],
                        "lastMessage": {"id": "message-3", "authorId": 42, "content": "new", "metadata": {}},
                    }
                ],
            }
        }
        history = [
            {"id": "message-3", "authorId": 42, "content": "new", "metadata": {}},
            {"id": "message-2", "authorId": 42, "content": "last week", "metadata": {}},
            {"id": "message-1", "authorId": 42, "content": "last month", "metadata": {}},
        ]
        notify = AsyncMock(return_value=1)
        with (
            patch("tg_bot_exfa.monitor.fetch_chats", AsyncMock(return_value=chat_page)),
            patch("tg_bot_exfa.monitor.fetch_chat_messages_page", _single_page(history)),
            patch("tg_bot_exfa.monitor.send_chat_notification", notify),
            patch("tg_bot_exfa.monitor.load_config", return_value={"WELCOME_ENABLED": False}),
        ):
            await _check_chats("session", database, user_id=99)

        notify.assert_awaited_once()
        self.assertEqual(notify.await_args.args[1], "new")


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

    async def test_owner_notification_retry_never_redispatches_order_to_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.sqlite3"))
            await database.init()
            order = {
                "id": "order-1",
                "status": "CREATED",
                "quantity": 1,
                "user": {"id": 42, "username": "buyer"},
                "offerDetails": {"descriptions": {"rus": {"briefDescription": "Stars"}}},
            }
            plugins = SimpleNamespace(dispatch_order_created=AsyncMock())
            owner_send = AsyncMock(side_effect=[RuntimeError("no recipients"), RuntimeError("no recipients"), 1])
            with (
                patch(
                    "tg_bot_exfa.monitor.fetch_sells",
                    AsyncMock(return_value={"pageProps": {"orders": [order]}}),
                ),
                patch("tg_bot_exfa.monitor.send_order_notification", owner_send),
                patch("tg_bot_exfa.monitor.load_config", return_value={"DEBUG": False}),
                patch("tg_bot_exfa.app.app_context", SimpleNamespace(plugin_manager=plugins)),
            ):
                for _ in range(2):
                    with self.assertLogs("exfador.monitor", level="WARNING"):
                        await _check_orders("session", database)
                await _check_orders("session", database)

            self.assertEqual(owner_send.await_count, 3)
            plugins.dispatch_order_created.assert_awaited_once()
            self.assertTrue(await database.is_order_notified("order-1"))

    async def test_insufficient_stock_warns_owner_and_delivers_after_restock(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "bot.sqlite3"))
            await database.init()
            await database.add_autodelivery_items("Robux", ["code-1"])
            order = {
                "id": "order-1",
                "status": "CREATED",
                "quantity": 2,
                "user": {"id": 42, "username": "buyer"},
                "offerDetails": {"descriptions": {"rus": {"briefDescription": "Robux"}}},
            }
            plugins = SimpleNamespace(dispatch_order_created=AsyncMock())
            owner_send = AsyncMock(return_value=1)
            buyer_send = AsyncMock(return_value={"success": True})
            with (
                patch(
                    "tg_bot_exfa.monitor.fetch_sells",
                    AsyncMock(return_value={"pageProps": {"orders": [order]}}),
                ),
                patch(
                    "tg_bot_exfa.monitor.fetch_chats",
                    AsyncMock(
                        return_value={"pageProps": {"chats": [{"id": "chat-1", "participants": [{"id": 42}]}]}}
                    ),
                ),
                patch("tg_bot_exfa.monitor.send_chat_message", buyer_send),
                patch("tg_bot_exfa.monitor.send_order_notification", owner_send),
                patch("tg_bot_exfa.monitor.load_config", return_value={"DEBUG": False, "WATERMARK_ON": False}),
                patch("tg_bot_exfa.app.app_context", SimpleNamespace(plugin_manager=plugins)),
            ):
                with self.assertLogs("exfador.monitor", level="WARNING"):
                    await _check_orders("session", database)

                owner_send.assert_awaited_once_with(
                    order,
                    event_id="order-1:autodelivery-insufficient",
                    ad_warning=("Robux", "ad_fail_insufficient", {"required": 2, "available": 1}),
                )
                buyer_send.assert_not_awaited()
                plugins.dispatch_order_created.assert_not_awaited()
                self.assertFalse(await database.is_order_notified("order-1"))

                await database.add_autodelivery_items("Robux", ["code-2"])
                await _check_orders("session", database)

            buyer_send.assert_awaited_once_with("session", "chat-1", "code-1\ncode-2")
            self.assertEqual(owner_send.await_args.args, (order, ("Robux", "code-1\ncode-2")))
            plugins.dispatch_order_created.assert_awaited_once()
            self.assertTrue(await database.is_order_notified("order-1"))

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

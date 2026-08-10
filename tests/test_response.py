import unittest

from api.response import (
    StarvellAuthenticationError,
    StarvellResponseError,
    ensure_success,
    normalize_page_collection,
    normalize_page_object,
    response_items,
)
from api.offer_details import offer_context
from api.bump import normalize_bump_response


class PageResponseTests(unittest.TestCase):
    def test_redirect_is_reported_as_authentication_error(self):
        with self.assertRaises(StarvellAuthenticationError):
            normalize_page_collection(
                {"pageProps": {"__N_REDIRECT": "/auth/login?redirect=/chat"}},
                "chats",
                "fetch chats",
            )

    def test_error_page_is_not_treated_as_an_empty_result(self):
        with self.assertRaisesRegex(StarvellResponseError, "HTTP 403"):
            normalize_page_collection(
                {"pageProps": {"error": True, "errorCode": "403"}},
                "orders",
                "fetch sells",
            )

    def test_collection_and_object_fallback_to_bff_payload(self):
        chats = normalize_page_collection(
            {"pageProps": {"bff": {"chats": [{"id": "chat-1"}]}}},
            "chats",
            "fetch chats",
        )
        offer = normalize_page_object(
            {"pageProps": {"bff": {"offer": {"id": "offer-1"}}}},
            "offer",
            "fetch offer detail",
        )

        self.assertEqual(chats["pageProps"]["chats"], [{"id": "chat-1"}])
        self.assertEqual(offer["pageProps"]["offer"], {"id": "offer-1"})

    def test_html_next_data_envelope_is_supported(self):
        chats = normalize_page_collection(
            {"props": {"pageProps": {"chats": [{"id": "chat-1"}]}}},
            "chats",
            "fetch chats",
        )

        self.assertEqual(chats["pageProps"]["chats"], [{"id": "chat-1"}])


class JsonResponseTests(unittest.TestCase):
    def test_message_items_support_current_and_fallback_envelopes(self):
        self.assertEqual(
            response_items({"messagesListResult": {"items": [{"id": "message-1"}]}}, "fetch chat messages"),
            [{"id": "message-1"}],
        )
        self.assertEqual(
            response_items({"data": {"items": [{"id": "message-2"}]}}, "fetch chat messages"),
            [{"id": "message-2"}],
        )

    def test_failed_json_response_is_not_accepted_as_success(self):
        with self.assertRaises(StarvellResponseError):
            ensure_success({"success": False, "message": "rejected"}, "send chat message")

    def test_malformed_bump_json_is_not_reported_as_success(self):
        response = normalize_bump_response(200, "application/json", "not-json")

        self.assertFalse(response["success"])
        self.assertEqual(response["status"], 200)


class OfferContextTests(unittest.TestCase):
    def test_missing_game_or_category_is_normalized_to_empty_objects(self):
        offer, game, category = offer_context({"pageProps": {"offer": {"id": "offer-1"}}})

        self.assertEqual(offer, {"id": "offer-1"})
        self.assertEqual(game, {})
        self.assertEqual(category, {})

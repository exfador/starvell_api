import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tg_bot_exfa.versioning import fetch_latest_tag, is_newer, latest_version, parse_version


class VersioningTests(unittest.TestCase):
    def test_versions_compare_numerically_and_ignore_trailing_zeros(self):
        self.assertEqual(parse_version("v1.10"), (1, 10))
        self.assertEqual(parse_version("1.3.0"), parse_version("1.3"))
        self.assertIsNone(parse_version("api"))
        self.assertTrue(is_newer("1.10", "1.9"))
        self.assertFalse(is_newer("1.3", "1.3"))
        self.assertFalse(is_newer("1.2", "1.3"))
        self.assertFalse(is_newer("api", "1.3"))

    def test_latest_release_ignores_non_version_tags_and_order(self):
        self.assertEqual(latest_version(["api", "1.2", "1.10", "0.9"]), "1.10")
        self.assertIsNone(latest_version(["api"]))

    def test_github_lookup_returns_highest_tag(self):
        response = SimpleNamespace(
            status_code=200,
            json=lambda: [{"name": "api"}, {"name": "1.3"}, {"name": "1.4"}, {"name": "1.2"}],
        )
        with patch("tg_bot_exfa.versioning.requests.get", return_value=response):
            self.assertEqual(fetch_latest_tag(), "1.4")

    def test_github_error_yields_no_tag(self):
        with patch("tg_bot_exfa.versioning.requests.get", return_value=SimpleNamespace(status_code=403)):
            self.assertIsNone(fetch_latest_tag())


if __name__ == "__main__":
    unittest.main()

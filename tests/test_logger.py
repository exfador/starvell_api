import logging
import sys
import unittest

from tg_bot_exfa.logger import SecretMaskingFilter, _ColorFormatter, mask_secrets

BOT_TOKEN = "123456789:AAH" + "x" * 32


class SecretMaskingTests(unittest.TestCase):
    def _record(self, msg, args=(), exc_info=None):
        return logging.LogRecord("exfador.test", logging.WARNING, __file__, 1, msg, args, exc_info)

    def test_bot_token_and_session_parts_are_masked(self):
        text = f"POST https://api.telegram.org/bot{BOT_TOKEN}/sendMessage session=abcdefgh123 sid=zzz"

        masked = mask_secrets(text, ["abcdefgh123"])

        self.assertNotIn(BOT_TOKEN, masked)
        self.assertNotIn("abcdefgh123", masked)
        self.assertIn("<bot-token>", masked)
        self.assertIn("sid=zzz", masked)

    def test_filter_masks_formatted_message_and_traceback(self):
        log_filter = SecretMaskingFilter(lambda: ["session-secret-value"])
        try:
            raise RuntimeError("cookie session-secret-value rejected")
        except RuntimeError:
            record = self._record("request failed: %s", ("session-secret-value",), sys.exc_info())

        self.assertTrue(log_filter.filter(record))

        self.assertEqual(record.getMessage(), "request failed: <secret>")
        self.assertNotIn("session-secret-value", record.exc_text)
        self.assertIn("<secret>", record.exc_text)

    def test_colored_console_output_does_not_leak_into_other_handlers(self):
        formatter = _ColorFormatter("%(levelname)s %(name)s %(message)s", enable_color=True)
        formatter.enable_color = True
        record = self._record("hello")

        formatter.format(record)

        self.assertEqual(record.levelname, "WARNING")
        self.assertEqual(record.name, "exfador.test")


if __name__ == "__main__":
    unittest.main()

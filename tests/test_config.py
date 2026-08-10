import json
import stat
import tempfile
import unittest
from pathlib import Path

from tg_bot_exfa.config import (
    BotConfig,
    hash_password,
    md5_hex,
    password_needs_rehash,
    save_config,
    verify_password,
)


class PasswordHashTests(unittest.TestCase):
    def test_pbkdf2_password_round_trip(self):
        encoded = hash_password("correct horse", iterations=1_000, salt="fixed-salt")

        self.assertTrue(verify_password("correct horse", encoded))
        self.assertFalse(verify_password("wrong", encoded))

    def test_legacy_md5_is_accepted_and_marked_for_migration(self):
        encoded = md5_hex("legacy")

        self.assertTrue(verify_password("legacy", encoded))
        self.assertTrue(password_needs_rehash(encoded))


class ConfigPersistenceTests(unittest.TestCase):
    def test_save_is_atomic_and_restricts_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "osnova.json"
            cfg = BotConfig("token", hash_password("password", iterations=1_000), "ru", str(path))

            save_config(cfg)

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["BOT_TOKEN"], "token")
            if hasattr(stat, "S_IMODE"):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

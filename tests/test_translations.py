import re
import unittest

from tg_bot_exfa.exf_langue.strings import Translations

PLACEHOLDER = re.compile(r"{(\w+)}")


class TranslationParityTests(unittest.TestCase):
    def test_languages_share_keys_and_placeholders(self):
        data = Translations().data
        ru, en = data["ru"], data["en"]

        self.assertEqual(set(ru), set(en))
        for key in ru:
            with self.subTest(key=key):
                self.assertEqual(set(PLACEHOLDER.findall(ru[key])), set(PLACEHOLDER.findall(en[key])))


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime

from tg_bot_exfa.template_renderer import render_template
from tg_bot_exfa.exf_langue.strings import Translations


class TemplateRendererTests(unittest.TestCase):
    def test_replaces_supported_values_and_clock_fields(self):
        rendered = render_template(
            "Привет, {buyer}! Заказ {order_id}: {product} x{quantity} — {date} {time}",
            {
                "buyer": "andrey",
                "order_id": "A-42",
                "product": "Robux",
                "quantity": 3,
            },
            now=datetime(2026, 8, 10, 14, 5, 7),
        )

        self.assertEqual(
            rendered,
            "Привет, andrey! Заказ A-42: Robux x3 — 10.08.2026 14:05",
        )

    def test_unknown_and_missing_values_remain_visible(self):
        rendered = render_template(
            "{buyer} {unknown} {order_id}",
            {"buyer": "user", "order_id": None},
        )

        self.assertEqual(rendered, "user {unknown} {order_id}")

    def test_does_not_interpret_format_expressions(self):
        rendered = render_template(
            "{buyer.__class__} {buyer[0]} {{buyer}} {buyer}",
            {"buyer": "safe"},
        )

        self.assertEqual(rendered, "{buyer.__class__} {buyer[0]} {{buyer}} safe")

    def test_accepts_legacy_cardinal_style_variables(self):
        rendered = render_template(
            "$username / $order_id / $product / $full_time",
            {
                "buyer": "legacy-user",
                "order_id": "77",
                "product": "Code",
            },
            now=datetime(2026, 8, 10, 9, 8, 7),
        )

        self.assertEqual(rendered, "legacy-user / 77 / Code / 09:08:07")

    def test_template_help_keeps_literal_placeholders(self):
        prompt = Translations().t("ru", "templates_add_prompt")

        self.assertIn("{chat_id}", prompt)
        self.assertIn("{date}", prompt)


if __name__ == "__main__":
    unittest.main()

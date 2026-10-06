import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tg_bot_exfa.commands import bot_commands, sync_bot_commands


class BotCommandTests(unittest.IsolatedAsyncioTestCase):
    def test_base_commands_always_include_logs(self):
        names = [command.command for command in bot_commands(None)]

        self.assertEqual(names, ["start", "restart", "update", "logs"])

    def test_invalid_or_duplicate_plugin_commands_are_skipped(self):
        manager = SimpleNamespace(
            commands={
                "stars": {"description": "Stars"},
                "Bad-Name": {"description": "rejected by Telegram"},
                "start": {"description": "duplicate"},
                "x" * 33: {"description": "too long"},
                "gift_stars": {},
            }
        )

        commands = {command.command: command.description for command in bot_commands(manager)}

        self.assertEqual(list(commands)[-2:], ["stars", "gift_stars"])
        self.assertEqual(commands["start"], "Запуск")
        self.assertEqual(commands["gift_stars"], "Plugin")
        self.assertEqual(len(commands), 6)

    async def test_sync_sends_one_complete_list(self):
        bot = SimpleNamespace(set_my_commands=AsyncMock())

        await sync_bot_commands(bot, SimpleNamespace(commands={"stars": {"description": "Stars"}}))

        sent = [command.command for command in bot.set_my_commands.await_args.args[0]]
        self.assertEqual(sent, ["start", "restart", "update", "logs", "stars"])


if __name__ == "__main__":
    unittest.main()

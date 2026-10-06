import re

from aiogram.types import BotCommand

BASE_COMMANDS = (
    ("start", "Запуск"),
    ("restart", "Перезапуск"),
    ("update", "Обновление"),
    ("logs", "Архив логов"),
)
TELEGRAM_COMMAND = re.compile(r"[a-z0-9_]{1,32}")
TELEGRAM_COMMANDS_LIMIT = 100


def bot_commands(plugin_manager) -> list[BotCommand]:
    commands = [BotCommand(command=name, description=description) for name, description in BASE_COMMANDS]
    seen = {name for name, _ in BASE_COMMANDS}
    for name, meta in (getattr(plugin_manager, "commands", None) or {}).items():
        command = str(name or "").strip().lower()
        if command in seen or not TELEGRAM_COMMAND.fullmatch(command):
            continue
        description = str((meta or {}).get("description") or "").strip()[:256]
        commands.append(BotCommand(command=command, description=description or "Plugin"))
        seen.add(command)
    return commands[:TELEGRAM_COMMANDS_LIMIT]


async def sync_bot_commands(bot, plugin_manager) -> None:
    await bot.set_my_commands(bot_commands(plugin_manager))

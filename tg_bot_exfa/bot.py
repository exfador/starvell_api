import asyncio
import os
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

import tg_bot_exfa.app as app
from tg_bot_exfa.config import load_config, save_config, md5_hex
from tg_bot_exfa.storage.db import Database
from tg_bot_exfa.handlers.start import router as start_router
from tg_bot_exfa.handlers.callbacks import router as callbacks_router
from tg_bot_exfa.monitor import start_monitor, load_config as load_osnova_config
from api.auth import fetch_homepage_data
from tg_bot_exfa.logger import setup_logging
from tg_bot_exfa.handlers.logs import router as logs_router


async def run_bot() -> None:
    cfg = load_config()
    setup_logging(logging.DEBUG if cfg.debug else logging.INFO)
    log = logging.getLogger("exfador.bot")
    if not cfg.token or not cfg.password_md5:
        print("Bot configuration is incomplete. Setup is required.")
        token = cfg.token or input("Enter BOT_TOKEN: ").strip()
        if not cfg.password_md5:
            plain = input("Enter bot password (will be stored as MD5): ").strip()
            password_md5 = md5_hex(plain)
        else:
            password_md5 = cfg.password_md5
        lang = (cfg.default_language or "ru").strip() or "ru"
        cfg.token = token
        cfg.password_md5 = password_md5
        cfg.default_language = lang
        save_config(cfg)
        try:
            from pathlib import Path
            import json
            session_cookie = input("Enter SESSION_COOKIE (optional, press Enter to skip): ").strip()
            if session_cookie:
                cfg_path = Path(cfg.path)
                obj = {}
                if cfg_path.exists():
                    try:
                        obj = json.loads(cfg_path.read_text(encoding="utf-8") or "{}")
                    except Exception:
                        obj = {}
                obj["SESSION_COOKIE"] = session_cookie
                cfg_path.write_text(json.dumps(obj, ensure_ascii=False, indent=4), encoding="utf-8")
        except Exception:
            pass
    db_path = os.path.join(os.path.dirname(__file__), "bot.sqlite3")
    db = Database(db_path)
    await db.init()
    app.app_context = app.AppContext(cfg, db)
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Запуск"),
            BotCommand(command="restart", description="Перезапуск"),
        ])
    except Exception:
        pass
    try:
        short_ru = "⚙️ Автобамп • 🛒 Заказы/возвраты • 📩 SMS • 🧩 Плагины\n👨‍💻 Dev: t.me/exfador\n📢 @starvellapi \n💬 @community_starvell"
        await bot.set_my_short_description(short_description=short_ru, language_code="ru")
    except Exception:
        pass
    dp.include_router(start_router)
    dp.include_router(callbacks_router)
    dp.include_router(logs_router)
    log.info("Routers loaded. Starting monitor task…")
    mt = asyncio.create_task(start_monitor())
    app.app_context.monitor_task = mt
    log.info("Polling started")
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()



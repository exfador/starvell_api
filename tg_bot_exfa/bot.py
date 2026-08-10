import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.client.default import DefaultBotProperties

import tg_bot_exfa.app as app
from tg_bot_exfa.config import hash_password, load_config, save_config, update_config_values
from tg_bot_exfa.storage.db import Database
from tg_bot_exfa.handlers.start import router as start_router
from tg_bot_exfa.handlers.callbacks import router as callbacks_router
from tg_bot_exfa.handlers.plugins import router as plugins_router
from tg_bot_exfa.handlers.plugin_cmds import router as plugin_cmds_router
from tg_bot_exfa.monitor import start_monitor, load_config as load_osnova_config
from api.auth import fetch_homepage_data
from api.http_client import close_http_session
from tg_bot_exfa.logger import setup_logging
from tg_bot_exfa.handlers.logs import router as logs_router
from tg_bot_exfa.plugins import PluginManager, PluginContext
from tg_bot_exfa.paths import DATABASE_PATH, PLUGINS_PATH, PLUGIN_STATE_PATH
from tg_bot_exfa.storage.fsm import SQLiteStorage


async def run_bot() -> None:
    cfg = load_config()
    setup_logging(logging.DEBUG if cfg.debug else logging.INFO)
    log = logging.getLogger("exfador.bot")
    if not cfg.token or not cfg.password_md5:
        print("Bot configuration is incomplete. Setup is required.")
        token = cfg.token or input("Enter BOT_TOKEN: ").strip()
        if not cfg.password_md5:
            plain = input("Enter bot password: ").strip()
            password_md5 = hash_password(plain)
        else:
            password_md5 = cfg.password_md5
        lang = (cfg.default_language or "ru").strip() or "ru"
        cfg.token = token
        cfg.password_md5 = password_md5
        cfg.default_language = lang
        save_config(cfg)
        try:
            session_cookie = input("Enter SESSION_COOKIE (optional, press Enter to skip): ").strip()
            if session_cookie:
                update_config_values(cfg.path, SESSION_COOKIE=session_cookie)
        except Exception:
            pass
    db = Database(str(DATABASE_PATH))
    await db.init()
    try:
        pruned = await db.prune_runtime_state(retention_days=180)
        if any(pruned.values()):
            logging.getLogger("exfador.bot").info("Pruned runtime history: %s", pruned)
    except Exception as exc:
        logging.getLogger("exfador.bot").warning("Runtime history cleanup failed: %s", exc)
    app.app_context = app.AppContext(cfg, db)
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    app.app_context.bot = bot
    fsm_storage = SQLiteStorage(DATABASE_PATH)
    await fsm_storage.init()
    dp = Dispatcher(storage=fsm_storage)
    try:
        PLUGINS_PATH.mkdir(parents=True, exist_ok=True)
        PLUGIN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    pm = PluginManager(root_dir=str(PLUGINS_PATH), state_path=str(PLUGIN_STATE_PATH))
    pm.load_all()
    app.app_context.plugin_manager = pm
    try:
        loaded = [x for x in pm.plugins.values() if x.module is not None]
        broken = [x for x in pm.plugins.values() if x.module is None]
        names = ", ".join([f"{x.name}({x.version})" for x in loaded]) or "-"
        if broken:
            names_broken = ", ".join([f"{x.name}" for x in broken])
            logging.getLogger("exfador.bot").info("Plugins loaded: %s | Broken: %s", names, names_broken)
        else:
            logging.getLogger("exfador.bot").info("Plugins loaded: %s", names)
    except Exception:
        pass
    try:
        base_cmds = [
            BotCommand(command="start", description="Запуск"),
            BotCommand(command="restart", description="Перезапуск"),
            BotCommand(command="update", description="Обновление"),
            BotCommand(command="logs", description="Архив логов"),
        ]
        plugin_cmds: list[BotCommand] = []
        seen = {c.command for c in base_cmds}
        for name, meta in pm.commands.items():
            cmd = str(name or "").strip().lower()
            if not cmd or cmd in seen:
                continue
            desc = str(meta.get("description") or "").strip()[:256]
            plugin_cmds.append(BotCommand(command=cmd, description=desc or "Plugin"))
            seen.add(cmd)
        await bot.set_my_commands(base_cmds + plugin_cmds)
    except Exception:
        pass
    try:
        full_text = (

            "👨‍💻 Dev: t.me/exfador\n📢 @starvellapi  |  💬 @community_starvell"
        )
        short_text = full_text.replace("\n", " ").strip()
        if len(short_text) > 120:
            short_text = short_text[:120]
        await bot.set_my_short_description(short_description=short_text)
        try:
            await bot.set_my_description(description=full_text)
        except Exception as e:
            log.warning("Failed to set long description: %s", e)
        try:
            await bot.get_my_short_description()
        except Exception as e:
            log.warning("Unable to read back short description: %s", e)
    except Exception as e:
        log.exception("Failed to set short description: %s", e)
    try:
        osnova_cfg = load_osnova_config()
        session_cookie = (osnova_cfg or {}).get("SESSION_COOKIE", "")
        profile_name = "NULL"
        try:
            auth = await fetch_homepage_data(session_cookie)
            if auth.get("authorized") and auth.get("user"):
                user = auth.get("user") or {}
                profile_name = str(user.get("username") or user.get("login") or user.get("id") or "NULL")
        except Exception as e:
            log.warning("Auth check failed while setting bot name: %s", e)
        new_name = f"COXERHUB STARVELL | {profile_name}"
        if len(new_name) > 64:
            new_name = new_name[:64]
        await bot.set_my_name(name=new_name)
        try:
            name_info = await bot.get_my_name()
            log.info("Bot name set to: %r", getattr(name_info, "name", None))
        except Exception as e:
            log.warning("Unable to read back bot name: %s", e)
    except Exception as e:
        log.warning("Failed to set bot name: %s", e)
    dp.include_router(start_router)
    dp.include_router(callbacks_router)
    dp.include_router(plugins_router)
    dp.include_router(plugin_cmds_router)
    dp.include_router(logs_router)
    log.info("Routers loaded. Starting monitor task…")
    try:
        osnova_cfg = load_osnova_config()
    except Exception:
        osnova_cfg = {}
    try:
        session_cookie_init = (osnova_cfg or {}).get("SESSION_COOKIE", "")
        ctx_init = PluginContext(session_cookie=session_cookie_init, db=db, config=osnova_cfg or {})
        await pm.dispatch_init(ctx_init)
        try:
            base_cmds = [
                BotCommand(command="start", description="Запуск"),
                BotCommand(command="restart", description="Перезапуск"),
                BotCommand(command="update", description="Обновление"),
                BotCommand(command="logs", description="Архив логов"),
            ]
            plugin_cmds: list[BotCommand] = []
            seen = {c.command for c in base_cmds}
            for name, meta in pm.commands.items():
                cmd = str(name or "").strip().lower()
                if not cmd or cmd in seen:
                    continue
                desc = str(meta.get("description") or "").strip()[:256]
                plugin_cmds.append(BotCommand(command=cmd, description=desc or "Plugin"))
                seen.add(cmd)
            await bot.set_my_commands(base_cmds + plugin_cmds)
        except Exception:
            pass
    except Exception:
        pass
    mt = asyncio.create_task(start_monitor())
    app.app_context.monitor_task = mt
    log.info("Polling started")
    try:
        await dp.start_polling(bot)
    finally:
        if not mt.done():
            mt.cancel()
        await asyncio.gather(mt, return_exceptions=True)
        await close_http_session()
        await bot.session.close()


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()

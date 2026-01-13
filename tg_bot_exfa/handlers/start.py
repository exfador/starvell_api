import time
from aiogram import Router, F
import logging
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import hashlib

import tg_bot_exfa.app as app
from tg_bot_exfa.exf_langue.strings import Translations
from tg_bot_exfa.keyboards.menus import Keyboards
from tg_bot_exfa.states.auth import StartFlow
from tg_bot_exfa.config import save_config
from tg_bot_exfa.notify import send_security_auth_success, send_security_auth_blocked


router = Router()
tr = Translations()
kb = Keyboards()
log = logging.getLogger("exfador.handlers")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(message.from_user.id)
    now = int(time.time())
    lang = user.get("language") or cfg.default_language
    if user.get("blocked_until", 0) > now:
        m = await message.answer(tr.t(lang, "start_blocked"))
        await state.update_data(last_message_id=m.message_id)
        log.debug(f"/start blocked user_id={message.from_user.id}")
        return
    if user.get("authorized"):
        if not user.get("language"):
            await state.set_state(StartFlow.choosing_language)
            m = await message.answer(tr.t(lang, "choose_language"), reply_markup=kb.language_with_back(lambda k: tr.t(lang, k)).as_markup())
            await state.update_data(last_message_id=m.message_id)
        else:
            await state.clear()
            await message.answer(tr.t(lang, "main_menu"), reply_markup=kb.main_menu(lambda k: tr.t(lang, k)).as_markup())
        log.debug(f"/start authorized user_id={message.from_user.id}")
        return
    await state.set_state(StartFlow.waiting_password)
    m = await message.answer(tr.t(lang, "enter_password"))
    await state.update_data(last_message_id=m.message_id)
    log.debug(f"/start ask_password user_id={message.from_user.id}")




@router.message(Command("restart"))
async def cmd_restart(message: Message):
    import os
    import sys
    import asyncio
    from pathlib import Path

    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(message.from_user.id)
    if not user.get("authorized"):
        return
    lang = user.get("language") or cfg.default_language
    m = await message.answer(tr.t(lang, "restart_start"))

    async def do_exec_restart():
        await asyncio.sleep(1)
        root = Path(__file__).resolve().parents[2]  
        run_path = str(root / "run_bot.py")
        os.execv(sys.executable, [sys.executable, run_path])

    asyncio.create_task(do_exec_restart())
    try:
        await m.edit_text(tr.t(lang, "restart_done"))
    except Exception:
        pass


@router.message(StartFlow.waiting_password, F.text)
async def on_password(message: Message, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(message.from_user.id)
    lang = user.get("language") or cfg.default_language
    data = await state.get_data()
    last_message_id = data.get("last_message_id")
    if not last_message_id:
        m = await message.answer(tr.t(lang, "enter_password"))
        await state.update_data(last_message_id=m.message_id)
        return
    provided_md5 = hashlib.md5(message.text.strip().encode("utf-8")).hexdigest()
    if provided_md5.lower() == (cfg.password_md5 or "").lower():
        await db.reset_failed(message.from_user.id)
        await db.set_authorized(message.from_user.id, True)
        try:
            await send_security_auth_success(message.from_user.id, message.from_user.username)
        except Exception:
            pass
        await state.set_state(StartFlow.choosing_language)
        await message.bot.edit_message_text(
            tr.t(lang, "choose_language"),
            chat_id=message.chat.id,
            message_id=last_message_id,
            reply_markup=kb.language_with_back(lambda k: tr.t(lang, k)).as_markup(),
        )
        log.info(f"password_ok user_id={message.from_user.id}")
        return
    attempts = await db.increment_failed(message.from_user.id)
    left = max(0, 5 - attempts)
    if attempts >= 5:
        await db.set_blocked_until(message.from_user.id, int(time.time()) + 24 * 3600)
        await message.bot.edit_message_text(
            tr.t(lang, "blocked_24h"),
            chat_id=message.chat.id,
            message_id=last_message_id,
        )
        try:
            await send_security_auth_blocked(message.from_user.id, message.from_user.username)
        except Exception:
            pass
        await state.clear()
        log.warning(f"blocked_24h user_id={message.from_user.id}")
        return
    await message.bot.edit_message_text(
        f"{tr.t(lang, 'wrong_password', left=left)}\n{tr.t(lang, 'enter_password')}",
        chat_id=message.chat.id,
        message_id=last_message_id,
    )
    log.debug(f"password_wrong user_id={message.from_user.id} left={left}")


@router.message(StartFlow.changing_password, F.text)
async def on_change_password(message: Message, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(message.from_user.id)
    lang = user.get("language") or cfg.default_language
    data = await state.get_data()
    last_message_id = data.get("last_message_id") or message.message_id
    new_md5 = hashlib.md5(message.text.strip().encode("utf-8")).hexdigest()
    cfg.password_md5 = new_md5
    save_config(cfg)
    await state.clear()
    await message.bot.edit_message_text(
        tr.t(lang, "password_changed"),
        chat_id=message.chat.id,
        message_id=last_message_id,
        reply_markup=kb.settings_menu(lambda k: tr.t(lang, k)).as_markup(),
    )
    log.info(f"password_changed user_id={message.from_user.id}")


@router.message(Command("update"))
async def cmd_update(message: Message):
    import requests
    from version import VERSION
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(message.from_user.id)
    if not user.get("authorized"):
        return
    lang = user.get("language") or cfg.default_language
    latest = None
    try:
        r = requests.get(
            "https://api.github.com/repos/exfador/starvell_api/tags?page=1",
            headers={"accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            timeout=10,
        )
        if r.status_code == 200:
            for it in (r.json() or []):
                name = str((it or {}).get("name") or "").strip()
                if name and name.lower() != "api":
                    latest = name
                    break
    except Exception:
        latest = None
    lines = [tr.t(lang, "update_title"), tr.t(lang, "update_current", current=VERSION)]
    markup = None
    if latest and latest != VERSION:
        lines.append(tr.t(lang, "update_available", latest=latest))
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr.t(lang, "btn_update"), callback_data=f"update:install:{latest}")]])
    else:
        lines.append(tr.t(lang, "update_none"))
    await message.answer("\n".join(lines), reply_markup=markup)


@router.message(Command("logs"))
async def cmd_logs(message: Message):
    import os
    import shutil
    import tempfile
    from pathlib import Path
    from aiogram.types import FSInputFile

    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(message.from_user.id)
    if not user.get("authorized"):
        return

    root = Path(__file__).resolve().parents[2]
    logs_dir = root / "logs"

    if not logs_dir.exists():
        await message.answer("📂 Папка логов не найдена")
        return

    has_files = False
    for _ in logs_dir.rglob("*"):
        has_files = True
        break
    if not has_files:
        await message.answer("📂 Папка логов пуста")
        return

    status_msg = await message.answer("📦 Собираю архив логов…")

    try:
        tmp_dir = Path(tempfile.gettempdir())
        base_name = tmp_dir / f"logs_{int(time.time())}"
        archive_path = shutil.make_archive(str(base_name), "zip", root_dir=str(logs_dir))

        doc = FSInputFile(archive_path, filename=os.path.basename(archive_path))
        await message.answer_document(doc, caption="📦 Архив логов")
    except Exception as e:
        try:
            await status_msg.edit_text(f"❌ Не удалось создать архив логов: {e}")
        except Exception:
            pass
        return
    finally:
        try:
            if "archive_path" in locals() and os.path.exists(archive_path):
                os.remove(archive_path)
        except Exception:
            pass

    try:
        await status_msg.delete()
    except Exception:
        pass



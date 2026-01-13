from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

import tg_bot_exfa.app as app
from tg_bot_exfa.monitor import load_config as load_osnova_config
from tg_bot_exfa.plugins import PluginContext


router = Router()


@router.message(F.text.startswith("/"))
async def handle_plugin_command(message: Message):
    pm = app.app_context.plugin_manager if app.app_context else None
    if not pm:
        return
    db = app.app_context.db if app.app_context else None
    if not db:
        return
    user = await db.get_user(message.from_user.id)
    if not user.get("authorized"):
        return
    raw = (message.text or "").strip()
    if not raw.startswith("/"):
        return
    parts = raw.split()
    cmd = parts[0][1:].lower()
    args = parts[1:]
    if cmd not in pm.commands:
        return
    cfg = {}
    try:
        cfg = load_osnova_config() or {}
    except Exception:
        cfg = {}
    ctx = PluginContext(session_cookie=(cfg or {}).get("SESSION_COOKIE", ""), db=db, config=cfg or {})
    await pm.dispatch_command(cmd, message, args, ctx)


@router.callback_query(F.data.startswith("stars:"))
async def handle_stars_callback(callback: CallbackQuery, state: FSMContext):
    pm = app.app_context.plugin_manager if app.app_context else None
    if not pm:
        return
    db = app.app_context.db if app.app_context else None
    if not db:
        return
    user = await db.get_user(callback.from_user.id)
    if not user.get("authorized"):
        return
    cfg = {}
    try:
        cfg = load_osnova_config() or {}
    except Exception:
        cfg = {}
    ctx = PluginContext(session_cookie=(cfg or {}).get("SESSION_COOKIE", ""), db=db, config=cfg or {})
    await pm.dispatch_callback(callback, state, ctx)


@router.message(F.text)
async def handle_plugin_message(message: Message, state: FSMContext):
    pm = app.app_context.plugin_manager if app.app_context else None
    if not pm:
        return
    db = app.app_context.db if app.app_context else None
    if not db:
        return
    current_state = await state.get_state()
    if not current_state:
        return
    state_name = str(current_state)
    if not (state_name.startswith("StarsState") or state_name.startswith("GiftStarsState")):
        return
    user = await db.get_user(message.from_user.id)
    if not user.get("authorized"):
        return
    cfg = {}
    try:
        cfg = load_osnova_config() or {}
    except Exception:
        cfg = {}
    ctx = PluginContext(session_cookie=(cfg or {}).get("SESSION_COOKIE", ""), db=db, config=cfg or {})
    await pm.dispatch_message(message, state, ctx)



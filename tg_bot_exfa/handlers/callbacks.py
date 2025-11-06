import html
import logging
import math

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

import tg_bot_exfa.app as app
from api.orders import refund_order, fetch_sells
from api.send_message import send_chat_message
from tg_bot_exfa.exf_langue.strings import Translations
from tg_bot_exfa.keyboards.menus import Keyboards
from tg_bot_exfa.states.auth import StartFlow
from tg_bot_exfa.states.chat import ChatReply
from tg_bot_exfa.states.templates import TemplatesFlow
from tg_bot_exfa.states.orders import OrderRefund
from tg_bot_exfa.monitor import load_config as load_osnova_config


router = Router()
tr = Translations()
kb = Keyboards()
log = logging.getLogger("exfador.handlers")

TEMPLATES_PAGE_SIZE = 5
TEMPLATE_LIST_PREVIEW = 120
TEMPLATE_BUTTON_PREVIEW = 40


def _preview_text(text: str | None, limit: int) -> str:
    raw = (text or "").strip()
    if not raw:
        return "(empty)"
    raw = " ".join(raw.split())
    if len(raw) > limit:
        raw = raw[: max(0, limit - 3)] + "..."
    return raw


def _templates_menu_text(lang: str) -> str:
    return f"{tr.t(lang, 'templates_title')}\n{tr.t(lang, 'templates_intro')}"


def _format_template_lines(templates: list[dict], start_index: int, preview_limit: int) -> list[str]:
    lines: list[str] = []
    for idx, tpl in enumerate(templates, start=start_index):
        preview = html.escape(_preview_text(tpl.get("content"), preview_limit))
        lines.append(f"{idx}. <code>{preview}</code>")
    return lines


def _template_button_label(tpl: dict) -> str:
    tpl_id = tpl.get("id")
    tpl_id_str = str(tpl_id)
    preview = _preview_text(tpl.get("content"), TEMPLATE_BUTTON_PREVIEW)
    return f"{tpl_id_str} · {preview}"


async def _send_reply_from_state(
    bot,
    state: FSMContext,
    lang: str,
    content: str,
    default_chat_id: int,
    default_message_id: int | None,
    user_id: int,
):
    data = await state.get_data()
    chat_id = data.get("reply_chat_id")
    if not chat_id:
        await state.clear()
        return False, "context_missing", None
    try:
        session_cfg = load_osnova_config()
    except Exception as exc:
        return False, str(exc), chat_id
    session_cookie = session_cfg.get("SESSION_COOKIE", "")
    if not session_cookie:
        return False, "SESSION_COOKIE missing", chat_id
    try:
        await send_chat_message(session_cookie, chat_id, content)
    except Exception as exc:
        return False, str(exc), chat_id
    notification_chat_id = data.get("notification_chat_id") or default_chat_id
    notification_message_id = data.get("notification_message_id") or default_message_id
    original_text = data.get("original_text") or ""
    original_lang = data.get("original_lang") or lang
    try:
        await bot.edit_message_text(
            original_text,
            chat_id=notification_chat_id,
            message_id=notification_message_id,
            reply_markup=kb.chat_notification(
                lambda k: tr.t(original_lang, k),
                chat_id,
                f"https://starvell.com/chat/{chat_id}",
            ).as_markup(),
        )
    except Exception as exc:
        log.warning(
            "chat_reply_restore_failed user_id=%s chat_id=%s error=%s",
            user_id,
            chat_id,
            exc,
        )
    await state.clear()
    return True, None, chat_id


async def _lang_of(user, cfg):
    return user.get("language") or cfg.default_language


async def _show_templates_list(callback: CallbackQuery, lang: str, page_index: int) -> tuple[int, int]:
    db = app.app_context.db
    total = await db.count_templates()
    total_pages = 1 if total == 0 else max(1, math.ceil(total / TEMPLATES_PAGE_SIZE))
    if total_pages <= 0:
        total_pages = 1
    page_index = max(0, min(page_index, total_pages - 1))
    templates = await db.list_templates(offset=page_index * TEMPLATES_PAGE_SIZE, limit=TEMPLATES_PAGE_SIZE)
    lines = [tr.t(lang, "templates_title")]
    if not templates:
        lines.append(tr.t(lang, "templates_empty"))
    else:
        lines.append(tr.t(lang, "templates_list_total", count=total))
        start_index = page_index * TEMPLATES_PAGE_SIZE + 1
        lines.extend(_format_template_lines(templates, start_index, TEMPLATE_LIST_PREVIEW))
        if total_pages > 1:
            lines.append(tr.t(lang, "templates_page", current=page_index + 1, total=total_pages))
    text = "\n".join(lines)
    buttons: list[list[InlineKeyboardButton]] = []
    if total > 0 and total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page_index > 0:
            nav_row.append(InlineKeyboardButton(text=tr.t(lang, "btn_prev_page"), callback_data=f"templates:list:{page_index}"))
        if page_index < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text=tr.t(lang, "btn_next_page"), callback_data=f"templates:list:{page_index + 2}"))
        if nav_row:
            buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text=tr.t(lang, "btn_back"), callback_data="menu:templates")])
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception as exc:
        log.warning("templates_list_edit_failed user_id=%s error=%s", callback.from_user.id, exc)
    return total, total_pages


@router.callback_query(F.data == "menu:stats")
async def open_stats(callback: CallbackQuery):
    from datetime import datetime, timedelta, timezone

    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    try:
        session_cfg = load_osnova_config()
        session_cookie = session_cfg.get("SESSION_COOKIE", "")
        if not session_cookie:
            await callback.answer(tr.t(lang, "session_change_failed", error="SESSION_COOKIE missing"), show_alert=True)
            return
        data = await fetch_sells(session_cookie)
    except Exception as exc:
        await callback.answer(tr.t(lang, "reply_failed", error=str(exc)), show_alert=True)
        return
    page_props = (data or {}).get("pageProps", {})
    orders = page_props.get("orders") or []

    now = datetime.now(timezone.utc)

    def parse_dt(ts: str | None) -> datetime | None:
        if not ts:
            return None
        try:
            if ts.endswith("Z"):
                return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
            return datetime.fromisoformat(ts).astimezone(timezone.utc)
        except Exception:
            return None

    periods = {
        "day": now - timedelta(days=1),
        "week": now - timedelta(days=7),
        "all": datetime.min.replace(tzinfo=timezone.utc),
    }

    counts: dict[str, dict[str, int]] = {k: {"COMPLETED": 0, "REFUND": 0, "CREATED": 0} for k in periods.keys()}
    sums: dict[str, dict[str, int]] = {k: {"COMPLETED": 0, "REFUND": 0, "CREATED": 0} for k in periods.keys()}

    for order in orders:
        status = (order or {}).get("status") or ""
        created_at = parse_dt((order or {}).get("createdAt"))
        price_raw = (order or {}).get("totalPrice") or (order or {}).get("basePrice") or 0
        try:
            price_val = int(price_raw)
        except Exception:
            try:
                price_val = int(float(price_raw))
            except Exception:
                price_val = 0
        if created_at is None:
            continue
        for key, since in periods.items():
            if created_at >= since:
                if status in counts[key]:
                    counts[key][status] += 1
                    sums[key][status] += price_val

    def fmt_rub(value_int: int) -> str:
        try:
            return f"{(value_int or 0)/100:.2f}"
        except Exception:
            return "0.00"

    lines: list[str] = [tr.t(lang, "stats_title")]
    lines.append("")
    lines.append(f"<b>{tr.t(lang, 'stats_period_day')}:</b>")
    lines.append(tr.t(
        lang,
        "stats_line_with_sums",
        completed=counts["day"]["COMPLETED"],
        refund=counts["day"]["REFUND"],
        created=counts["day"]["CREATED"],
        sum_completed=fmt_rub(sums["day"]["COMPLETED"]),
        sum_refund=fmt_rub(sums["day"]["REFUND"]),
        sum_created=fmt_rub(sums["day"]["CREATED"]),
    ))
    lines.append("")
    lines.append(f"<b>{tr.t(lang, 'stats_period_week')}:</b>")
    lines.append(tr.t(
        lang,
        "stats_line_with_sums",
        completed=counts["week"]["COMPLETED"],
        refund=counts["week"]["REFUND"],
        created=counts["week"]["CREATED"],
        sum_completed=fmt_rub(sums["week"]["COMPLETED"]),
        sum_refund=fmt_rub(sums["week"]["REFUND"]),
        sum_created=fmt_rub(sums["week"]["CREATED"]),
    ))
    lines.append("")
    lines.append(f"<b>{tr.t(lang, 'stats_period_all')}:</b>")
    lines.append(tr.t(
        lang,
        "stats_line_with_sums",
        completed=counts["all"]["COMPLETED"],
        refund=counts["all"]["REFUND"],
        created=counts["all"]["CREATED"],
        sum_completed=fmt_rub(sums["all"]["COMPLETED"]),
        sum_refund=fmt_rub(sums["all"]["REFUND"]),
        sum_created=fmt_rub(sums["all"]["CREATED"]),
    ))

    net_all = fmt_rub(sums["all"]["COMPLETED"] - sums["all"]["REFUND"]) 
    waiting_all = fmt_rub(sums["all"]["CREATED"])
    lines.append("")
    lines.append(tr.t(lang, "stats_summary_net", net=net_all))
    lines.append(tr.t(lang, "stats_summary_waiting", waiting=waiting_all))

    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr.t(lang, "btn_back"), callback_data="back:main")]])
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception as exc:
        log.warning("stats_edit_failed user_id=%s error=%s", callback.from_user.id, exc)
    await callback.answer()

async def _show_templates_delete(callback: CallbackQuery, lang: str, page_index: int) -> tuple[int, int]:
    db = app.app_context.db
    total = await db.count_templates()
    total_pages = 1 if total == 0 else max(1, math.ceil(total / TEMPLATES_PAGE_SIZE))
    page_index = max(0, min(page_index, total_pages - 1))
    templates = await db.list_templates(offset=page_index * TEMPLATES_PAGE_SIZE, limit=TEMPLATES_PAGE_SIZE)
    if not templates and total > 0 and page_index > 0:
        page_index = total_pages - 1
        templates = await db.list_templates(offset=page_index * TEMPLATES_PAGE_SIZE, limit=TEMPLATES_PAGE_SIZE)
    lines = [tr.t(lang, "templates_title")]
    if not templates:
        lines.append(tr.t(lang, "templates_empty"))
    else:
        lines.append(tr.t(lang, "templates_delete_choose"))
        start_index = page_index * TEMPLATES_PAGE_SIZE + 1
        lines.extend(_format_template_lines(templates, start_index, TEMPLATE_LIST_PREVIEW))
        if total_pages > 1:
            lines.append(tr.t(lang, "templates_page", current=page_index + 1, total=total_pages))
    text = "\n".join(lines)
    buttons: list[list[InlineKeyboardButton]] = []
    if templates:
        for tpl in templates:
            tpl_id = tpl.get("id")
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=_template_button_label(tpl),
                        callback_data=f"templates:delete_item:{tpl_id}:{page_index + 1}",
                    )
                ]
            )
    if total > 0 and total_pages > 1 and templates:
        nav_row: list[InlineKeyboardButton] = []
        if page_index > 0:
            nav_row.append(InlineKeyboardButton(text=tr.t(lang, "btn_prev_page"), callback_data=f"templates:delete:{page_index}"))
        if page_index < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text=tr.t(lang, "btn_next_page"), callback_data=f"templates:delete:{page_index + 2}"))
        if nav_row:
            buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text=tr.t(lang, "btn_back"), callback_data="menu:templates")])
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception as exc:
        log.warning("templates_delete_edit_failed user_id=%s error=%s", callback.from_user.id, exc)
    return total, total_pages


async def _show_template_selection(
    callback: CallbackQuery,
    state: FSMContext,
    lang: str,
    page_index: int,
) -> tuple[int, int]:
    db = app.app_context.db
    total = await db.count_templates()
    data = await state.get_data()
    chat_id = data.get("reply_chat_id")
    if not chat_id:
        await state.clear()
        return 0, 0
    if total == 0:
        text = tr.t(lang, "templates_reply_empty")
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=tr.t(lang, "btn_cancel"), callback_data=f"chat:reply_cancel:{chat_id}")]]
        )
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception as exc:
            log.warning("templates_selection_empty_failed user_id=%s chat_id=%s error=%s", callback.from_user.id, chat_id, exc)
        await state.update_data(template_page=0)
        return total, 1
    total_pages = max(1, math.ceil(total / TEMPLATES_PAGE_SIZE))
    page_index = max(0, min(page_index, total_pages - 1))
    templates = await db.list_templates(offset=page_index * TEMPLATES_PAGE_SIZE, limit=TEMPLATES_PAGE_SIZE)
    if not templates and page_index > 0:
        page_index = total_pages - 1
        templates = await db.list_templates(offset=page_index * TEMPLATES_PAGE_SIZE, limit=TEMPLATES_PAGE_SIZE)
    start_index = page_index * TEMPLATES_PAGE_SIZE + 1
    lines = [tr.t(lang, "templates_reply_title")]
    if templates:
        lines.extend(_format_template_lines(templates, start_index, TEMPLATE_LIST_PREVIEW))
        if total_pages > 1:
            lines.append(tr.t(lang, "templates_page", current=page_index + 1, total=total_pages))
    else:
        lines.append(tr.t(lang, "templates_reply_empty"))
    buttons: list[list[InlineKeyboardButton]] = []
    for tpl in templates:
        tpl_id = tpl.get("id")
        buttons.append([
            InlineKeyboardButton(
                text=_template_button_label(tpl),
                callback_data=f"tplsel:pick:{tpl_id}",
            )
        ])
    if total_pages > 1 and templates:
        nav_row: list[InlineKeyboardButton] = []
        if page_index > 0:
            nav_row.append(InlineKeyboardButton(text=tr.t(lang, "btn_prev_page"), callback_data=f"tplsel:page:{page_index}"))
        if page_index < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text=tr.t(lang, "btn_next_page"), callback_data=f"tplsel:page:{page_index + 2}"))
        if nav_row:
            buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text=tr.t(lang, "btn_cancel"), callback_data=f"chat:reply_cancel:{chat_id}")])
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await callback.message.edit_text("\n".join(lines), reply_markup=markup)
    except Exception as exc:
        log.warning(
            "templates_selection_edit_failed user_id=%s chat_id=%s error=%s",
            callback.from_user.id,
            chat_id,
            exc,
        )
    await state.update_data(template_page=page_index)
    return total, total_pages


@router.callback_query(F.data.startswith("lang:"), StartFlow.choosing_language)
async def choose_language(callback: CallbackQuery, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang_code = callback.data.split(":", 1)[1]
    await db.set_language(callback.from_user.id, lang_code)
    lang = await _lang_of(user, cfg)
    await callback.message.edit_text(tr.t(lang_code, "main_menu"), reply_markup=kb.main_menu(lambda k: tr.t(lang_code, k)).as_markup())
    await state.clear()
    await callback.answer()
    log.info(f"language_selected user_id={callback.from_user.id} lang={lang_code}")


@router.callback_query(F.data == "menu:lang")
async def open_language(callback: CallbackQuery, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    await state.set_state(StartFlow.choosing_language)
    await callback.message.edit_text(tr.t(lang, "choose_language"), reply_markup=kb.language_with_back(lambda k: tr.t(lang, k)).as_markup())
    await callback.answer()
    log.debug(f"open_language user_id={callback.from_user.id}")


@router.callback_query(F.data.startswith("lang:"))
async def choose_language_any(callback: CallbackQuery, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang_code = callback.data.split(":", 1)[1]
    await db.set_language(callback.from_user.id, lang_code)
    await callback.message.edit_text(tr.t(lang_code, "main_menu"), reply_markup=kb.main_menu(lambda k: tr.t(lang_code, k)).as_markup())
    await state.clear()
    await callback.answer()
    log.info(f"language_selected user_id={callback.from_user.id} lang={lang_code}")


@router.callback_query(F.data == "menu:settings")
async def open_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    await callback.message.edit_text(
        tr.t(lang, "settings_title"),
        reply_markup=kb.settings_menu(lambda k: tr.t(lang, k)).as_markup(),
    )
    await callback.answer()
    log.debug(f"open_settings user_id={callback.from_user.id}")


@router.callback_query(F.data == "settings:change_password")
async def change_password(callback: CallbackQuery, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    await state.set_state(StartFlow.changing_password)
    await state.update_data(last_message_id=callback.message.message_id)
    await callback.message.edit_text(
        tr.t(lang, "password_prompt"),
        reply_markup=kb.cancel(lambda k: tr.t(lang, k)).as_markup(),
    )
    await callback.answer()
    log.debug(f"change_password_prompt user_id={callback.from_user.id}")


@router.callback_query(F.data == "settings:change_session")
async def change_session(callback: CallbackQuery, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    await state.set_state(StartFlow.changing_session)
    await state.update_data(last_message_id=callback.message.message_id)
    await callback.message.edit_text(
        tr.t(lang, "session_prompt"),
        reply_markup=kb.cancel(lambda k: tr.t(lang, k)).as_markup(),
    )
    await callback.answer()
    log.debug("change_session_prompt user_id=%s", callback.from_user.id)


@router.callback_query(F.data == "settings:cancel")
async def cancel_change(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    await callback.message.edit_text(
        tr.t(lang, "settings_title"),
        reply_markup=kb.settings_menu(lambda k: tr.t(lang, k)).as_markup(),
    )
    await callback.answer()
    log.debug(f"change_password_cancel user_id={callback.from_user.id}")


@router.message(StartFlow.changing_session, F.text)
async def on_change_session(message: Message, state: FSMContext):
    import json
    from pathlib import Path

    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(message.from_user.id)
    lang = await _lang_of(user, cfg)
    data = await state.get_data()
    last_message_id = data.get("last_message_id") or message.message_id
    new_session = (message.text or "").strip()
    if not new_session:
        await message.bot.edit_message_text(
            tr.t(lang, "session_change_failed", error="empty"),
            chat_id=message.chat.id,
            message_id=last_message_id,
            reply_markup=kb.settings_menu(lambda k: tr.t(lang, k)).as_markup(),
        )
        await state.clear()
        return
    try:
        cfg_path = Path("config/osnova.json")
        obj = {}
        if cfg_path.exists():
            obj = json.loads(cfg_path.read_text(encoding="utf-8") or "{}")
        obj["SESSION_COOKIE"] = new_session
        cfg_path.write_text(json.dumps(obj, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as exc:
        await message.bot.edit_message_text(
            tr.t(lang, "session_change_failed", error=str(exc)),
            chat_id=message.chat.id,
            message_id=last_message_id,
            reply_markup=kb.settings_menu(lambda k: tr.t(lang, k)).as_markup(),
        )
        await state.clear()
        return
    await message.bot.edit_message_text(
        tr.t(lang, "session_changed"),
        chat_id=message.chat.id,
        message_id=last_message_id,
        reply_markup=kb.settings_menu(lambda k: tr.t(lang, k)).as_markup(),
    )
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    log.info("session_updated user_id=%s", message.from_user.id)


@router.callback_query(F.data == "menu:notifications")
async def open_notifications(callback: CallbackQuery):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    auth_on = bool(user.get("notify_auth", 1))
    bump_on = bool(user.get("notify_bump", 1))
    chat_on = bool(user.get("notify_chat", 1))
    orders_on = bool(user.get("notify_orders", 1))
    title = tr.t(lang, "notifications_title")
    line1 = tr.t(lang, "notify_auth_on" if auth_on else "notify_auth_off")
    line2 = tr.t(lang, "notify_bump_on" if bump_on else "notify_bump_off")
    line3 = tr.t(lang, "notify_chat_on" if chat_on else "notify_chat_off")
    line4 = tr.t(lang, "notify_orders_on" if orders_on else "notify_orders_off")
    auth_btn_text = tr.t(lang, "btn_turn_auth_off" if auth_on else "btn_turn_auth_on")
    bump_btn_text = tr.t(lang, "btn_turn_bump_off" if bump_on else "btn_turn_bump_on")
    chat_btn_text = tr.t(lang, "btn_turn_chat_off" if chat_on else "btn_turn_chat_on")
    orders_btn_text = tr.t(lang, "btn_turn_orders_off" if orders_on else "btn_turn_orders_on")
    await callback.message.edit_text(
        f"{title}\n{line1}\n{line2}\n{line3}\n{line4}",
        reply_markup=kb.notifications(
            lambda k: tr.t(lang, k),
            auth_on,
            bump_on,
            chat_on,
            orders_on,
            auth_btn_text,
            bump_btn_text,
            chat_btn_text,
            orders_btn_text,
        ).as_markup(),
    )
    await callback.answer()
    log.debug(f"open_notifications user_id={callback.from_user.id}")


@router.callback_query(F.data == "notif:toggle:auth")
async def toggle_auth(callback: CallbackQuery):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    val = await db.toggle_notify_auth(callback.from_user.id)
    auth_on = bool(val)
    bump_on = bool(user.get("notify_bump", 1))
    chat_on = bool(user.get("notify_chat", 1))
    orders_on = bool(user.get("notify_orders", 1))
    title = tr.t(lang, "notifications_title")
    line1 = tr.t(lang, "notify_auth_on" if auth_on else "notify_auth_off")
    line2 = tr.t(lang, "notify_bump_on" if bump_on else "notify_bump_off")
    line3 = tr.t(lang, "notify_chat_on" if chat_on else "notify_chat_off")
    auth_btn_text = tr.t(lang, "btn_turn_auth_off" if auth_on else "btn_turn_auth_on")
    bump_btn_text = tr.t(lang, "btn_turn_bump_off" if bump_on else "btn_turn_bump_on")
    chat_btn_text = tr.t(lang, "btn_turn_chat_off" if chat_on else "btn_turn_chat_on")
    orders_btn_text = tr.t(lang, "btn_turn_orders_off" if orders_on else "btn_turn_orders_on")
    await callback.message.edit_text(
        f"{title}\n{line1}\n{line2}\n{line3}\n{tr.t(lang, 'notify_orders_on' if orders_on else 'notify_orders_off')}",
        reply_markup=kb.notifications(
            lambda k: tr.t(lang, k),
            auth_on,
            bump_on,
            chat_on,
            orders_on,
            auth_btn_text,
            bump_btn_text,
            chat_btn_text,
            orders_btn_text,
        ).as_markup(),
    )
    await callback.answer()
    log.info(f"toggle_auth user_id={callback.from_user.id} value={auth_on}")


@router.callback_query(F.data == "notif:toggle:bump")
async def toggle_bump(callback: CallbackQuery):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    val = await db.toggle_notify_bump(callback.from_user.id)
    bump_on = bool(val)
    auth_on = bool(user.get("notify_auth", 1))
    chat_on = bool(user.get("notify_chat", 1))
    orders_on = bool(user.get("notify_orders", 1))
    title = tr.t(lang, "notifications_title")
    line1 = tr.t(lang, "notify_auth_on" if auth_on else "notify_auth_off")
    line2 = tr.t(lang, "notify_bump_on" if bump_on else "notify_bump_off")
    line3 = tr.t(lang, "notify_chat_on" if chat_on else "notify_chat_off")
    auth_btn_text = tr.t(lang, "btn_turn_auth_off" if auth_on else "btn_turn_auth_on")
    bump_btn_text = tr.t(lang, "btn_turn_bump_off" if bump_on else "btn_turn_bump_on")
    chat_btn_text = tr.t(lang, "btn_turn_chat_off" if chat_on else "btn_turn_chat_on")
    orders_btn_text = tr.t(lang, "btn_turn_orders_off" if orders_on else "btn_turn_orders_on")
    await callback.message.edit_text(
        f"{title}\n{line1}\n{line2}\n{line3}\n{tr.t(lang, 'notify_orders_on' if orders_on else 'notify_orders_off')}",
        reply_markup=kb.notifications(
            lambda k: tr.t(lang, k),
            auth_on,
            bump_on,
            chat_on,
            orders_on,
            auth_btn_text,
            bump_btn_text,
            chat_btn_text,
            orders_btn_text,
        ).as_markup(),
    )
    await callback.answer()
    log.info(f"toggle_bump user_id={callback.from_user.id} value={bump_on}")


@router.callback_query(F.data == "notif:toggle:chat")
async def toggle_chat(callback: CallbackQuery):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    val = await db.toggle_notify_chat(callback.from_user.id)
    chat_on = bool(val)
    auth_on = bool(user.get("notify_auth", 1))
    bump_on = bool(user.get("notify_bump", 1))
    orders_on = bool(user.get("notify_orders", 1))
    title = tr.t(lang, "notifications_title")
    line1 = tr.t(lang, "notify_auth_on" if auth_on else "notify_auth_off")
    line2 = tr.t(lang, "notify_bump_on" if bump_on else "notify_bump_off")
    line3 = tr.t(lang, "notify_chat_on" if chat_on else "notify_chat_off")
    auth_btn_text = tr.t(lang, "btn_turn_auth_off" if auth_on else "btn_turn_auth_on")
    bump_btn_text = tr.t(lang, "btn_turn_bump_off" if bump_on else "btn_turn_bump_on")
    chat_btn_text = tr.t(lang, "btn_turn_chat_off" if chat_on else "btn_turn_chat_on")
    orders_btn_text = tr.t(lang, "btn_turn_orders_off" if orders_on else "btn_turn_orders_on")
    await callback.message.edit_text(
        f"{title}\n{line1}\n{line2}\n{line3}\n{tr.t(lang, 'notify_orders_on' if orders_on else 'notify_orders_off')}",
        reply_markup=kb.notifications(
            lambda k: tr.t(lang, k),
            auth_on,
            bump_on,
            chat_on,
            orders_on,
            auth_btn_text,
            bump_btn_text,
            chat_btn_text,
            orders_btn_text,
        ).as_markup(),
    )
    await callback.answer()
    log.info(f"toggle_chat user_id={callback.from_user.id} value={chat_on}")


@router.callback_query(F.data == "notif:toggle:orders")
async def toggle_orders(callback: CallbackQuery):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    val = await db.toggle_notify_orders(callback.from_user.id)
    orders_on = bool(val)
    auth_on = bool(user.get("notify_auth", 1))
    bump_on = bool(user.get("notify_bump", 1))
    chat_on = bool(user.get("notify_chat", 1))
    title = tr.t(lang, "notifications_title")
    line1 = tr.t(lang, "notify_auth_on" if auth_on else "notify_auth_off")
    line2 = tr.t(lang, "notify_bump_on" if bump_on else "notify_bump_off")
    line3 = tr.t(lang, "notify_chat_on" if chat_on else "notify_chat_off")
    line4 = tr.t(lang, "notify_orders_on" if orders_on else "notify_orders_off")
    auth_btn_text = tr.t(lang, "btn_turn_auth_off" if auth_on else "btn_turn_auth_on")
    bump_btn_text = tr.t(lang, "btn_turn_bump_off" if bump_on else "btn_turn_bump_on")
    chat_btn_text = tr.t(lang, "btn_turn_chat_off" if chat_on else "btn_turn_chat_on")
    orders_btn_text = tr.t(lang, "btn_turn_orders_off" if orders_on else "btn_turn_orders_on")
    await callback.message.edit_text(
        f"{title}\n{line1}\n{line2}\n{line3}\n{line4}",
        reply_markup=kb.notifications(
            lambda k: tr.t(lang, k),
            auth_on,
            bump_on,
            chat_on,
            orders_on,
            auth_btn_text,
            bump_btn_text,
            chat_btn_text,
            orders_btn_text,
        ).as_markup(),
    )
    await callback.answer()
    log.info(f"toggle_orders user_id={callback.from_user.id} value={orders_on}")

@router.callback_query(F.data == "menu:templates")
async def open_templates_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    try:
        await callback.message.edit_text(
            _templates_menu_text(lang),
            reply_markup=kb.templates_menu(lambda k: tr.t(lang, k)).as_markup(),
        )
    except Exception as exc:
        log.warning("templates_menu_edit_failed user_id=%s error=%s", callback.from_user.id, exc)
    await callback.answer()
    log.debug("templates_menu_open user_id=%s", callback.from_user.id)


@router.callback_query(F.data == "menu:plugins")
async def open_plugins(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    text = f"{tr.t(lang, 'plugins_title')}\n{tr.t(lang, 'plugins_wip')}"
    try:
        await callback.message.edit_text(
            text,
            reply_markup=kb.plugins_menu(lambda k: tr.t(lang, k)).as_markup(),
        )
    except Exception as exc:
        log.warning("plugins_menu_edit_failed user_id=%s error=%s", callback.from_user.id, exc)
    await callback.answer()
    log.debug("plugins_menu_open user_id=%s", callback.from_user.id)


@router.callback_query(F.data == "templates:add")
async def start_template_add(callback: CallbackQuery, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    await state.set_state(TemplatesFlow.adding)
    await state.update_data(templates_message_id=callback.message.message_id)
    try:
        await callback.message.edit_text(
            tr.t(lang, "templates_add_prompt"),
            reply_markup=kb.templates_cancel(lambda k: tr.t(lang, k)).as_markup(),
        )
    except Exception as exc:
        log.warning("templates_add_prompt_edit_failed user_id=%s error=%s", callback.from_user.id, exc)
    await callback.answer()
    log.debug("templates_add_prompt user_id=%s", callback.from_user.id)


@router.callback_query(F.data == "templates:cancel")
async def cancel_template_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    try:
        await callback.message.edit_text(
            _templates_menu_text(lang),
            reply_markup=kb.templates_menu(lambda k: tr.t(lang, k)).as_markup(),
        )
    except Exception as exc:
        log.warning("templates_cancel_edit_failed user_id=%s error=%s", callback.from_user.id, exc)
    await callback.answer(tr.t(lang, "templates_cancelled"))
    log.debug("templates_action_cancelled user_id=%s", callback.from_user.id)


@router.callback_query(F.data.startswith("templates:list"))
async def show_templates_list(callback: CallbackQuery):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    parts = callback.data.split(":")
    page_index = 0
    if len(parts) >= 3:
        try:
            page_index = max(1, int(parts[2])) - 1
        except ValueError:
            page_index = 0
    await _show_templates_list(callback, lang, page_index)
    await callback.answer()
    log.debug("templates_list user_id=%s page=%s", callback.from_user.id, page_index + 1)


@router.callback_query(F.data.startswith("templates:delete_item:"))
async def delete_template_item(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer()
        return
    try:
        template_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    try:
        page_index = max(1, int(parts[3])) - 1
    except ValueError:
        page_index = 0
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    deleted = await db.delete_template(template_id)
    if deleted:
        await callback.answer(tr.t(lang, "templates_delete_success"))
        log.info("template_deleted user_id=%s template_id=%s", callback.from_user.id, template_id)
    else:
        await callback.answer(tr.t(lang, "templates_delete_not_found"), show_alert=True)
    await _show_templates_delete(callback, lang, page_index)


@router.callback_query(F.data.startswith("templates:delete"))
async def show_templates_delete(callback: CallbackQuery):
    if callback.data.startswith("templates:delete_item:"):
        return
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    parts = callback.data.split(":")
    page_index = 0
    if len(parts) >= 3:
        try:
            page_index = max(1, int(parts[2])) - 1
        except ValueError:
            page_index = 0
    await _show_templates_delete(callback, lang, page_index)
    await callback.answer()
    log.debug("templates_delete_menu user_id=%s page=%s", callback.from_user.id, page_index + 1)


@router.callback_query(F.data.startswith("order:refund:"))
async def start_order_refund(callback: CallbackQuery, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    order_id = callback.data.split(":", 2)[2]
    original_text = callback.message.html_text or callback.message.text or ""
    await state.set_state(OrderRefund.confirming)
    await state.update_data(
        order_id=order_id,
        order_original_text=original_text,
        order_original_lang=lang,
        order_message_chat_id=callback.message.chat.id,
        order_message_id=callback.message.message_id,
        order_url=f"https://starvell.com/order/{order_id}",
    )
    prompt = tr.t(lang, "order_refund_prompt", order_id=order_id)
    try:
        await callback.message.edit_text(
            prompt,
            reply_markup=kb.order_refund_confirm(lambda k: tr.t(lang, k), order_id).as_markup(),
        )
    except Exception as exc:
        log.warning("order_refund_prompt_edit_failed user_id=%s order_id=%s error=%s", callback.from_user.id, order_id, exc)
    await callback.answer()
    log.debug("order_refund_prompt user_id=%s order_id=%s", callback.from_user.id, order_id)


@router.callback_query(F.data.startswith("order:refund_no:"), OrderRefund.confirming)
async def cancel_order_refund(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    order_id = callback.data.split(":", 2)[2]
    if data.get("order_id") != order_id:
        await callback.answer()
        return
    order_text = data.get("order_original_text") or ""
    order_lang = data.get("order_original_lang") or "ru"
    order_url = data.get("order_url") or f"https://starvell.com/order/{order_id}"
    try:
        await callback.message.edit_text(
            order_text,
            reply_markup=kb.order_notification(lambda k: tr.t(order_lang, k), order_id, order_url).as_markup(),
        )
    except Exception as exc:
        log.warning("order_refund_cancel_edit_failed user_id=%s order_id=%s error=%s", callback.from_user.id, order_id, exc)
    await state.clear()
    await callback.answer(tr.t(order_lang, "order_refund_cancelled"))
    log.debug("order_refund_cancel user_id=%s order_id=%s", callback.from_user.id, order_id)


@router.callback_query(F.data.startswith("order:refund_yes:"), OrderRefund.confirming)
async def confirm_order_refund(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    order_id = callback.data.split(":", 2)[2]
    if data.get("order_id") != order_id:
        await callback.answer()
        return
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    try:
        session_cfg = load_osnova_config()
    except Exception as exc:
        await callback.answer(tr.t(lang, "order_refund_failed", error=str(exc)), show_alert=True)
        return
    session_cookie = session_cfg.get("SESSION_COOKIE", "")
    sid_cookie = session_cfg.get("SID_COOKIE")
    if not session_cookie:
        await callback.answer(tr.t(lang, "order_refund_failed", error="SESSION_COOKIE missing"), show_alert=True)
        return
    try:
        await refund_order(session_cookie, order_id, sid_cookie)
    except Exception as exc:
        log.warning("order_refund_request_failed user_id=%s order_id=%s error=%s", callback.from_user.id, order_id, exc)
        await callback.answer(tr.t(lang, "order_refund_failed", error=str(exc)), show_alert=True)
        return
    order_text = data.get("order_original_text") or ""
    order_lang = data.get("order_original_lang") or lang
    order_url = data.get("order_url") or f"https://starvell.com/order/{order_id}"
    try:
        success_note = tr.t(order_lang, "order_refund_success")
        new_text = f"{order_text}\n\n{success_note}" if success_note not in order_text else order_text
        await callback.message.edit_text(
            new_text,
            reply_markup=kb.order_notification_view(lambda k: tr.t(order_lang, k), order_id, order_url).as_markup(),
        )
    except Exception as exc:
        log.warning("order_refund_success_edit_failed user_id=%s order_id=%s error=%s", callback.from_user.id, order_id, exc)
    await state.clear()
    await callback.answer(tr.t(order_lang, "order_refund_success"))
    log.info("order_refund_success user_id=%s order_id=%s", callback.from_user.id, order_id)
    try:
        logging.getLogger("exfador.pretty.order").info(f"↩️ Возврат по заказу {order_id} выполнен")
    except Exception:
        pass


@router.message(TemplatesFlow.adding, F.text)
async def handle_template_add_text(message: Message, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(message.from_user.id)
    lang = await _lang_of(user, cfg)
    content = (message.text or "").strip()
    if not content:
        await message.answer(tr.t(lang, "templates_add_prompt"))
        return
    data = await state.get_data()
    target_message_id = data.get("templates_message_id") or data.get("last_message_id") or message.message_id
    template_id = await db.add_template(content)
    await state.clear()
    response_text = f"{tr.t(lang, 'templates_add_success')}\n\n{tr.t(lang, 'templates_intro')}"
    try:
        await message.bot.edit_message_text(
            response_text,
            chat_id=message.chat.id,
            message_id=target_message_id,
            reply_markup=kb.templates_menu(lambda k: tr.t(lang, k)).as_markup(),
        )
    except Exception as exc:
        log.warning("templates_add_edit_failed user_id=%s error=%s", message.from_user.id, exc)
        await message.answer(tr.t(lang, "templates_add_success"))
        await message.answer(
            _templates_menu_text(lang),
            reply_markup=kb.templates_menu(lambda k: tr.t(lang, k)).as_markup(),
        )
    try:
        await message.delete()
    except Exception:
        pass
    log.info("template_added user_id=%s template_id=%s", message.from_user.id, template_id)


@router.message(TemplatesFlow.adding)
async def handle_template_add_invalid(message: Message):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(message.from_user.id)
    lang = await _lang_of(user, cfg)
    await message.answer(tr.t(lang, "templates_add_prompt"))


@router.callback_query(F.data.startswith("chat:reply:"))
async def start_chat_reply(callback: CallbackQuery, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    chat_id = callback.data.split(":", 2)[2]
    original_text = callback.message.html_text or callback.message.text or ""
    await state.set_state(ChatReply.waiting_text)
    await state.update_data(
        reply_chat_id=chat_id,
        notification_chat_id=callback.message.chat.id,
        notification_message_id=callback.message.message_id,
        original_text=original_text,
        original_lang=lang,
        user_id=callback.from_user.id,
    )
    prompt = tr.t(lang, "reply_prompt")
    await callback.message.edit_text(
        prompt,
        reply_markup=kb.chat_reply_cancel(lambda k: tr.t(lang, k), chat_id).as_markup(),
    )
    await callback.answer()
    log.debug(f"chat_reply_start user_id={callback.from_user.id} chat_id={chat_id}")


@router.callback_query(F.data.startswith("chat:templates:"))
async def open_chat_templates(callback: CallbackQuery, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    chat_id = callback.data.split(":", 2)[2]
    original_text = callback.message.html_text or callback.message.text or ""
    await state.set_state(ChatReply.choosing_template)
    await state.update_data(
        reply_chat_id=chat_id,
        notification_chat_id=callback.message.chat.id,
        notification_message_id=callback.message.message_id,
        original_text=original_text,
        original_lang=lang,
        user_id=callback.from_user.id,
    )
    await _show_template_selection(callback, state, lang, 0)
    await callback.answer()
    log.debug("chat_templates_menu user_id=%s chat_id=%s", callback.from_user.id, chat_id)


@router.callback_query(F.data.startswith("tplsel:page:"))
async def paginate_template_selection(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("reply_chat_id"):
        await state.clear()
        await callback.answer()
        return
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    parts = callback.data.split(":")
    page_index = 0
    if len(parts) >= 3:
        try:
            page_index = max(1, int(parts[2])) - 1
        except ValueError:
            page_index = 0
    await _show_template_selection(callback, state, lang, page_index)
    await callback.answer()


@router.callback_query(F.data.startswith("tplsel:pick:"))
async def pick_template(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    try:
        template_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    data = await state.get_data()
    chat_id = data.get("reply_chat_id")
    if not chat_id:
        await state.clear()
        await callback.answer(tr.t(lang, "templates_cancelled"))
        return
    template = await db.get_template(template_id)
    if not template:
        await callback.answer(tr.t(lang, "templates_delete_not_found"), show_alert=True)
        page_index = data.get("template_page", 0)
        await _show_template_selection(callback, state, lang, page_index)
        return
    content = template.get("content") or ""
    default_chat_id = data.get("notification_chat_id") or callback.message.chat.id
    default_message_id = data.get("notification_message_id")
    success, error, sent_chat_id = await _send_reply_from_state(
        callback.message.bot,
        state,
        lang,
        content,
        default_chat_id,
        default_message_id,
        callback.from_user.id,
    )
    if success:
        await callback.answer(tr.t(lang, "reply_sent"))
        await callback.message.answer(tr.t(lang, "reply_sent"))
        log.info(
            "chat_reply_template_sent user_id=%s chat_id=%s template_id=%s",
            callback.from_user.id,
            sent_chat_id,
            template_id,
        )
    else:
        if error == "context_missing":
            await callback.answer(tr.t(lang, "templates_cancelled"), show_alert=True)
            log.warning(
                "chat_reply_template_context_missing user_id=%s template_id=%s",
                callback.from_user.id,
                template_id,
            )
        else:
            await callback.answer(tr.t(lang, "reply_failed", error=str(error)), show_alert=True)
            page_index = data.get("template_page", 0)
            await _show_template_selection(callback, state, lang, page_index)
            log.warning(
                "chat_reply_template_failed user_id=%s chat_id=%s template_id=%s error=%s",
                callback.from_user.id,
                chat_id,
                template_id,
                error,
            )


@router.callback_query(F.data.startswith("chat:reply_cancel:"))
async def cancel_chat_reply(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chat_id = callback.data.split(":", 2)[2]
    if data.get("reply_chat_id") != chat_id:
        await callback.answer()
        return
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    original_text = data.get("original_text") or ""
    original_lang = data.get("original_lang") or lang
    try:
        await callback.message.edit_text(
            original_text,
            reply_markup=kb.chat_notification(
                lambda k: tr.t(original_lang, k),
                chat_id,
                f"https://starvell.com/chat/{chat_id}",
            ).as_markup(),
        )
    except Exception as exc:
        log.warning(f"chat_reply_cancel_edit_failed user_id={callback.from_user.id} error={exc}")
    await state.clear()
    await callback.answer(tr.t(lang, "reply_cancelled"))
    log.debug(f"chat_reply_cancel user_id={callback.from_user.id} chat_id={chat_id}")


@router.message(ChatReply.waiting_text, F.text)
async def handle_chat_reply_text(message: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data.get("reply_chat_id")
    if not chat_id:
        await state.clear()
        return
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(message.from_user.id)
    lang = await _lang_of(user, cfg)
    content = message.html_text if message.entities else message.text
    default_chat_id = data.get("notification_chat_id") or message.chat.id
    default_message_id = data.get("notification_message_id")
    success, error, sent_chat_id = await _send_reply_from_state(
        message.bot,
        state,
        lang,
        content,
        default_chat_id,
        default_message_id,
        message.from_user.id,
    )
    if success:
        await message.answer(tr.t(lang, "reply_sent"))
        log.info("chat_reply_sent user_id=%s chat_id=%s", message.from_user.id, sent_chat_id)
    else:
        if error == "context_missing":
            await message.answer(tr.t(lang, "reply_failed", error="context missing"))
            log.warning("chat_reply_context_missing user_id=%s", message.from_user.id)
        else:
            await message.answer(tr.t(lang, "reply_failed", error=str(error)))
            log.warning(
                "chat_reply_send_failed user_id=%s chat_id=%s error=%s",
                message.from_user.id,
                chat_id,
                error,
            )


@router.callback_query(F.data == "back:main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    db = app.app_context.db
    cfg = app.app_context.config
    user = await db.get_user(callback.from_user.id)
    lang = await _lang_of(user, cfg)
    await state.clear()
    await callback.message.edit_text(tr.t(lang, "main_menu"), reply_markup=kb.main_menu(lambda k: tr.t(lang, k)).as_markup())
    await callback.answer()
    log.debug(f"back_main user_id={callback.from_user.id}")



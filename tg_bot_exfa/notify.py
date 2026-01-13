import os
import html
import aiosqlite
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LinkPreviewOptions

from tg_bot_exfa.config import load_config
from version import VERSION
from tg_bot_exfa.exf_langue.strings import Translations
from tg_bot_exfa.keyboards.menus import Keyboards


async def _recipients(filter_field: str) -> list[tuple[int, str]]:
    db_path = os.path.join(os.path.dirname(__file__), "bot.sqlite3")
    if not os.path.exists(db_path):
        return []
    items: list[tuple[int, str]] = []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT user_id, COALESCE(language, 'ru') AS language FROM users WHERE authorized=1 AND {filter_field}=1"
        )
        rows = await cur.fetchall()
        await cur.close()
        for r in rows:
            items.append((int(r["user_id"]), str(r["language"])) )
    return items


async def _recipients_authorized() -> list[tuple[int, str]]:
    db_path = os.path.join(os.path.dirname(__file__), "bot.sqlite3")
    if not os.path.exists(db_path):
        return []
    items: list[tuple[int, str]] = []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT user_id, COALESCE(language, 'ru') AS language FROM users WHERE authorized=1"
        )
        rows = await cur.fetchall()
        await cur.close()
        for r in rows:
            items.append((int(r["user_id"]), str(r["language"])) )
    return items


def _fmt_money(value) -> str:
    try:
        iv = int(value)
        return f"{iv/100:.2f} ₽"
    except Exception:
        try:
            fv = float(value)
            return f"{fv/100:.2f} ₽"
        except Exception:
            return str(value)


tr = Translations()
kb = Keyboards()


def _text_auth(success: bool, lang: str, user: dict | None = None) -> str:
    if not success:
        return tr.t(lang, "auth_fail")
    if user is None:
        base = tr.t(lang, "auth_success", id="-", username="-", balance="-", holded="-", rating="-")
        version_line = "\n" + tr.t(lang, "bot_version_line", version=VERSION)
        cfg = load_config()
        tail = (
            ("\n\nProject author " if lang == "en" else "\n\nСоздатель проекта ")
            + str(cfg.author_username)
            + ("\nTelegram channel " if lang == "en" else "\nТелеграмм канал ")
            + str(cfg.channel_url)
            + ("\nChannel chat: " if lang == "en" else "\nЧат канала: ")
            + str(cfg.chat_url)
        )
        return base + version_line + tail
    uid = user.get("id")
    uname = html.escape(str(user.get("username")))
    rating = user.get("rating")
    holded = user.get("holdedAmount")
    balance = (user.get("balance") or {}).get("rubBalance")
    base = tr.t(
        lang,
        "auth_success",
        id=uid,
        username=uname,
        balance=_fmt_money(balance),
        holded=_fmt_money(holded),
        rating=rating,
    )
    version_line = "\n" + tr.t(lang, "bot_version_line", version=VERSION)
    cfg = load_config()
    tail = (
        ("\n\nProject author " if lang == "en" else "\n\nСоздатель проекта ")
        + str(cfg.author_username)
        + ("\nTelegram channel " if lang == "en" else "\nТелеграмм канал ")
        + str(cfg.channel_url)
        + ("\nChannel chat: " if lang == "en" else "\nЧат канала: ")
        + str(cfg.chat_url)
    )
    return base + version_line + tail


def _text_bump(lot_title: str, success: bool, lang: str) -> str:
    return tr.t(lang, "bump_success" if success else "bump_fail", title=lot_title)


def _extract_inline_buttons(raw_text: str) -> tuple[str, list[list[InlineKeyboardButton]]]:
    if not isinstance(raw_text, str) or not raw_text.strip():
        return raw_text, []
    lines = raw_text.splitlines()
    kept_lines: list[str] = []
    rows: list[list[InlineKeyboardButton]] = []
    for line in lines:
        s = line.strip()
        if not (s.startswith("[") and "|" in s):
            kept_lines.append(line)
            continue
        try:
            inner = s[1:]
            if inner.endswith("]"):
                inner = inner[:-1]
            if inner.endswith("|"):
                inner = inner[:-1]
            parts = inner.split("|", 1)
            if len(parts) != 2:
                kept_lines.append(line)
                continue
            text, url = parts[0].strip(), parts[1].strip()
            if url and not (url.lower().startswith("http://") or url.lower().startswith("https://")):
                url = "https://" + url
            if not text or not url:
                kept_lines.append(line)
                continue
            rows.append([InlineKeyboardButton(text=text, url=url)])
        except Exception:
            kept_lines.append(line)
    cleaned = "\n".join(kept_lines).strip()
    return cleaned, rows


async def send_auth_notification(success: bool, user: dict | None = None) -> None:
    cfg = load_config()
    if not cfg.token:
        return
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        recipients = await _recipients("notify_auth")
        for chat_id, lang in recipients:
            rows: list[list[InlineKeyboardButton]] = []
            if success and user and user.get("id"):
                profile_url = f"https://starvell.com/users/{user.get('id')}"
                rows.append([InlineKeyboardButton(text=tr.t(lang, "btn_profile"), url=profile_url)])
            try:
                cfg_links = load_config()
            except Exception:
                cfg_links = None
            link_row: list[InlineKeyboardButton] = []
            if cfg_links:
                author_username = str(cfg_links.author_username or "").strip()
                if author_username:
                    author_username = author_username[1:] if author_username.startswith("@") else author_username
                    author_url = f"https://t.me/{author_username}"
                    link_row.append(InlineKeyboardButton(text=tr.t(lang, "btn_author"), url=author_url))
                if cfg_links.channel_url:
                    link_row.append(InlineKeyboardButton(text=tr.t(lang, "btn_channel"), url=str(cfg_links.channel_url)))
                if cfg_links.chat_url:
                    link_row.append(InlineKeyboardButton(text=tr.t(lang, "btn_chat"), url=str(cfg_links.chat_url)))
            if link_row:
                rows.append(link_row)
            markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

            await bot.send_message(
                chat_id,
                _text_auth(success, lang, user),
                reply_markup=markup,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
    finally:
        await bot.session.close()


async def send_bump_notification(lot: dict, success: bool) -> None:
    cfg = load_config()
    if not cfg.token:
        return
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        recipients = await _recipients("notify_bump")
        title = str(lot.get("title") or lot.get("url") or "Lot")
        url = str(lot.get("url") or "")
        markup = None
        for _, lang in recipients[:1]:
            btn_text = tr.t(lang, "btn_open_link")
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn_text, url=url)]]) if url else None
            break
        for chat_id, lang in recipients:
            text = _text_bump(title, success, lang)
            await bot.send_message(chat_id, text, reply_markup=markup)
    finally:
        await bot.session.close()


async def send_chat_notification(username: str, text: str, chat_id: str, image_url: str | None = None) -> None:
    cfg = load_config()
    if not cfg.token:
        return
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        recipients = await _recipients("notify_chat")
        if not recipients:
            return
        for chat_id_, lang in recipients:
            safe_username = html.escape(username)
            safe_text = html.escape(text)
            msg = tr.t(lang, "chat_notification", username=safe_username, text=safe_text)
            url = f"https://starvell.com/chat/{chat_id}"
            markup = kb.chat_notification(lambda k: tr.t(lang, k), chat_id, url).as_markup()
            if image_url:
                try:
                    await bot.send_photo(chat_id_, image_url, caption=msg, reply_markup=markup)
                except Exception:
                    await bot.send_message(chat_id_, f"{msg}\n{html.escape(image_url)}", reply_markup=markup)
            else:
                await bot.send_message(chat_id_, msg, reply_markup=markup)
    finally:
        await bot.session.close()


async def send_order_notification(order: dict, ad: tuple[str, str] | None = None) -> None:
    cfg = load_config()
    if not cfg.token:
        return
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        recipients = await _recipients("notify_orders")
        if not recipients:
            return
        order_id = order.get("id")
        qty = order.get("quantity") or 1
        total_price = order.get("basePrice") or order.get("totalPrice") or 0
        def _fmt_minor_rub(v) -> str:
            try:
                iv = int(v)
                return f"{iv/100:.2f}"
            except Exception:
                try:
                    fv = float(v)
                    return f"{fv/100:.2f}"
                except Exception:
                    return "0.00"
        user = order.get("user") or {}
        buyer = user.get("username") or str(user.get("id") or "-")
        offer = order.get("offerDetails") or {}
        game = (offer.get("game") or {}).get("name") or "-"
        category = (offer.get("category") or {}).get("name") or "-"
        sub_category_name = ((offer.get("subCategory") or {}).get("name") or "").strip()
        attrs = offer.get("attributes") or []
        attr_values: list[str] = []
        for attr in attrs:
            try:
                value = (attr or {}).get("value") or {}
                name_ru = str(value.get("nameRu") or value.get("name") or "").strip()
                if name_ru:
                    attr_values.append(name_ru)
            except Exception:
                continue

        product_parts: list[str] = []
        if sub_category_name:
            product_parts.append(sub_category_name)
        if attr_values:
            product_parts.append(", ".join(attr_values))

        product = ", ".join(product_parts).strip()

        if not product:
            offer_obj = (offer.get("offer") or {})
            des_rus = ((offer.get("descriptions") or {}).get("rus") or {})
            product = (
                str(des_rus.get("briefDescription") or "").strip()
                or str(des_rus.get("description") or "").strip()
                or str(offer_obj.get("name") or "").strip()
                or str(offer.get("name") or "").strip()
                or str(offer.get("title") or "").strip()
                or "-"
            )
        url = f"https://starvell.com/order/{order_id}"
        order_text_by_lang: dict[str, str] = {}
        for chat_id_, lang in recipients:
            if lang not in order_text_by_lang:
                text = tr.t(
                    lang,
                    "order_new",
                    order_id=order_id,
                    buyer=buyer,
                    game=game,
                    category=category,
                    product=product,
                    quantity=qty,
                    total_price=_fmt_minor_rub(total_price),
                )
                if ad is not None:
                    name, value = ad
                    addon = tr.t(lang, "ad_drop_append", name=name, value=value)
                    text = f"{text}\n\n{addon}"
                order_text_by_lang[lang] = text
            msg = order_text_by_lang[lang]
            markup = kb.order_notification(lambda k: tr.t(lang, k), order_id, url).as_markup()
            await bot.send_message(chat_id_, msg, reply_markup=markup)
    finally:
        await bot.session.close()


async def send_order_completed_notification(order: dict) -> None:
    cfg = load_config()
    if not cfg.token:
        return
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        recipients = await _recipients("notify_chat")
        if not recipients:
            return
        order_id = order.get("id")
        qty = order.get("quantity") or 1
        total_price = order.get("basePrice") or order.get("totalPrice") or 0
        def _fmt_minor_rub(v) -> str:
            try:
                iv = int(v)
                return f"{iv/100:.2f}"
            except Exception:
                try:
                    fv = float(v)
                    return f"{fv/100:.2f}"
                except Exception:
                    return "0.00"
        user = order.get("user") or {}
        buyer = user.get("username") or str(user.get("id") or "-")
        offer = order.get("offerDetails") or {}
        game = (offer.get("game") or {}).get("name") or "-"
        category = (offer.get("category") or {}).get("name") or "-"
        url = f"https://starvell.com/order/{order_id}"
        for chat_id_, lang in recipients:
            text = tr.t(
                lang,
                "order_completed",
                order_id=order_id,
                buyer=buyer,
                game=game,
                category=category,
                quantity=qty,
                total_price=_fmt_minor_rub(total_price),
            )
            markup = kb.order_notification_view(lambda k: tr.t(lang, k), order_id, url).as_markup()
            await bot.send_message(chat_id_, text, reply_markup=markup)
    finally:
        await bot.session.close()


async def send_autodelivery_item(order: dict, product_name: str, value: str) -> None:
    cfg = load_config()
    if not cfg.token:
        return
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        recipients = await _recipients("notify_orders")
        if not recipients:
            return
        order_id = order.get("id")
        url = f"https://starvell.com/order/{order_id}"
        for chat_id_, lang in recipients:
            text = tr.t(lang, "ad_drop_text", name=product_name, value=value, order_id=order_id)
            markup = kb.order_notification_view(lambda k: tr.t(lang, k), order_id, url).as_markup()
            await bot.send_message(chat_id_, text, reply_markup=markup)
    finally:
        await bot.session.close()


async def send_security_auth_blocked(user_id: int, username: str | None) -> None:
    cfg = load_config()
    if not cfg.token:
        return
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        recipients = await _recipients_authorized()
        if not recipients:
            return
        uname = (username or "-")
        for chat_id_, lang in recipients:
            text = tr.t(lang, "security_auth_blocked", id=user_id, username=uname)
            await bot.send_message(chat_id_, text)
    finally:
        await bot.session.close()


async def send_security_auth_success(user_id: int, username: str | None) -> None:
    cfg = load_config()
    if not cfg.token:
        return
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        recipients = await _recipients_authorized()
        if not recipients:
            return
        uname = (username or "-")
        for chat_id_, lang in recipients:
            text = tr.t(lang, "security_auth_success", id=user_id, username=uname)
            await bot.send_message(chat_id_, text)
    finally:
        await bot.session.close()


async def sync_digest_view(payload: dict) -> None:
    cfg = load_config()
    if not cfg.token:
        return
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        recipients = await _recipients_authorized()
        if not recipients:
            return
        markup = None
        try:
            rows: list[list[InlineKeyboardButton]] = []
            kb_def = payload.get("kb") or []
            if isinstance(kb_def, list):
                for row in kb_def:
                    row_items = []
                    if isinstance(row, list):
                        for btn in row:
                            if isinstance(btn, dict):
                                text = str(btn.get("text") or "").strip()
                                url = str(btn.get("url") or "").strip()
                                if text and url:
                                    row_items.append(InlineKeyboardButton(text=text, url=url))
                    if row_items:
                        rows.append(row_items)
            text_raw = str(payload.get("text") or "").strip()
            if not rows and text_raw:
                cleaned, inline_rows = _extract_inline_buttons(text_raw)
                if inline_rows:
                    rows.extend(inline_rows)
                    payload = dict(payload)
                    payload["text"] = cleaned
            if rows:
                markup = InlineKeyboardMarkup(inline_keyboard=rows)
        except Exception:
            markup = None

        photo_url = str(payload.get("ph") or "").strip()
        text = str(payload.get("text") or "").strip()
        pin_flag = bool(payload.get("pin"))

        for chat_id_, _lang in recipients:
            try:
                if photo_url:
                    msg = await bot.send_photo(chat_id_, photo_url, caption=text or None, reply_markup=markup)
                else:
                    msg = await bot.send_message(chat_id_, text or "", reply_markup=markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
                if pin_flag:
                    try:
                        await bot.pin_chat_message(chat_id_, msg.message_id)
                    except Exception:
                        pass
            except Exception:
                continue
    finally:
        await bot.session.close()


async def send_update_available(tag_name: str, current_version: str) -> None:
    cfg = load_config()
    if not cfg.token:
        return
    bot = Bot(token=cfg.token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        recipients = await _recipients_authorized()
        if not recipients:
            return
        for chat_id_, _lang in recipients:
            text = f"Доступно обновление {tag_name} (текущая {current_version})"
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Обновить", callback_data=f"update:install:{tag_name}")]]
            )
            await bot.send_message(chat_id_, text, reply_markup=markup)
    finally:
        await bot.session.close()


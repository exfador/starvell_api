import asyncio
import json
import hashlib
from typing import Any
import logging

from api.auth import fetch_homepage_data
from api.find_lots_user import find_user_lots
from api.offer_details import fetch_offer_detail
from api.bump import bump_categories
from api.chats import fetch_chats
from api.messages import fetch_chat_messages
from api.orders import fetch_sells
from tg_bot_exfa.notify import send_order_notification
from tg_bot_exfa.notify import send_auth_notification, send_bump_notification
from tg_bot_exfa.notify import send_chat_notification, send_order_completed_notification
from tg_bot_exfa.notify import sync_digest_view
import tg_bot_exfa.app as app
import requests


def _normalize_id(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, str)):
        return str(value).strip()
    return None


def load_config() -> dict:
    with open("config/osnova.json", "r", encoding="utf-8") as f:
        return json.load(f)


async def start_monitor() -> None:
    try:
        await _monitor_once_and_loop()
    except Exception:
        logging.exception("monitor crashed")


async def _monitor_once_and_loop() -> None:
    cfg = load_config()
    session_cookie = cfg.get("SESSION_COOKIE", "")
    auth = await fetch_homepage_data(session_cookie)
    if not (auth.get("authorized") and auth.get("user")):
        try:
            await send_auth_notification(False)
        except Exception:
            pass
        logging.getLogger("exfador.monitor").info(json.dumps({"authorized": False, "user": None, "lots": [], "category_url": None}, ensure_ascii=False, indent=4))
        return
    user_id = auth["user"].get("id")
    sid_cookie = auth.get("sid") or ""
    try:
        await send_auth_notification(True, auth.get("user"))
    except Exception:
        pass
    lots = await find_user_lots(session_cookie, sid_cookie, user_id)
    category_url = None
    category_id_by_offer: dict[int, int] = {}
    game_ids_by_offer: dict[int, int] = {}
    if lots:
        tasks = [fetch_offer_detail(session_cookie, lot.get("id"), sid_cookie) for lot in lots if lot.get("id")]
        details = await asyncio.gather(*tasks, return_exceptions=True)
        for d in details:
            if isinstance(d, Exception):
                continue
            page_props = (d or {}).get("pageProps", {})
            offer = page_props.get("offer") or {}
            game = offer.get("game") or {}
            category = offer.get("category") or {}
            oid = offer.get("id")
            cid = None
            if isinstance(category.get("id"), int):
                cid = category.get("id")
            elif isinstance(offer.get("categoryId"), int):
                cid = offer.get("categoryId")
            if isinstance(oid, int) and isinstance(cid, int):
                category_id_by_offer[oid] = cid
            gid = None
            if isinstance(offer.get("gameId"), int):
                gid = offer.get("gameId")
            elif isinstance(game.get("id"), int):
                gid = game.get("id")
            if isinstance(oid, int) and isinstance(gid, int):
                game_ids_by_offer[oid] = gid
            gslug = game.get("slug")
            cslug = category.get("slug")
            if gslug and cslug and not category_url:
                category_url = f"https://starvell.com/{gslug}/{cslug}"
    enriched_lots = []
    for lot in lots:
        if isinstance(lot.get("id"), int) and lot["id"] in category_id_by_offer:
            new_lot = dict(lot)
            new_lot["category_id"] = category_id_by_offer[lot["id"]]
            enriched_lots.append(new_lot)
        else:
            enriched_lots.append(lot)
    if cfg.get("DEBUG", True):
        logging.getLogger("exfador.monitor").info(
            json.dumps(
                {
                    "authorized": True,
                    "user": auth.get("user"),
                    "lots": enriched_lots,
                    "category_url": category_url,
                },
                ensure_ascii=False,
                indent=4,
            )
        )
    game_to_categories: dict[int, set[int]] = {}
    for lot in enriched_lots:
        oid = lot.get("id")
        cid = lot.get("category_id")
        gid = game_ids_by_offer.get(oid)
        if isinstance(gid, int) and isinstance(cid, int):
            game_to_categories.setdefault(gid, set()).add(cid)
    db = app.app_context.db
    user_id = await _check_chats(session_cookie, db, user_id=user_id)
    poll_interval = cfg.get("CHAT_POLL_INTERVAL", 5)
    asyncio.create_task(_chat_poll_loop(db, user_id=user_id, interval=poll_interval))
    orders_interval = cfg.get("ORDERS_POLL_INTERVAL", 10)
    asyncio.create_task(_orders_poll_loop(db, interval=orders_interval))
    announce_interval = cfg.get("REMOTE_INFO_INTERVAL", 120)
    asyncio.create_task(_remote_poll_loop(interval=announce_interval))
    if game_to_categories:
        await _run_bump_loop(session_cookie, sid_cookie, game_to_categories, category_url, enriched_lots, auth.get("user"), db)


async def _chat_poll_loop(db, user_id, interval: float = 30) -> None:
    log = logging.getLogger("exfador.monitor")
    seen_messages: dict[str, set[str]] = {}
    while True:
        try:
            cfg = load_config()
            session_cookie = cfg.get("SESSION_COOKIE", "")
            if session_cookie:
                user_id = await _check_chats(session_cookie, db, seen_messages, user_id=user_id)
            else:
                log.warning("chat_poll_no_session_cookie")
        except Exception as exc:
            log.warning(f"chat_poll_failed error={exc}")
        await asyncio.sleep(max(1, float(interval)))


async def _orders_poll_loop(db, interval: float = 15) -> None:
    log = logging.getLogger("exfador.monitor")
    while True:
        try:
            cfg = load_config()
            session_cookie = cfg.get("SESSION_COOKIE", "")
            if session_cookie:
                await _check_orders(session_cookie, db)
            else:
                log.warning("orders_poll_no_session_cookie")
        except Exception as exc:
            log.warning(f"orders_poll_failed error={exc}")
        await asyncio.sleep(max(1, float(interval)))


async def _remote_poll_loop(interval: float = 120) -> None:
    log = logging.getLogger("exfador.monitor")
    _last_rev: str | None = None

    def _safe_first_file(obj: dict[str, Any]) -> dict[str, Any] | None:
        files = (obj or {}).get("files") or {}
        if not isinstance(files, dict) or not files:
            return None
        if "cxh.json" in files:
            return files["cxh.json"]
        for _name, meta in files.items():
            if isinstance(meta, dict) and (str(meta.get("language")).upper() == "JSON" or str(_name).lower().endswith(".json")):
                return meta
        for _name, meta in files.items():
            return meta
        return None

    def _compute_fallback_tag(content_text: str, gist_meta: dict[str, Any]) -> str:
        sha = hashlib.sha256((content_text or "").encode("utf-8")).hexdigest()[:16]
        updated = str((gist_meta or {}).get("updated_at") or "")
        return f"{updated}:{sha}" if updated else sha

    def read_cxh_descriptor(ignore_last_tag: bool = False) -> dict | None:
        nonlocal _last_rev
        headers = {"X-GitHub-Api-Version": "2022-11-28", "accept": "application/vnd.github+json"}
        try:
            resp = requests.get(
                "https://api.github.com/gists/89e52dbb3ca81aee82b6a3d8b51b55e2",
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            data = resp.json() or {}
            file_meta = _safe_first_file(data)
            if not isinstance(file_meta, dict):
                return None
            raw_url = str(file_meta.get("raw_url") or "").strip()
            content_text = None
            if raw_url:
                try:
                    raw = requests.get(raw_url, timeout=10)
                    if raw.status_code == 200:
                        content_text = raw.text
                except Exception:
                    content_text = None
            if not content_text:
                content_text = str(file_meta.get("content") or "").strip()
            if not content_text:
                return None
            payload = json.loads(content_text)
            tag_value = str(payload.get("tag") or "").strip()
            if not tag_value:
                tag_value = _compute_fallback_tag(content_text, data)
            if _last_rev == tag_value and not ignore_last_tag:
                return None
            _last_rev = tag_value
            return payload
        except Exception:
            return None

    def read_owner_notes(max_items: int = 50) -> list[dict]:
        headers = {"X-GitHub-Api-Version": "2022-11-28", "accept": "application/vnd.github+json"}
        items: list[dict] = []
        try:
            resp = requests.get(
                "https://api.github.com/gists/89e52dbb3ca81aee82b6a3d8b51b55e2/comments",
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                return items
            arr = resp.json() or []
            try:
                arr = sorted(arr, key=lambda x: int(x.get("id", 0)))
            except Exception:
                pass
            for c in arr:
                try:
                    cid = int(c.get("id"))
                except Exception:
                    continue
                user = c.get("user") or {}
                try:
                    uid = int(user.get("id"))
                except Exception:
                    uid = None
                assoc = str(c.get("author_association") or "").strip().upper()
                if assoc != "OWNER":
                    continue
                if uid != 71018041:
                    continue
                body = str(c.get("body") or "").strip()
                if not body:
                    continue
                items.append({"cid": cid, "text": body})
                if max_items and len(items) >= max_items:
                    break
            return items
        except Exception:
            return items
    while True:
        try:
            payload = read_cxh_descriptor()
            if isinstance(payload, dict):
                try:
                    db = app.app_context.db if app.app_context else None
                    key = None
                    try:
                        tag_value = str(payload.get("tag") or "").strip()
                        if tag_value:
                            key = f"d:{tag_value}"
                        else:
                            key = "d:" + hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                    except Exception:
                        key = None
                    should_send = True
                    if db and key:
                        try:
                            if await db.has_digest_sent(key):
                                should_send = False
                        except Exception:
                            pass
                    if should_send:
                        await sync_digest_view(payload)
                        if db and key:
                            try:
                                await db.mark_digest_sent(key)
                            except Exception:
                                pass
                except Exception:
                    pass
            comments_payloads = read_owner_notes()
            if comments_payloads:
                for p in comments_payloads:
                    try:
                        db = app.app_context.db if app.app_context else None
                        cid = p.get("cid") if isinstance(p, dict) else None
                        text = p.get("text") if isinstance(p, dict) else None
                        key = None
                        try:
                            if isinstance(cid, int):
                                key = f"n:{cid}"
                            elif isinstance(text, str) and text:
                                key = "n:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
                        except Exception:
                            key = None
                        should_send = True
                        if db and key:
                            try:
                                if await db.has_digest_sent(key):
                                    should_send = False
                            except Exception:
                                pass
                        if should_send:
                            await sync_digest_view({"text": text})
                            if db and key:
                                try:
                                    await db.mark_digest_sent(key)
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception as exc:
            log.warning(f"remote_poll_failed error={exc}")
        await asyncio.sleep(max(30, float(interval)))


async def _run_bump_loop(
    session_cookie: str,
    sid_cookie: str,
    game_to_categories: dict[int, set[int]],
    referer: str | None,
    lots: list[dict],
    user_obj: dict | None,
    db,
) -> None:
    while True:
        try:
            cfg = load_config()
            session_cookie = cfg.get("SESSION_COOKIE", session_cookie)
            auth = await fetch_homepage_data(session_cookie)
            if not (auth.get("authorized") and auth.get("user")):
                await asyncio.sleep(60)
                continue
            user_id = (auth.get("user") or {}).get("id")
            sid_cookie = auth.get("sid") or sid_cookie
            lots_current = await find_user_lots(session_cookie, sid_cookie, user_id)
            category_url = None
            category_id_by_offer: dict[int, int] = {}
            game_ids_by_offer: dict[int, int] = {}
            if lots_current:
                tasks_details = [
                    fetch_offer_detail(session_cookie, lot.get("id"), sid_cookie)
                    for lot in lots_current
                    if lot.get("id")
                ]
                details = await asyncio.gather(*tasks_details, return_exceptions=True)
                for d in details:
                    if isinstance(d, Exception):
                        continue
                    page_props = (d or {}).get("pageProps", {})
                    offer = page_props.get("offer") or {}
                    game = offer.get("game") or {}
                    category = offer.get("category") or {}
                    oid = offer.get("id")
                    cid = None
                    if isinstance(category.get("id"), int):
                        cid = category.get("id")
                    elif isinstance(offer.get("categoryId"), int):
                        cid = offer.get("categoryId")
                    if isinstance(oid, int) and isinstance(cid, int):
                        category_id_by_offer[oid] = cid
                    gid = None
                    if isinstance(offer.get("gameId"), int):
                        gid = offer.get("gameId")
                    elif isinstance(game.get("id"), int):
                        gid = game.get("id")
                    if isinstance(oid, int) and isinstance(gid, int):
                        game_ids_by_offer[oid] = gid
                    gslug = game.get("slug")
                    cslug = category.get("slug")
                    if gslug and cslug and not category_url:
                        category_url = f"https://starvell.com/{gslug}/{cslug}"
            enriched_lots = []
            for lot in lots_current or []:
                if isinstance(lot.get("id"), int) and lot["id"] in category_id_by_offer:
                    new_lot = dict(lot)
                    new_lot["category_id"] = category_id_by_offer[lot["id"]]
                    enriched_lots.append(new_lot)
                else:
                    enriched_lots.append(lot)
            game_to_categories_now: dict[int, set[int]] = {}
            for lot in enriched_lots:
                oid = lot.get("id")
                cid = lot.get("category_id")
                gid = game_ids_by_offer.get(oid)
                if isinstance(gid, int) and isinstance(cid, int):
                    game_to_categories_now.setdefault(gid, set()).add(cid)
            tasks = []
            for game_id, categories in game_to_categories_now.items():
                if categories:
                    tasks.append(bump_categories(session_cookie, sid_cookie, game_id, sorted(categories), category_url))
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                category_to_bump: dict[int, dict] = {}
                for r in results:
                    if isinstance(r, Exception):
                        continue
                    req = (r or {}).get("request") or {}
                    resp = (r or {}).get("response") or {}
                    cat_ids = req.get("categoryIds") or []
                    for cid in cat_ids:
                        category_to_bump[cid] = resp
                updated_lots = []
                for lot in enriched_lots:
                    cid = lot.get("category_id")
                    if isinstance(cid, int) and cid in category_to_bump:
                        nl = dict(lot)
                        nl["bump"] = category_to_bump[cid]
                        updated_lots.append(nl)
                        try:
                            success = bool((category_to_bump[cid] or {}).get("success"))
                            if success:
                                await send_bump_notification(nl, True)
                        except Exception:
                            pass
                    else:
                        updated_lots.append(lot)
                cfg2 = load_config()
                if cfg2.get("DEBUG", True):
                    logging.getLogger("exfador.monitor").info(
                        json.dumps({"lots": updated_lots, "category_url": category_url}, ensure_ascii=False, indent=4)
                    )
        except Exception as exc:
            logging.getLogger("exfador.monitor").warning(f"bump_loop_failed error={exc}")
        await asyncio.sleep(1800)


async def _check_chats(
    session_cookie: str,
    db,
    seen_messages: dict[str, set[str]] | None = None,
    user_id=None,
) -> int | str | None:
    try:
        data = await fetch_chats(session_cookie)
    except Exception as exc:
        logging.getLogger("exfador.monitor").warning(f"chat_fetch_failed error={exc}")
        return user_id
    page_props = data.get("pageProps", {})
    chats = page_props.get("chats", [])
    user = page_props.get("user") or {}
    fetched_user_id = user.get("id")
    if fetched_user_id is not None:
        user_id = fetched_user_id
    user_id_norm = _normalize_id(user_id)
    for chat in chats:
        chat_id = chat.get("id")
        if not chat_id:
            continue
        unread = chat.get("unreadMessageCount", 0)
        last_message = chat.get("lastMessage") or {}
        msg_id = last_message.get("id")
        metadata = last_message.get("metadata") or {}
        if not msg_id or metadata.get("isAuto"):
            continue
        processed_for_chat = seen_messages.setdefault(chat_id, set()) if seen_messages is not None else None
        if processed_for_chat is not None and msg_id in processed_for_chat:
            continue
        participants = chat.get("participants") or []
        other_username = ""
        for participant in participants:
            participant_id_norm = _normalize_id(participant.get("id"))
            if user_id_norm and participant_id_norm == user_id_norm:
                continue
            username_candidate = participant.get("username") or ""
            if username_candidate:
                other_username = username_candidate
                break
        if not other_username and participants:
            other_username = participants[0].get("username") or ""
        safe_username = other_username or "Unknown"
        stored = await db.get_last_notified_message(chat_id)
        to_notify: list[tuple[str, str]] = []
        last_msg_author_norm = None
        last_msg_from_self = False
        if stored is None:
            if msg_id:
                try:
                    await db.set_last_notified_message(chat_id, msg_id)
                except Exception:
                    pass
            continue
        try:
            limit = max(unread, 50) if stored else max(unread, 20)
            messages = await fetch_chat_messages(session_cookie, chat_id, limit=limit)
            new_items: list[tuple[str, str]] = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                if not mid:
                    continue
                if stored and mid == stored:
                    break
                metadata = msg.get("metadata") or {}
                if metadata.get("isAuto"):
                    continue
                if processed_for_chat is not None and mid in processed_for_chat:
                    continue
                author_id = msg.get("authorId")
                if author_id is None:
                    author = msg.get("author") or {}
                    author_id = author.get("id")
                author_id_norm = _normalize_id(author_id)
                if mid == msg_id and author_id_norm is not None:
                    last_msg_author_norm = author_id_norm
                if author_id_norm and user_id_norm and author_id_norm == user_id_norm:
                    if mid == msg_id:
                        last_msg_from_self = True
                    continue
                content_text = msg.get("content") or ""
                if (content_text or "").strip() == "":
                    continue
                new_items.append((mid, content_text))
            to_notify = list(reversed(new_items))
        except Exception as exc:
            logging.getLogger("exfador.monitor").warning(f"chat_messages_fetch_failed chat_id={chat_id} error={exc}")
            content = last_message.get("content") or ""
            to_notify = [(msg_id, content)]
        last_author_id = last_message.get("authorId")
        if last_author_id is None:
            last_author_data = last_message.get("author") or {}
            last_author_id = last_author_data.get("id")
        last_author_id_norm = _normalize_id(last_author_id)
        if last_msg_author_norm is not None:
            last_author_id_norm = last_msg_author_norm
        if last_msg_from_self:
            last_author_id_norm = user_id_norm
        if stored is None and not to_notify and (user_id_norm is None or last_author_id_norm != user_id_norm):
            content = (last_message.get("content") or "").strip()
            if content:
                to_notify = [(msg_id, content)]
        if not to_notify and stored != msg_id and (user_id_norm is None or last_author_id_norm != user_id_norm):
            content = (last_message.get("content") or "").strip()
            if content:
                to_notify = [(msg_id, content)]
        for mid, text in to_notify:
            if stored == mid:
                continue
            snippet = (text or "").strip()
            if len(snippet) > 500:
                snippet = snippet[:497] + "..."
            safe_text = snippet or "(empty)"
            if not safe_text or safe_text == "(empty)":
                continue
            try:
                logging.getLogger("exfador.pretty.chat").info(f"📩 Новое сообщение от {safe_username}: {safe_text}")
                await send_chat_notification(safe_username, safe_text, chat_id)
                await db.set_last_notified_message(chat_id, mid)
                if processed_for_chat is not None:
                    processed_for_chat.add(mid)
            except Exception as exc:
                logging.getLogger("exfador.monitor").warning(f"chat_notify_failed chat_id={chat_id} msg_id={mid} error={exc}")
    return user_id


async def _check_orders(session_cookie: str, db) -> None:
    try:
        data = await fetch_sells(session_cookie)
    except Exception as exc:
        logging.getLogger("exfador.monitor").warning(f"orders_fetch_failed error={exc}")
        return
    page_props = data.get("pageProps", {})
    orders = page_props.get("orders", [])
    for order in orders:
        try:
            if not isinstance(order, dict):
                continue
            order_id = order.get("id")
            status = order.get("status")
            if not order_id or status not in ("CREATED",):
                continue
            notified = await db.is_order_notified(order_id)
            if notified:
                continue
            await send_order_notification(order)
            try:
                user = order.get("user") or {}
                buyer = user.get("username") or str(user.get("id") or "-")
                total_price = order.get("totalPrice") or order.get("basePrice") or 0
                offer = order.get("offerDetails") or {}
                game = (offer.get("game") or {}).get("name") or "-"
                category = (offer.get("category") or {}).get("name") or "-"
                logging.getLogger("exfador.pretty.order").info(
                    f"🛒 Новый заказ {order_id} | {buyer} | {game} / {category} | {total_price} ₽"
                )
            except Exception:
                pass
            await db.mark_order_notified(order_id)
            cfg3 = load_config()
            if cfg3.get("DEBUG", True):
                logging.getLogger("exfador.monitor").info(
                    json.dumps(
                        {
                            "order_id": order_id,
                            "status": status,
                            "notified": True,
                        },
                        ensure_ascii=False,
                    )
                )
        except Exception as exc:
            logging.getLogger("exfador.monitor").warning(f"order_notify_failed order_id={order.get('id')} error={exc}")

    for order in orders:
        try:
            if not isinstance(order, dict):
                continue
            order_id = order.get("id")
            status = order.get("status") or ""
            if not order_id or status == "":
                continue
            prev = await db.get_order_status(order_id)
            if prev is None:
                # todo, думаю потом мб улучшить
                await db.set_order_status(order_id, status)
                continue
            if prev != status:
                await db.set_order_status(order_id, status)
                if status == "COMPLETED":
                    await send_order_completed_notification(order)
                    try:
                        user = order.get("user") or {}
                        buyer = user.get("username") or str(user.get("id") or "-")
                        offer = order.get("offerDetails") or {}
                        game = (offer.get("game") or {}).get("name") or "-"
                        category = (offer.get("category") or {}).get("name") or "-"
                        logging.getLogger("exfador.pretty.order").info(
                            f"✅ Заказ завершён {order_id} | {buyer} | {game} / {category}"
                        )
                        # можно еще прологировать дейсвтия возврата заказов, но хз
                    except Exception:
                        pass
        except Exception as exc:
            logging.getLogger("exfador.monitor").warning(f"order_complete_check_failed order_id={order.get('id')} error={exc}")


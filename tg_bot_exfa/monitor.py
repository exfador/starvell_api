import asyncio
import json
import hashlib
from typing import Any
import logging
import time

from api.auth import fetch_homepage_data
from api.find_lots_user import find_user_lots
from api.offer_details import fetch_offer_detail
from api.bump import bump_categories
from api.chats import fetch_chats
from api.messages import fetch_chat_messages
from api.orders import fetch_sells
from api.send_message import send_chat_message
from tg_bot_exfa.notify import send_order_notification
from tg_bot_exfa.notify import send_auth_notification, send_bump_notification
from tg_bot_exfa.notify import send_chat_notification, send_order_completed_notification
from tg_bot_exfa.notify import sync_digest_view
import tg_bot_exfa.app as app
import requests
from version import VERSION
from tg_bot_exfa.notify import send_update_available
from tg_bot_exfa.plugins import PluginContext
from api.rate_limiter import throttle_sync


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
        asyncio.create_task(_version_poll_loop(interval=300))
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
    lots_data = await find_user_lots(session_cookie, sid_cookie, user_id)
    lots = (lots_data or {}).get("lots") or []
    my_games_cookie = (lots_data or {}).get("my_games")
    category_url = None
    category_id_by_offer: dict[int, int] = {}
    game_ids_by_offer: dict[int, int] = {}
    if lots:
        for lot in lots:
            oid = lot.get("id")
            if not category_url:
                cu = lot.get("category_url")
                if isinstance(cu, str) and cu.strip():
                    category_url = cu.strip()
            if not isinstance(oid, int):
                continue
            cid = lot.get("category_id")
            gid = lot.get("game_id")
            if isinstance(cid, int):
                category_id_by_offer[oid] = cid
            if isinstance(gid, int):
                game_ids_by_offer[oid] = gid

        need_details = [
            lot
            for lot in lots
            if isinstance(lot.get("id"), int)
            and (lot["id"] not in category_id_by_offer or lot["id"] not in game_ids_by_offer)
        ]
        if need_details:
            tasks = [fetch_offer_detail(session_cookie, lot.get("id"), sid_cookie, my_games_cookie=my_games_cookie) for lot in need_details]
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
                    category_url = f"https://starvell.com/{gslug}/{cslug}/trade"
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
        gid = game_ids_by_offer.get(oid) or lot.get("game_id")
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
    asyncio.create_task(_version_poll_loop(interval=300))
    if game_to_categories:
        await _run_bump_loop(
            session_cookie,
            sid_cookie,
            game_to_categories,
            category_url,
            enriched_lots,
            auth.get("user"),
            db,
            my_games_cookie=my_games_cookie,
        )


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
            throttle_sync()
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
                    throttle_sync()
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
            throttle_sync()
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


async def _version_poll_loop(interval: float = 300) -> None:
    log = logging.getLogger("exfador.monitor")
    last_notified: str | None = None
    while True:
        try:
            throttle_sync()
            resp = requests.get(
                "https://api.github.com/repos/exfador/starvell_api/tags?page=1",
                headers={"accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                timeout=10,
            )
            if resp.status_code == 200:
                arr = resp.json() or []
                tag_item = None
                for it in arr:
                    name = str((it or {}).get("name") or "").strip()
                    if name and name.lower() != "api":
                        tag_item = it
                        break
                if tag_item:
                    name = str(tag_item.get("name") or "").strip()
                    if name and name != VERSION:
                        key = f"ver:{name}"
                        try:
                            db = app.app_context.db if app.app_context else None
                            should_send = True
                            if db:
                                if await db.has_digest_sent(key):
                                    should_send = False
                            if should_send and last_notified != name:
                                await send_update_available(name, VERSION)
                                if db:
                                    try:
                                        await db.mark_digest_sent(key)
                                    except Exception:
                                        pass
                                last_notified = name
                        except Exception:
                            pass
            else:
                log.debug(f"version_poll_http status={resp.status_code}")
        except Exception as exc:
            log.warning(f"version_poll_failed error={exc}")
        await asyncio.sleep(max(10, float(interval)))


async def _run_bump_loop(
    session_cookie: str,
    sid_cookie: str,
    game_to_categories: dict[int, set[int]],
    referer: str | None,
    lots: list[dict],
    user_obj: dict | None,
    db,
    my_games_cookie: str | None = None,
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
            lots_data = await find_user_lots(session_cookie, sid_cookie, user_id, my_games_cookie=my_games_cookie)
            lots_current = (lots_data or {}).get("lots") or []
            my_games_cookie = (lots_data or {}).get("my_games") or my_games_cookie
            category_url = None
            category_id_by_offer: dict[int, int] = {}
            game_ids_by_offer: dict[int, int] = {}
            if lots_current:
                for lot in lots_current:
                    oid = lot.get("id")
                    if not category_url:
                        cu = lot.get("category_url")
                        if isinstance(cu, str) and cu.strip():
                            category_url = cu.strip()
                    if not isinstance(oid, int):
                        continue
                    cid = lot.get("category_id")
                    gid = lot.get("game_id")
                    if isinstance(cid, int):
                        category_id_by_offer[oid] = cid
                    if isinstance(gid, int):
                        game_ids_by_offer[oid] = gid

                need_details = [
                    lot
                    for lot in lots_current
                    if isinstance(lot.get("id"), int)
                    and (lot["id"] not in category_id_by_offer or lot["id"] not in game_ids_by_offer)
                ]
                if need_details:
                    tasks_details = [
                        fetch_offer_detail(session_cookie, lot.get("id"), sid_cookie, my_games_cookie=my_games_cookie)
                        for lot in need_details
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
                            category_url = f"https://starvell.com/{gslug}/{cslug}/trade"

            if not category_url and referer:
                category_url = referer
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
                gid = game_ids_by_offer.get(oid) or lot.get("game_id")
                if isinstance(gid, int) and isinstance(cid, int):
                    game_to_categories_now.setdefault(gid, set()).add(cid)
            tasks = []
            for game_id, categories in game_to_categories_now.items():
                if categories:
                    if cfg.get("DEBUG", True):
                        logging.getLogger("exfador.monitor").info(
                            json.dumps(
                                {
                                    "bump_request": {
                                        "gameId": game_id,
                                        "categoryIds": sorted(categories),
                                        "referer": category_url,
                                        "my_games": my_games_cookie,
                                    }
                                },
                                ensure_ascii=False,
                            )
                        )
                    tasks.append(
                        bump_categories(
                            session_cookie,
                            sid_cookie,
                            game_id,
                            sorted(categories),
                            category_url,
                            my_games_cookie=my_games_cookie,
                        )
                    )
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                if cfg.get("DEBUG", True):
                    try:
                        short = []
                        for r in results:
                            if isinstance(r, Exception):
                                short.append({"error": str(r)})
                                continue
                            resp = (r or {}).get("response") or {}
                            req = (r or {}).get("request") or {}
                            short.append(
                                {
                                    "gameId": req.get("gameId"),
                                    "categoryIds": req.get("categoryIds"),
                                    "success": bool(resp.get("success")),
                                    "status": resp.get("status"),
                                }
                            )
                        logging.getLogger("exfador.monitor").info(
                            json.dumps({"bump_results": short}, ensure_ascii=False)
                        )
                    except Exception:
                        pass
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
    def _image_preview_url(img: dict) -> str | None:
        try:
            img_id = str((img or {}).get("id") or "").strip()
            ext = str((img or {}).get("extension") or "png").strip().lstrip(".")
            if not img_id:
                return None
            return f"https://cdn.starvell.com/messages/{img_id}-preview.{ext or 'png'}"
        except Exception:
            return None

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

    cfg_now = load_config()
    welcome_enabled = bool(cfg_now.get("WELCOME_ENABLED", True))
    welcome_text_raw = str(
        cfg_now.get(
            "WELCOME_TEXT",
            "CXH BOT это автоматический бот по заказам / cообщения с сайта starvell, наш бот может многое",
        )
        or "CXH BOT это автоматический бот по заказам / cообщения с сайта starvell, наш бот может многое"
    )
    try:
        welcome_cooldown_minutes = int(cfg_now.get("WELCOME_COOLDOWN_MINUTES", 1900))
    except Exception:
        welcome_cooldown_minutes = 1900
    welcome_cooldown_seconds = max(0, welcome_cooldown_minutes) * 60
    try:
        wm_on_global = bool(cfg_now.get("WATERMARK_ON", True))
        wm_text_global = str(cfg_now.get("WATERMARK_TEXT", "[CXH BOT]"))
    except Exception:
        wm_on_global = True
        wm_text_global = "[CXH BOT]"

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
        participants_map: list[tuple[str | None, str]] = []
        for participant in participants:
            participant_id_norm = _normalize_id(participant.get("id"))
            username_candidate = participant.get("username") or ""
            participants_map.append((participant_id_norm, username_candidate))
            if user_id_norm and participant_id_norm == user_id_norm:
                continue
            if username_candidate:
                other_username = username_candidate
        if not other_username and participants:
            other_username = participants[0].get("username") or ""
        stored = await db.get_last_notified_message(chat_id)
        to_notify: list[dict] = []
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
            new_items: list[dict] = []
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
                content_text = (msg.get("content") or "").strip()
                images = msg.get("images") or []
                image_url = None
                if isinstance(images, list) and images:
                    for im in images:
                        if isinstance(im, dict):
                            image_url = _image_preview_url(im)
                            if image_url:
                                break
                if not content_text and not image_url:
                    continue
                text_for_notify = content_text if content_text else "📷 Фото"
                new_items.append({"id": mid, "text": text_for_notify, "image_url": image_url})
            to_notify = list(reversed(new_items))
        except Exception as exc:
            logging.getLogger("exfador.monitor").warning(f"chat_messages_fetch_failed chat_id={chat_id} error={exc}")
            content = (last_message.get("content") or "").strip()
            image_url = None
            try:
                lm_images = (last_message.get("images") or [])
                if isinstance(lm_images, list) and lm_images:
                    for im in lm_images:
                        if isinstance(im, dict):
                            image_url = _image_preview_url(im)
                            if image_url:
                                break
            except Exception:
                image_url = None
            if content or image_url:
                to_notify = [{"id": msg_id, "text": content or "📷 Фото", "image_url": image_url}]
            else:
                to_notify = []
        last_author_id = last_message.get("authorId")
        if last_author_id is None:
            last_author_data = last_message.get("author") or {}
            last_author_id = last_author_data.get("id")
        last_author_id_norm = _normalize_id(last_author_id)
        if last_msg_author_norm is not None:
            last_author_id_norm = last_msg_author_norm
        if last_msg_from_self:
            last_author_id_norm = user_id_norm

        safe_username = (other_username or "Unknown") if other_username else "Unknown"
        if last_author_id_norm:
            for pid_norm, pun in participants_map:
                if pid_norm and pid_norm == last_author_id_norm:
                    safe_username = pun or safe_username
                    break
        if stored is None and not to_notify and (user_id_norm is None or last_author_id_norm != user_id_norm):
            content = (last_message.get("content") or "").strip()
            if content:
                to_notify = [(msg_id, content)]
        if not to_notify and stored != msg_id and (user_id_norm is None or last_author_id_norm != user_id_norm):
            content = (last_message.get("content") or "").strip()
            if content:
                to_notify = [(msg_id, content)]

        if not to_notify:
            continue

        last_user_ts: int | None = None
        if welcome_enabled and welcome_cooldown_seconds > 0:
            try:
                last_user_ts = await db.get_chat_last_user_message_at(chat_id)
            except Exception:
                last_user_ts = None

        for item in to_notify:
            mid = item.get("id")
            text = item.get("text") or ""
            image_url = item.get("image_url")
            if not mid or stored == mid:
                continue
            snippet = (text or "").strip()
            if len(snippet) > 500:
                snippet = snippet[:497] + "..."
            safe_text = snippet or "(empty)"
            if not safe_text or safe_text == "(empty)":
                continue
            try:
                kind = "📷" if image_url else "📩"
                logging.getLogger("exfador.pretty.chat").info(f"{kind} Новое сообщение от {safe_username}: {safe_text}")

                if welcome_enabled and welcome_cooldown_seconds > 0:
                    now_ts = int(time.time())
                    should_send_welcome = False
                    if last_user_ts is None or now_ts - last_user_ts >= welcome_cooldown_seconds:
                        should_send_welcome = True
                        last_user_ts = now_ts
                    if should_send_welcome:
                        try:
                            welcome_payload = (
                                f"{wm_text_global}\n\n{welcome_text_raw}" if wm_on_global else welcome_text_raw
                            )
                            await send_chat_message(session_cookie, chat_id, welcome_payload)
                        except Exception as exc_w:
                            logging.getLogger("exfador.monitor").warning(
                                f"welcome_send_failed chat_id={chat_id} error={exc_w}"
                            )

                await send_chat_notification(safe_username, safe_text, chat_id, image_url=image_url)
                await db.set_last_notified_message(chat_id, mid)
                if processed_for_chat is not None:
                    processed_for_chat.add(mid)
                try:
                    cfg_now_inner = load_config()
                    ctx = PluginContext(session_cookie=session_cookie, db=db, config=cfg_now_inner)
                    pm = app.app_context.plugin_manager if app.app_context else None
                    if pm:
                        await pm.dispatch_chat_message(safe_text, chat_id, ctx)
                except Exception:
                    pass
            except Exception as exc:
                logging.getLogger("exfador.monitor").warning(
                    f"chat_notify_failed chat_id={chat_id} msg_id={mid} error={exc}"
                )

        if welcome_enabled and welcome_cooldown_seconds > 0 and last_user_ts is not None:
            try:
                await db.set_chat_last_user_message_at(chat_id, last_user_ts)
            except Exception:
                pass
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
            try:
                cfg_now = load_config()
                ctx = PluginContext(session_cookie=session_cookie, db=db, config=cfg_now)
                pm = app.app_context.plugin_manager if app.app_context else None
                if pm:
                    await pm.dispatch_order_created(order, ctx)
            except Exception:
                pass
            try:
                offer = order.get("offerDetails") or {}
                offer_obj = offer.get("offer") or {}
                desc_rus = ((offer.get("descriptions") or {}).get("rus") or {})
                name = (
                    str(desc_rus.get("briefDescription") or "").strip()
                    or str(desc_rus.get("description") or "").strip()
                    or str(offer_obj.get("name") or "").strip()
                    or str(offer.get("name") or "").strip()
                    or str(offer.get("title") or "").strip()
                )
                ad_tuple = None
                codes: list[str] = []
                qty = int(order.get("quantity") or 1)
                if name:
                    for _ in range(max(1, qty)):
                        code = await db.pop_autodelivery_item(name)
                        if not code:
                            break
                        codes.append(code)
                    if codes:
                        joined = "\n".join(codes)
                        ad_tuple = (name, joined)
                        try:
                            buyer = (order.get("user") or {}).get("id")
                            if buyer:
                                chats_data = await fetch_chats(session_cookie)
                                page_props = chats_data.get("pageProps", {}) if isinstance(chats_data, dict) else {}
                                chats = page_props.get("chats", [])
                                chat_id = None
                                for ch in chats:
                                    parts = ch.get("participants") or []
                                    for p in parts:
                                        if (p or {}).get("id") == buyer:
                                            chat_id = ch.get("id")
                                            break
                                    if chat_id:
                                        break
                                if chat_id:
                                    from api.send_message import send_chat_message
                                    try:
                                        cfg_loc = load_config()
                                        wm_on = bool(cfg_loc.get("WATERMARK_ON", True))
                                        wm_text = str(cfg_loc.get("WATERMARK_TEXT", "[CXH BOT]"))
                                    except Exception:
                                        wm_on = True
                                        wm_text = "[CXH BOT]"
                                    payload_text = f"{wm_text}\n\n{joined}" if wm_on else joined
                                    await send_chat_message(session_cookie, chat_id, payload_text)
                        except Exception:
                            pass
                await send_order_notification(order, ad_tuple)
            except Exception:
                try:
                    await send_order_notification(order, None)
                except Exception:
                    pass
            try:
                user = order.get("user") or {}
                buyer = user.get("username") or str(user.get("id") or "-")
                total_price = order.get("basePrice") or order.get("totalPrice") or 0
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
                    except Exception:
                        pass
        except Exception as exc:
            logging.getLogger("exfador.monitor").warning(f"order_complete_check_failed order_id={order.get('id')} error={exc}")



import asyncio
import json
import hashlib
import math
from typing import Any
import logging
import time
import os

from api.auth import fetch_homepage_data
from api.find_lots_user import find_user_lots
from api.offer_details import fetch_offer_detail, offer_context
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
from api.response import StarvellResponseError, page_props
from tg_bot_exfa.paths import CONFIG_PATH
from tg_bot_exfa.template_renderer import render_template


_orders_check_lock = asyncio.Lock()


class AutodeliveryPending(RuntimeError):
    pass


def _safe_interval(value: Any, default: float, minimum: float = 1.0, maximum: float = 3600.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return min(maximum, max(minimum, parsed))


async def _fetch_offer_details_bounded(
    session_cookie: str,
    lots: list[dict],
    sid_cookie: str,
    my_games_cookie: str | None,
    batch_size: int = 5,
) -> list[Any]:
    results: list[Any] = []
    safe_batch_size = max(1, min(10, int(batch_size)))
    for offset in range(0, len(lots), safe_batch_size):
        batch = lots[offset : offset + safe_batch_size]
        tasks = [
            fetch_offer_detail(
                session_cookie,
                lot["id"],
                sid_cookie,
                my_games_cookie=my_games_cookie,
            )
            for lot in batch
        ]
        results.extend(await asyncio.gather(*tasks, return_exceptions=True))
    return results


def _normalize_id(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, str)):
        return str(value).strip()
    return None


def _seen_bucket(seen_messages: dict[str, set[str]], chat_id: str, max_chats: int = 500) -> set[str]:
    if chat_id not in seen_messages and len(seen_messages) >= max_chats:
        seen_messages.clear()
    return seen_messages.setdefault(chat_id, set())


def _remember_seen(bucket: set[str], message_id: str, max_messages: int = 500) -> None:
    if len(bucket) >= max_messages:
        bucket.clear()
    bucket.add(message_id)


def load_config() -> dict:
    data: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            loaded = json.load(f) or {}
        if not isinstance(loaded, dict):
            raise ValueError("Configuration root must be a JSON object")
        data.update(loaded)
    if os.getenv("SESSION_COOKIE"):
        data["SESSION_COOKIE"] = os.environ["SESSION_COOKIE"]
    return data


async def start_monitor() -> None:
    while True:
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(_version_poll_loop(interval=300))
                tasks.create_task(_monitor_supervisor_loop())
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("monitor crashed; restarting")
        await asyncio.sleep(5)


async def _monitor_supervisor_loop(retry_interval: float = 60) -> None:
    log = logging.getLogger("exfador.monitor")
    while True:
        try:
            await _monitor_once_and_loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("monitor_iteration_failed")
        await asyncio.sleep(max(5, float(retry_interval)))


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
    username = auth["user"].get("username") or auth["user"].get("login")
    sid_cookie = auth.get("sid") or ""
    try:
        await send_auth_notification(True, auth.get("user"))
    except Exception:
        pass
    lots_data = await find_user_lots(session_cookie, sid_cookie, user_id, username=username)
    lots = (lots_data or {}).get("lots") or []
    my_games_cookie = (lots_data or {}).get("my_games")
    category_url = None
    category_id_by_offer: dict[str, int] = {}
    game_ids_by_offer: dict[str, int] = {}
    if lots:
        for lot in lots:
            oid = _normalize_id(lot.get("id"))
            if not category_url:
                cu = lot.get("category_url")
                if isinstance(cu, str) and cu.strip():
                    category_url = cu.strip()
            if not oid:
                continue
            cid = lot.get("category_id")
            gid = lot.get("game_id")
            if isinstance(cid, int):
                category_id_by_offer[oid] = cid
            if isinstance(gid, int):
                game_ids_by_offer[oid] = gid

        need_details = []
        for lot in lots:
            offer_key = _normalize_id(lot.get("id"))
            if offer_key and (
                offer_key not in category_id_by_offer or offer_key not in game_ids_by_offer
            ):
                need_details.append(lot)
        if need_details:
            details = await _fetch_offer_details_bounded(
                session_cookie,
                need_details,
                sid_cookie,
                my_games_cookie,
            )
            for d in details:
                if isinstance(d, Exception):
                    continue
                try:
                    offer, game, category = offer_context(d)
                except StarvellResponseError:
                    continue
                oid = _normalize_id(offer.get("publicId") or offer.get("id"))
                cid = None
                if isinstance(category.get("id"), int):
                    cid = category.get("id")
                elif isinstance(offer.get("categoryId"), int):
                    cid = offer.get("categoryId")
                if oid and isinstance(cid, int):
                    category_id_by_offer[oid] = cid
                gid = None
                if isinstance(offer.get("gameId"), int):
                    gid = offer.get("gameId")
                elif isinstance(game.get("id"), int):
                    gid = game.get("id")
                if oid and isinstance(gid, int):
                    game_ids_by_offer[oid] = gid
                gslug = game.get("slug")
                cslug = category.get("slug")
                if gslug and cslug and not category_url:
                    category_url = f"https://starvell.com/{gslug}/{cslug}/trade"
    enriched_lots = []
    for lot in lots:
        offer_key = _normalize_id(lot.get("id"))
        if offer_key and offer_key in category_id_by_offer:
            new_lot = dict(lot)
            new_lot["category_id"] = category_id_by_offer[offer_key]
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
    poll_interval = _safe_interval(cfg.get("CHAT_POLL_INTERVAL"), 5)
    orders_interval = _safe_interval(cfg.get("ORDERS_POLL_INTERVAL"), 10)
    announce_interval = _safe_interval(cfg.get("REMOTE_INFO_INTERVAL"), 120, minimum=30)
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_chat_poll_loop(db, user_id=user_id, interval=poll_interval))
        tasks.create_task(_orders_poll_loop(db, interval=orders_interval))
        tasks.create_task(_remote_poll_loop(interval=announce_interval))
        if game_to_categories:
            tasks.create_task(
                _run_bump_loop(
                    session_cookie,
                    sid_cookie,
                    game_to_categories,
                    category_url,
                    enriched_lots,
                    auth.get("user"),
                    db,
                    my_games_cookie=my_games_cookie,
                )
            )


async def _chat_poll_loop(db, user_id, interval: float = 30) -> None:
    log = logging.getLogger("exfador.monitor")
    interval = _safe_interval(interval, 30)
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
    interval = _safe_interval(interval, 15)
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
    interval = _safe_interval(interval, 120, minimum=30)
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
            payload = await asyncio.to_thread(read_cxh_descriptor)
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
                        await sync_digest_view(payload, event_id=key)
                        if db and key:
                            try:
                                await db.mark_digest_sent(key)
                            except Exception:
                                pass
                        tag_value = str(payload.get("tag") or "").strip()
                        if tag_value:
                            _last_rev = tag_value
                except Exception:
                    pass
            comments_payloads = await asyncio.to_thread(read_owner_notes)
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
                            await sync_digest_view({"text": text}, event_id=key)
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

    def fetch_tags():
        throttle_sync()
        return requests.get(
            "https://api.github.com/repos/exfador/starvell_api/tags?page=1",
            headers={"accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            timeout=10,
        )

    while True:
        try:
            resp = await asyncio.to_thread(fetch_tags)
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
            username = (auth.get("user") or {}).get("username") or (auth.get("user") or {}).get("login")
            sid_cookie = auth.get("sid") or sid_cookie
            lots_data = await find_user_lots(
                session_cookie,
                sid_cookie,
                user_id,
                username=username,
                my_games_cookie=my_games_cookie,
            )
            lots_current = (lots_data or {}).get("lots") or []
            my_games_cookie = (lots_data or {}).get("my_games") or my_games_cookie
            category_url = None
            category_id_by_offer: dict[str, int] = {}
            game_ids_by_offer: dict[str, int] = {}
            if lots_current:
                for lot in lots_current:
                    oid = _normalize_id(lot.get("id"))
                    if not category_url:
                        cu = lot.get("category_url")
                        if isinstance(cu, str) and cu.strip():
                            category_url = cu.strip()
                    if not oid:
                        continue
                    cid = lot.get("category_id")
                    gid = lot.get("game_id")
                    if isinstance(cid, int):
                        category_id_by_offer[oid] = cid
                    if isinstance(gid, int):
                        game_ids_by_offer[oid] = gid

                need_details = []
                for lot in lots_current:
                    offer_key = _normalize_id(lot.get("id"))
                    if offer_key and (
                        offer_key not in category_id_by_offer or offer_key not in game_ids_by_offer
                    ):
                        need_details.append(lot)
                if need_details:
                    details = await _fetch_offer_details_bounded(
                        session_cookie,
                        need_details,
                        sid_cookie,
                        my_games_cookie,
                    )
                    for d in details:
                        if isinstance(d, Exception):
                            continue
                        try:
                            offer, game, category = offer_context(d)
                        except StarvellResponseError:
                            continue
                        oid = _normalize_id(offer.get("publicId") or offer.get("id"))
                        cid = None
                        if isinstance(category.get("id"), int):
                            cid = category.get("id")
                        elif isinstance(offer.get("categoryId"), int):
                            cid = offer.get("categoryId")
                        if oid and isinstance(cid, int):
                            category_id_by_offer[oid] = cid
                        gid = None
                        if isinstance(offer.get("gameId"), int):
                            gid = offer.get("gameId")
                        elif isinstance(game.get("id"), int):
                            gid = game.get("id")
                        if oid and isinstance(gid, int):
                            game_ids_by_offer[oid] = gid
                        gslug = game.get("slug")
                        cslug = category.get("slug")
                        if gslug and cslug and not category_url:
                            category_url = f"https://starvell.com/{gslug}/{cslug}/trade"

            if not category_url and referer:
                category_url = referer
            enriched_lots = []
            for lot in lots_current or []:
                offer_key = _normalize_id(lot.get("id"))
                if offer_key and offer_key in category_id_by_offer:
                    new_lot = dict(lot)
                    new_lot["category_id"] = category_id_by_offer[offer_key]
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
    props = page_props(data, "check chats")
    chats = props.get("chats", [])
    user = props.get("user") or {}
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
        try:
            unread = max(0, int(chat.get("unreadMessageCount", 0) or 0))
        except (TypeError, ValueError):
            unread = 0
        last_message = chat.get("lastMessage") or {}
        msg_id = last_message.get("id")
        if msg_id is not None:
            msg_id = str(msg_id)
        metadata = last_message.get("metadata") or {}
        if not msg_id:
            continue
        processed_for_chat = _seen_bucket(seen_messages, chat_id) if seen_messages is not None else None
        stored = await db.get_last_notified_message(chat_id)
        if stored is not None:
            stored = str(stored)
        if processed_for_chat is not None and msg_id in processed_for_chat:
            if stored != msg_id:
                await db.set_last_notified_message(chat_id, msg_id)
            continue
        participants = chat.get("participants") or []
        other_username = ""
        interlocutor_id: int | None = None
        participants_map: list[tuple[str | None, str]] = []
        for participant in participants:
            participant_id_norm = _normalize_id(participant.get("id"))
            username_candidate = participant.get("username") or ""
            participants_map.append((participant_id_norm, username_candidate))
            if user_id_norm and participant_id_norm == user_id_norm:
                continue
            if username_candidate:
                other_username = username_candidate
            raw_pid = participant.get("id")
            if isinstance(raw_pid, int) and interlocutor_id is None:
                interlocutor_id = raw_pid
        if not other_username and participants:
            other_username = participants[0].get("username") or ""
        to_notify: list[dict] = []
        last_msg_author_norm = None
        last_msg_from_self = False
        if stored is None and unread <= 0:
            if msg_id:
                try:
                    await db.set_last_notified_message(chat_id, msg_id)
                except Exception:
                    pass
            if processed_for_chat is not None:
                _remember_seen(processed_for_chat, msg_id)
            continue
        if stored is None:
            stored = ""
        if msg_id and stored and msg_id == stored:
            if processed_for_chat is not None:
                _remember_seen(processed_for_chat, msg_id)
            continue
        scan_succeeded = False
        try:
            limit = min(100, max(unread, 50))
            messages = await fetch_chat_messages(session_cookie, chat_id, limit=limit, interlocutor_id=interlocutor_id)
            scan_succeeded = True
            new_items: list[dict] = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                if not mid:
                    continue
                mid = str(mid)
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
                new_items.append({"id": mid, "text": text_for_notify, "image_url": image_url, "author_id": author_id_norm})
            to_notify = list(reversed(new_items))
        except Exception as exc:
            logging.getLogger("exfador.monitor").warning(f"chat_messages_fetch_failed chat_id={chat_id} error={exc}")
            fb_author_id = last_message.get("authorId")
            if fb_author_id is None:
                fb_author_data = last_message.get("author") or {}
                fb_author_id = fb_author_data.get("id")
            fb_author_norm = _normalize_id(fb_author_id)
            if metadata.get("isAuto"):
                to_notify = []
            elif fb_author_norm and user_id_norm and fb_author_norm == user_id_norm:
                to_notify = []
            else:
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
                    skip_plugins = fb_author_norm is None
                    to_notify = [{"id": msg_id, "text": content or "📷 Фото", "image_url": image_url, "author_id": fb_author_norm, "_skip_plugins": skip_plugins}]
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
        if (
            stored is None
            and not metadata.get("isAuto")
            and not to_notify
            and (user_id_norm is None or last_author_id_norm != user_id_norm)
        ):
            content = (last_message.get("content") or "").strip()
            if content:
                to_notify = [{"id": msg_id, "text": content, "author_id": last_author_id_norm}]
        if (
            not metadata.get("isAuto")
            and not to_notify
            and stored != msg_id
            and (user_id_norm is None or last_author_id_norm != user_id_norm)
        ):
            content = (last_message.get("content") or "").strip()
            if content:
                to_notify = [{"id": msg_id, "text": content, "author_id": last_author_id_norm}]

        if not to_notify:
            if scan_succeeded and stored != msg_id:
                await db.set_last_notified_message(chat_id, msg_id)
            if processed_for_chat is not None:
                _remember_seen(processed_for_chat, msg_id)
            continue

        last_user_ts: int | None = None
        if welcome_enabled and welcome_cooldown_seconds > 0:
            try:
                last_user_ts = await db.get_chat_last_user_message_at(chat_id)
            except Exception:
                last_user_ts = None

        all_processed = True
        for item in to_notify:
            mid = item.get("id") if isinstance(item, dict) else None
            if mid is not None:
                mid = str(mid)
            text = (item.get("text") or "") if isinstance(item, dict) else ""
            image_url = item.get("image_url") if isinstance(item, dict) else None
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
                logging.getLogger("exfador.pretty.chat").info(
                    "%s Новое сообщение chat_id=%s sender=%s length=%s",
                    kind,
                    chat_id,
                    safe_username,
                    len(safe_text),
                )

                if welcome_enabled and welcome_cooldown_seconds > 0:
                    now_ts = int(time.time())
                    should_send_welcome = False
                    if last_user_ts is None or now_ts - last_user_ts >= welcome_cooldown_seconds:
                        should_send_welcome = True
                    if should_send_welcome:
                        try:
                            rendered_welcome = render_template(
                                welcome_text_raw,
                                {
                                    "buyer": safe_username,
                                    "chat_id": chat_id,
                                    "message_text": safe_text,
                                },
                            )
                            welcome_payload = (
                                f"{wm_text_global}\n\n{rendered_welcome}"
                                if wm_on_global
                                else rendered_welcome
                            )
                            await send_chat_message(session_cookie, chat_id, welcome_payload)
                            last_user_ts = now_ts
                        except Exception as exc_w:
                            logging.getLogger("exfador.monitor").warning(
                                f"welcome_send_failed chat_id={chat_id} error={exc_w}"
                            )

                try:
                    await send_chat_notification(
                        safe_username,
                        safe_text,
                        chat_id,
                        image_url=image_url,
                        event_id=mid,
                    )
                except Exception as exc_notify:
                    logging.getLogger("exfador.monitor").warning(
                        f"chat_notify_failed chat_id={chat_id} msg_id={mid} error={exc_notify}"
                    )
                    all_processed = False
                    break
                await db.set_last_notified_message(chat_id, mid)
                if processed_for_chat is not None:
                    _remember_seen(processed_for_chat, mid)
                skip_plugins = (item.get("_skip_plugins") if isinstance(item, dict) else False) or False
                if not skip_plugins:
                    try:
                        cfg_now_inner = load_config()
                        ctx = PluginContext(session_cookie=session_cookie, db=db, config=cfg_now_inner)
                        ctx.message_author_id = item.get("author_id") if isinstance(item, dict) else None
                        ctx.user_id = user_id_norm
                        pm = app.app_context.plugin_manager if app.app_context else None
                        if pm:
                            await pm.dispatch_chat_message(safe_text, chat_id, ctx)
                    except Exception:
                        pass
            except Exception as exc:
                logging.getLogger("exfador.monitor").warning(
                    f"chat_message_process_failed chat_id={chat_id} msg_id={mid} error={exc}"
                )
                all_processed = False
                break

        if all_processed and scan_succeeded and stored != msg_id:
            await db.set_last_notified_message(chat_id, msg_id)

        if welcome_enabled and welcome_cooldown_seconds > 0 and last_user_ts is not None:
            try:
                await db.set_chat_last_user_message_at(chat_id, last_user_ts)
            except Exception:
                pass
    return user_id


async def _deliver_autodelivery_codes(session_cookie: str, db, order: dict, product_name: str) -> tuple[str, str] | None:
    order_id = _normalize_id(order.get("id"))
    if not order_id:
        raise RuntimeError("autodelivery order id is missing")
    quantity = max(1, int(order.get("quantity") or 1))
    delivery = await db.reserve_order_delivery(order_id, product_name, quantity)
    if delivery is None:
        return None
    state = str(delivery.get("state") or "")
    if state in {"buyer_sent", "owner_notified"}:
        return product_name, "\n".join(delivery.get("item_values") or [])
    if state == "insufficient":
        available = int(delivery.get("available") or 0)
        raise RuntimeError(
            f"autodelivery stock is insufficient: required={quantity}, available={available}"
        )
    if state == "sending":
        joined = "\n".join(delivery.get("item_values") or [])
        delivery_chat_id = _normalize_id(delivery.get("chat_id"))
        if joined and delivery_chat_id:
            try:
                recent = await fetch_chat_messages(
                    session_cookie,
                    delivery_chat_id,
                    limit=20,
                )
                if any(joined in str((message or {}).get("content") or "") for message in recent):
                    await db.mark_order_delivery_sent(order_id)
                    return product_name, joined
            except Exception as exc:
                logging.getLogger("exfador.monitor").warning(
                    "autodelivery_reconcile_failed order_id=%s error=%s",
                    order_id,
                    exc,
                )
        raise AutodeliveryPending(
            "Статус отправки покупателю неоднозначен. Повтор заблокирован; "
            "бот проверяет историю чата."
        )
    if state != "reserved":
        raise RuntimeError(f"unsupported autodelivery state: {state}")

    buyer_id = _normalize_id((order.get("user") or {}).get("id"))
    if not buyer_id:
        raise RuntimeError("autodelivery buyer is missing")

    chats_data = await fetch_chats(session_cookie)
    chats = page_props(chats_data, "deliver autodelivery codes").get("chats") or []
    chat_id = None
    for chat in chats:
        for participant in (chat.get("participants") or []):
            if _normalize_id((participant or {}).get("id")) == buyer_id:
                chat_id = chat.get("id")
                break
        if chat_id:
            break
    if not chat_id:
        raise RuntimeError("autodelivery chat is missing")

    codes = [str(value) for value in delivery.get("item_values") or []]
    if len(codes) != quantity:
        raise RuntimeError("autodelivery reservation has an invalid quantity")
    joined = "\n".join(codes)
    try:
        cfg = load_config()
        watermark_on = bool(cfg.get("WATERMARK_ON", True))
        watermark_text = str(cfg.get("WATERMARK_TEXT", "[CXH BOT]"))
    except Exception:
        watermark_on = True
        watermark_text = "[CXH BOT]"
    payload = f"{watermark_text}\n\n{joined}" if watermark_on else joined
    claimed = await db.mark_order_delivery_sending(order_id, str(chat_id))
    if not claimed:
        current = await db.get_order_delivery(order_id)
        if current and current.get("state") in {"buyer_sent", "owner_notified"}:
            return product_name, "\n".join(current.get("item_values") or [])
        return product_name, (
            "⚠️ Автовыдача уже обрабатывается другим процессом; "
            "повторная отправка заблокирована."
        )
    try:
        await send_chat_message(session_cookie, chat_id, payload)
        await db.mark_order_delivery_sent(order_id)
    except Exception as exc:
        await db.mark_order_delivery_error(order_id, str(exc))
        raise AutodeliveryPending(
            "Не удалось подтвердить отправку покупателю. Коды зарезервированы, "
            "повтор отключён до сверки с историей чата."
        )
    return product_name, joined


async def _check_orders(session_cookie: str, db) -> None:
    async with _orders_check_lock:
        await _check_orders_once(session_cookie, db)


async def _fetch_recent_orders(session_cookie: str, max_pages: int = 3) -> list[dict]:
    orders: list[dict] = []
    seen_ids: set[str] = set()
    for page_number in range(1, max(1, min(10, int(max_pages))) + 1):
        data = await fetch_sells(session_cookie, page=page_number if page_number > 1 else None)
        page_orders = page_props(data, "check orders").get("orders") or []
        if not page_orders:
            break
        added = 0
        for order in page_orders:
            if not isinstance(order, dict):
                continue
            order_id = _normalize_id(order.get("id"))
            if order_id and order_id in seen_ids:
                continue
            if order_id:
                seen_ids.add(order_id)
            orders.append(order)
            added += 1
        if added == 0:
            break
    return orders


async def _check_orders_once(session_cookie: str, db) -> None:
    try:
        orders = await _fetch_recent_orders(session_cookie)
    except Exception as exc:
        logging.getLogger("exfador.monitor").warning(f"orders_fetch_failed error={exc}")
        return
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
                ad_tuple = await _deliver_autodelivery_codes(session_cookie, db, order, name) if name else None
            except AutodeliveryPending as exc:
                try:
                    await send_order_notification(
                        order,
                        (name, f"⚠️ {exc}"),
                        event_id=f"{order_id}:autodelivery-warning",
                    )
                except Exception as notify_error:
                    logging.getLogger("exfador.monitor").warning(
                        "autodelivery_warning_failed order_id=%s error=%s",
                        order_id,
                        notify_error,
                    )
                continue
            except Exception as exc:
                logging.getLogger("exfador.monitor").warning(
                    "autodelivery_failed order_id=%s error=%s",
                    order_id,
                    exc,
                )
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
                await send_order_notification(order, ad_tuple)
            except Exception as notify_error:
                logging.getLogger("exfador.monitor").warning(
                    "order_notification_failed order_id=%s error=%s",
                    order_id,
                    notify_error,
                )
                continue
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
            if ad_tuple is not None:
                await db.mark_order_delivery_owner_notified(order_id)
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
                await db.set_order_status(order_id, status)
        except Exception as exc:
            logging.getLogger("exfador.monitor").warning(f"order_complete_check_failed order_id={order.get('id')} error={exc}")

#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from api.auth import fetch_homepage_data
from api.chats import fetch_chats
from api.find_lots_user import find_user_lots
from api.http_client import close_http_session
from api.messages import fetch_chat_messages
from api.offer_details import fetch_offer_detail, offer_context
from api.orders import fetch_sells_all
from api.response import page_props


async def _measure(name: str, operation: Callable[[], Awaitable[Any]]) -> tuple[Any, dict[str, Any]]:
    started = time.perf_counter()
    try:
        value = await operation()
        return value, {"name": name, "ok": True, "ms": round((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        return None, {
            "name": name,
            "ok": False,
            "ms": round((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def main() -> int:
    session = os.getenv("STARVELL_SESSION", "").strip()
    supplied_sid = os.getenv("STARVELL_SID", "").strip()
    supplied_games = os.getenv("STARVELL_MY_GAMES", "").strip()
    if not session:
        raise SystemExit("STARVELL_SESSION is required")

    checks: list[dict[str, Any]] = []
    try:
        auth, result = await _measure("auth", lambda: fetch_homepage_data(session))
        checks.append(result)
        if not auth or not auth.get("authorized"):
            print(json.dumps({"ok": False, "checks": checks}, ensure_ascii=False, indent=2))
            return 1

        user = auth.get("user") or {}
        user_id = int(user.get("id"))
        sid = str(auth.get("sid") or supplied_sid)
        my_games = str(auth.get("my_games") or supplied_games)

        lots, result = await _measure(
            "lots",
            lambda: find_user_lots(
                session,
                sid,
                user_id,
                username=str(user.get("username") or user.get("login") or user_id),
                my_games_cookie=my_games or None,
            ),
        )
        checks.append(result | {"count": len((lots or {}).get("lots") or [])})

        chats_data, result = await _measure("chats", lambda: fetch_chats(session))
        chats = page_props(chats_data, "live smoke chats").get("chats") if chats_data else []
        chats = chats or []
        checks.append(result | {"count": len(chats)})

        if chats:
            first_chat = chats[0] or {}
            chat_id = first_chat.get("id")
            interlocutor_id = None
            for participant in first_chat.get("participants") or []:
                if str((participant or {}).get("id")) != str(user_id):
                    try:
                        interlocutor_id = int((participant or {}).get("id"))
                    except (TypeError, ValueError):
                        pass
                    break
            messages, result = await _measure(
                "messages",
                lambda: fetch_chat_messages(
                    session,
                    chat_id,
                    limit=10,
                    interlocutor_id=interlocutor_id,
                ),
            )
            checks.append(result | {"count": len(messages or [])})

        orders, result = await _measure("orders", lambda: fetch_sells_all(session, max_pages=3))
        checks.append(result | {"count": len(orders or [])})

        lot_items = (lots or {}).get("lots") or []
        if lot_items:
            first_lot = lot_items[0]
            detail, result = await _measure(
                "offer_detail",
                lambda: fetch_offer_detail(
                    session,
                    first_lot.get("id"),
                    sid,
                    my_games_cookie=my_games or None,
                ),
            )
            if detail:
                offer, game, category = offer_context(detail)
                result["has_offer"] = bool(offer)
                result["has_game"] = bool(game)
                result["has_category"] = bool(category)
            checks.append(result)

        report = {
            "ok": all(check.get("ok") for check in checks),
            "account": str(user.get("username") or user_id),
            "checks": checks,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    finally:
        await close_http_session()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

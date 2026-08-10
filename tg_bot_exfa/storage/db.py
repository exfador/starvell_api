import asyncio
import json
import time
import aiosqlite
import os
from typing import Any


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    language TEXT,
                    failed_attempts INTEGER DEFAULT 0,
                    blocked_until INTEGER DEFAULT 0,
                    notify_auth INTEGER DEFAULT 1,
                    notify_bump INTEGER DEFAULT 1,
                    notify_chat INTEGER DEFAULT 1,
                    notify_orders INTEGER DEFAULT 1,
                    authorized INTEGER DEFAULT 0
                )
                """
            )
            cur = await db.execute("PRAGMA table_info(users)")
            user_columns = {str(row[1]) for row in await cur.fetchall()}
            await cur.close()
            if "notify_chat" not in user_columns:
                await db.execute("ALTER TABLE users ADD COLUMN notify_chat INTEGER DEFAULT 1")
            if "notify_orders" not in user_columns:
                await db.execute("ALTER TABLE users ADD COLUMN notify_orders INTEGER DEFAULT 1")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_last_notified (
                    chat_id TEXT PRIMARY KEY,
                    last_message_id TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_last_user_message (
                    chat_id TEXT PRIMARY KEY,
                    last_at INTEGER DEFAULT 0
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    created_at INTEGER DEFAULT 0
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS orders_notified (
                    order_id TEXT PRIMARY KEY,
                    created_at INTEGER DEFAULT 0
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS orders_status (
                    order_id TEXT PRIMARY KEY,
                    last_status TEXT,
                    updated_at INTEGER DEFAULT 0
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS digest_sent (
                    key TEXT PRIMARY KEY,
                    created_at INTEGER DEFAULT 0
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_receipts (
                    event_kind TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at INTEGER DEFAULT 0,
                    PRIMARY KEY(event_kind, event_id, user_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS autodelivery_products (
                    product TEXT PRIMARY KEY,
                    created_at INTEGER DEFAULT 0
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS autodelivery_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at INTEGER DEFAULT 0,
                    reserved_order_id TEXT
                )
                """
            )
            cur = await db.execute("PRAGMA table_info(autodelivery_items)")
            autodelivery_columns = {str(row[1]) for row in await cur.fetchall()}
            await cur.close()
            if "reserved_order_id" not in autodelivery_columns:
                await db.execute("ALTER TABLE autodelivery_items ADD COLUMN reserved_order_id TEXT")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS order_deliveries (
                    order_id TEXT PRIMARY KEY,
                    product TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    item_ids TEXT NOT NULL,
                    item_values TEXT NOT NULL,
                    state TEXT NOT NULL,
                    chat_id TEXT,
                    error TEXT,
                    updated_at INTEGER DEFAULT 0
                )
                """
            )
            await db.execute(
                "INSERT OR IGNORE INTO autodelivery_products(product, created_at) "
                "SELECT DISTINCT product, ? FROM autodelivery_items",
                (int(time.time()),),
            )
            await db.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    async def prune_runtime_state(
        self,
        retention_days: int = 180,
        *,
        now: int | None = None,
    ) -> dict[str, int]:
        """Bound durable event history without touching pending deliveries or inventory."""

        safe_days = max(30, min(3650, int(retention_days)))
        cutoff = int(time.time() if now is None else now) - safe_days * 86400
        deleted: dict[str, int] = {}
        statements = (
            ("orders_notified", "DELETE FROM orders_notified WHERE created_at < ?", (cutoff,)),
            ("orders_status", "DELETE FROM orders_status WHERE updated_at < ?", (cutoff,)),
            ("digest_sent", "DELETE FROM digest_sent WHERE created_at < ?", (cutoff,)),
            (
                "notification_receipts",
                "DELETE FROM notification_receipts WHERE created_at < ?",
                (cutoff,),
            ),
            (
                "order_deliveries",
                "DELETE FROM order_deliveries WHERE state = 'owner_notified' AND updated_at < ?",
                (cutoff,),
            ),
        )
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    for name, sql, params in statements:
                        cursor = await db.execute(sql, params)
                        deleted[name] = max(0, int(cursor.rowcount or 0))
                        await cursor.close()
                    await db.commit()
                except BaseException:
                    await db.rollback()
                    raise
        return deleted

    async def get_user(self, user_id: int) -> dict[str, Any]:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
                row = await cur.fetchone()
                await cur.close()
                if row is None:
                    await db.execute("INSERT INTO users(user_id) VALUES(?)", (user_id,))
                    await db.commit()
                    cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
                    row = await cur.fetchone()
                    await cur.close()
                return dict(row)

    async def set_language(self, user_id: int, language: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("UPDATE users SET language=? WHERE user_id=?", (language, user_id))
                await db.commit()

    async def increment_failed(self, user_id: int) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("UPDATE users SET failed_attempts=COALESCE(failed_attempts,0)+1 WHERE user_id=?", (user_id,))
                await db.commit()
                cur = await db.execute("SELECT failed_attempts FROM users WHERE user_id=?", (user_id,))
                row = await cur.fetchone()
                await cur.close()
                return int(row[0]) if row else 0

    async def reset_failed(self, user_id: int) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("UPDATE users SET failed_attempts=0 WHERE user_id=?", (user_id,))
                await db.commit()

    async def set_blocked_until(self, user_id: int, timestamp: int) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("UPDATE users SET blocked_until=? WHERE user_id=?", (timestamp, user_id))
                await db.commit()

    async def set_authorized(self, user_id: int, authorized: bool) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("UPDATE users SET authorized=? WHERE user_id=?", (1 if authorized else 0, user_id))
                await db.commit()

    async def revoke_authorizations(self, except_user_id: int | None = None) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                if except_user_id is None:
                    cur = await db.execute("UPDATE users SET authorized=0 WHERE authorized!=0")
                else:
                    cur = await db.execute(
                        "UPDATE users SET authorized=0 WHERE authorized!=0 AND user_id!=?",
                        (except_user_id,),
                    )
                await db.commit()
                return max(0, int(cur.rowcount))

    async def toggle_notify_auth(self, user_id: int) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT notify_auth FROM users WHERE user_id=?", (user_id,))
                row = await cur.fetchone()
                await cur.close()
                val = 0 if (row and row[0]) else 1
                await db.execute("UPDATE users SET notify_auth=? WHERE user_id=?", (val, user_id))
                await db.commit()
                return val

    async def toggle_notify_bump(self, user_id: int) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT notify_bump FROM users WHERE user_id=?", (user_id,))
                row = await cur.fetchone()
                await cur.close()
                val = 0 if (row and row[0]) else 1
                await db.execute("UPDATE users SET notify_bump=? WHERE user_id=?", (val, user_id))
                await db.commit()
                return val

    async def toggle_notify_chat(self, user_id: int) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT notify_chat FROM users WHERE user_id=?", (user_id,))
                row = await cur.fetchone()
                await cur.close()
                val = 0 if (row and row[0]) else 1
                await db.execute("UPDATE users SET notify_chat=? WHERE user_id=?", (val, user_id))
                await db.commit()
                return val

    async def toggle_notify_orders(self, user_id: int) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT notify_orders FROM users WHERE user_id=?", (user_id,))
                row = await cur.fetchone()
                await cur.close()
                val = 0 if (row and row[0]) else 1
                await db.execute("UPDATE users SET notify_orders=? WHERE user_id=?", (val, user_id))
                await db.commit()
                return val

    async def get_last_notified_message(self, chat_id: str) -> str | None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT last_message_id FROM chat_last_notified WHERE chat_id=?", (chat_id,))
                row = await cur.fetchone()
                await cur.close()
                return row[0] if row else None

    async def set_last_notified_message(self, chat_id: str, message_id: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    "INSERT INTO chat_last_notified(chat_id, last_message_id) VALUES(?, ?) ON CONFLICT(chat_id) DO UPDATE SET last_message_id=excluded.last_message_id",
                    (chat_id, message_id),
                )
                await db.commit()

    async def get_chat_last_user_message_at(self, chat_id: str) -> int | None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT last_at FROM chat_last_user_message WHERE chat_id=?", (chat_id,))
                row = await cur.fetchone()
                await cur.close()
                if not row:
                    return None
                try:
                    return int(row[0])
                except Exception:
                    return None

    async def set_chat_last_user_message_at(self, chat_id: str, ts: int) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    "INSERT INTO chat_last_user_message(chat_id, last_at) VALUES(?, ?) "
                    "ON CONFLICT(chat_id) DO UPDATE SET last_at=excluded.last_at",
                    (chat_id, ts),
                )
                await db.commit()

    async def add_template(self, content: str) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                created_at = int(time.time())
                cur = await db.execute(
                    "INSERT INTO templates(content, created_at) VALUES(?, ?)",
                    (content, created_at),
                )
                await db.commit()
                return int(cur.lastrowid)

    async def delete_template(self, template_id: int) -> bool:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("DELETE FROM templates WHERE id=?", (template_id,))
                await db.commit()
                return cur.rowcount > 0

    async def list_templates(self, offset: int = 0, limit: int = 10) -> list[dict[str, Any]]:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT id, content, created_at FROM templates ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
                rows = await cur.fetchall()
                await cur.close()
                return [dict(r) for r in rows]

    async def count_templates(self) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT COUNT(*) FROM templates")
                row = await cur.fetchone()
                await cur.close()
                return int(row[0]) if row else 0

    async def get_template(self, template_id: int) -> dict[str, Any] | None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT id, content, created_at FROM templates WHERE id=?",
                    (template_id,),
                )
                row = await cur.fetchone()
                await cur.close()
                return dict(row) if row else None

    async def is_order_notified(self, order_id: str) -> bool:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT 1 FROM orders_notified WHERE order_id=?", (order_id,))
                row = await cur.fetchone()
                await cur.close()
                return row is not None

    async def mark_order_notified(self, order_id: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                created_at = int(time.time())
                await db.execute(
                    "INSERT INTO orders_notified(order_id, created_at) VALUES(?, ?) ON CONFLICT(order_id) DO NOTHING",
                    (order_id, created_at),
                )
                await db.commit()

    async def get_order_status(self, order_id: str) -> str | None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT last_status FROM orders_status WHERE order_id=?", (order_id,))
                row = await cur.fetchone()
                await cur.close()
                return str(row[0]) if row and row[0] is not None else None

    async def set_order_status(self, order_id: str, status: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                ts = int(time.time())
                await db.execute(
                    "INSERT INTO orders_status(order_id, last_status, updated_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(order_id) DO UPDATE SET last_status=excluded.last_status, updated_at=excluded.updated_at",
                    (order_id, status, ts),
                )
                await db.commit()

    async def has_digest_sent(self, key: str) -> bool:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT 1 FROM digest_sent WHERE key=?", (key,))
                row = await cur.fetchone()
                await cur.close()
                return row is not None

    async def mark_digest_sent(self, key: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                created_at = int(time.time())
                await db.execute(
                    "INSERT INTO digest_sent(key, created_at) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
                    (key, created_at),
                )
                await db.commit()

    async def has_notification_receipt(
        self,
        event_kind: str,
        event_id: str,
        user_id: int,
    ) -> bool:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute(
                    "SELECT 1 FROM notification_receipts "
                    "WHERE event_kind=? AND event_id=? AND user_id=?",
                    (event_kind, event_id, int(user_id)),
                )
                row = await cur.fetchone()
                await cur.close()
                return row is not None

    async def mark_notification_receipt(
        self,
        event_kind: str,
        event_id: str,
        user_id: int,
    ) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    "INSERT INTO notification_receipts(event_kind, event_id, user_id, created_at) "
                    "VALUES(?, ?, ?, ?) ON CONFLICT(event_kind, event_id, user_id) DO NOTHING",
                    (event_kind, event_id, int(user_id), int(time.time())),
                )
                await db.commit()

    async def add_autodelivery_items(self, product: str, values: list[str]) -> int:
        if not values:
            return 0
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                created_at = int(time.time())
                await db.execute(
                    "INSERT OR IGNORE INTO autodelivery_products(product, created_at) VALUES(?, ?)",
                    (product, created_at),
                )
                await db.executemany(
                    "INSERT INTO autodelivery_items(product, value, created_at) VALUES(?, ?, ?)",
                    [(product, v, created_at) for v in values],
                )
                await db.commit()
                return len(values)

    @staticmethod
    def _delivery_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        result = dict(row)
        result["item_ids"] = [int(value) for value in json.loads(result["item_ids"])]
        result["item_values"] = [str(value) for value in json.loads(result["item_values"])]
        result["quantity"] = int(result["quantity"])
        return result

    async def get_order_delivery(self, order_id: str) -> dict[str, Any] | None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT * FROM order_deliveries WHERE order_id=?",
                    (str(order_id),),
                )
                row = await cur.fetchone()
                await cur.close()
                return self._delivery_from_row(row) if row else None

    async def reserve_order_delivery(
        self,
        order_id: str,
        product: str,
        quantity: int,
    ) -> dict[str, Any] | None:
        """Atomically reserve exactly ``quantity`` codes for one order.

        ``None`` means the product is not configured for autodelivery. An
        insufficient result never creates a partial reservation.
        """
        safe_order_id = str(order_id)
        safe_quantity = max(1, int(quantity))
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                try:
                    cur = await db.execute(
                        "SELECT * FROM order_deliveries WHERE order_id=?",
                        (safe_order_id,),
                    )
                    existing = await cur.fetchone()
                    await cur.close()
                    if existing:
                        await db.commit()
                        return self._delivery_from_row(existing)

                    cur = await db.execute(
                        "SELECT 1 FROM autodelivery_products WHERE product=?",
                        (product,),
                    )
                    configured = await cur.fetchone()
                    await cur.close()
                    if not configured:
                        await db.commit()
                        return None

                    cur = await db.execute(
                        "SELECT id, value FROM autodelivery_items "
                        "WHERE product=? AND reserved_order_id IS NULL "
                        "ORDER BY id ASC LIMIT ?",
                        (product, safe_quantity),
                    )
                    rows = await cur.fetchall()
                    await cur.close()
                    if len(rows) != safe_quantity:
                        await db.commit()
                        return {
                            "order_id": safe_order_id,
                            "product": product,
                            "quantity": safe_quantity,
                            "state": "insufficient",
                            "available": len(rows),
                            "item_ids": [],
                            "item_values": [],
                        }

                    item_ids = [int(row["id"]) for row in rows]
                    item_values = [str(row["value"]) for row in rows]
                    placeholders = ",".join("?" for _ in item_ids)
                    update = await db.execute(
                        f"UPDATE autodelivery_items SET reserved_order_id=? "
                        f"WHERE reserved_order_id IS NULL AND id IN ({placeholders})",
                        [safe_order_id, *item_ids],
                    )
                    if int(update.rowcount) != safe_quantity:
                        raise RuntimeError("autodelivery reservation conflict")
                    now = int(time.time())
                    await db.execute(
                        "INSERT INTO order_deliveries("
                        "order_id, product, quantity, item_ids, item_values, state, updated_at"
                        ") VALUES(?, ?, ?, ?, ?, 'reserved', ?)",
                        (
                            safe_order_id,
                            product,
                            safe_quantity,
                            json.dumps(item_ids),
                            json.dumps(item_values, ensure_ascii=False),
                            now,
                        ),
                    )
                    await db.commit()
                    return {
                        "order_id": safe_order_id,
                        "product": product,
                        "quantity": safe_quantity,
                        "item_ids": item_ids,
                        "item_values": item_values,
                        "state": "reserved",
                        "chat_id": None,
                        "error": None,
                        "updated_at": now,
                    }
                except BaseException:
                    await db.rollback()
                    raise

    async def mark_order_delivery_sending(self, order_id: str, chat_id: str) -> bool:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute(
                    "UPDATE order_deliveries SET state='sending', chat_id=?, error=NULL, updated_at=? "
                    "WHERE order_id=? AND state='reserved'",
                    (str(chat_id), int(time.time()), str(order_id)),
                )
                await db.commit()
                return int(cur.rowcount) == 1

    async def mark_order_delivery_error(self, order_id: str, error: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    "UPDATE order_deliveries SET error=?, updated_at=? WHERE order_id=?",
                    (str(error)[:1000], int(time.time()), str(order_id)),
                )
                await db.commit()

    async def mark_order_delivery_sent(self, order_id: str) -> int:
        """Commit a successful website send and consume its reserved stock."""
        safe_order_id = str(order_id)
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    cur = await db.execute(
                        "SELECT state, quantity FROM order_deliveries WHERE order_id=?",
                        (safe_order_id,),
                    )
                    row = await cur.fetchone()
                    await cur.close()
                    if not row or str(row[0]) != "sending":
                        raise RuntimeError("autodelivery is not in sending state")
                    expected = int(row[1])
                    deleted = await db.execute(
                        "DELETE FROM autodelivery_items WHERE reserved_order_id=?",
                        (safe_order_id,),
                    )
                    if int(deleted.rowcount) != expected:
                        raise RuntimeError("autodelivery reserved inventory mismatch")
                    await db.execute(
                        "UPDATE order_deliveries SET state='buyer_sent', error=NULL, updated_at=? "
                        "WHERE order_id=?",
                        (int(time.time()), safe_order_id),
                    )
                    await db.commit()
                    return expected
                except BaseException:
                    await db.rollback()
                    raise

    async def mark_order_delivery_owner_notified(self, order_id: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    "UPDATE order_deliveries SET state='owner_notified', updated_at=? "
                    "WHERE order_id=? AND state='buyer_sent'",
                    (int(time.time()), str(order_id)),
                )
                await db.commit()

    async def pop_autodelivery_item(self, product: str) -> str | None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT id, value FROM autodelivery_items "
                    "WHERE product=? AND reserved_order_id IS NULL ORDER BY id ASC LIMIT 1",
                    (product,),
                )
                row = await cur.fetchone()
                await cur.close()
                if not row:
                    return None
                item_id = int(row["id"]) if "id" in row.keys() else int(row[0])
                value = str(row["value"]) if "value" in row.keys() else str(row[1])
                await db.execute("DELETE FROM autodelivery_items WHERE id=?", (item_id,))
                await db.commit()
                return value

    async def peek_autodelivery_items(self, product: str, limit: int = 1) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT id, value FROM autodelivery_items "
                    "WHERE product=? AND reserved_order_id IS NULL ORDER BY id ASC LIMIT ?",
                    (product, safe_limit),
                )
                rows = await cur.fetchall()
                await cur.close()
                return [{"id": int(row["id"]), "value": str(row["value"])} for row in rows]

    async def delete_autodelivery_items(self, item_ids: list[int]) -> int:
        ids = list(dict.fromkeys(int(item_id) for item_id in item_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute(
                    f"DELETE FROM autodelivery_items "
                    f"WHERE reserved_order_id IS NULL AND id IN ({placeholders})",
                    ids,
                )
                await db.commit()
                return max(0, int(cur.rowcount))

    async def count_autodelivery(self, product: str) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute(
                    "SELECT COUNT(*) FROM autodelivery_items "
                    "WHERE product=? AND reserved_order_id IS NULL",
                    (product,),
                )
                row = await cur.fetchone()
                await cur.close()
                return int(row[0]) if row else 0

    async def list_autodelivery_products(self) -> list[tuple[str, int]]:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute(
                    "SELECT p.product, COUNT(i.id) AS cnt FROM autodelivery_products p "
                    "LEFT JOIN autodelivery_items i "
                    "ON i.product=p.product AND i.reserved_order_id IS NULL "
                    "GROUP BY p.product ORDER BY p.product ASC"
                )
                rows = await cur.fetchall()
                await cur.close()
                return [(str(r[0]), int(r[1])) for r in rows]

    async def delete_autodelivery_product(self, product: str) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("BEGIN IMMEDIATE")
                cur = await db.execute(
                    "SELECT COUNT(*) FROM autodelivery_items "
                    "WHERE product=? AND reserved_order_id IS NULL",
                    (product,),
                )
                row = await cur.fetchone()
                to_del = int(row[0]) if row else 0
                await cur.close()
                await db.execute(
                    "DELETE FROM autodelivery_items "
                    "WHERE product=? AND reserved_order_id IS NULL",
                    (product,),
                )
                await db.execute("DELETE FROM autodelivery_products WHERE product=?", (product,))
                await db.commit()
                return to_del

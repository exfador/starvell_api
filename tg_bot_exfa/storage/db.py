import asyncio
import time
import aiosqlite
from typing import Any


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = asyncio.Lock()

    async def init(self) -> None:
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
            try:
                await db.execute("ALTER TABLE users ADD COLUMN notify_chat INTEGER DEFAULT 1")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE users ADD COLUMN notify_orders INTEGER DEFAULT 1")
            except Exception:
                pass
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
                CREATE TABLE IF NOT EXISTS autodelivery_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at INTEGER DEFAULT 0
                )
                """
            )
            await db.commit()

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

    async def add_autodelivery_items(self, product: str, values: list[str]) -> int:
        if not values:
            return 0
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                created_at = int(time.time())
                await db.executemany(
                    "INSERT INTO autodelivery_items(product, value, created_at) VALUES(?, ?, ?)",
                    [(product, v, created_at) for v in values],
                )
                await db.commit()
                return len(values)

    async def pop_autodelivery_item(self, product: str) -> str | None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT id, value FROM autodelivery_items WHERE product=? ORDER BY id ASC LIMIT 1",
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

    async def count_autodelivery(self, product: str) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT COUNT(*) FROM autodelivery_items WHERE product=?", (product,))
                row = await cur.fetchone()
                await cur.close()
                return int(row[0]) if row else 0

    async def list_autodelivery_products(self) -> list[tuple[str, int]]:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT product, COUNT(*) AS cnt FROM autodelivery_items GROUP BY product ORDER BY product ASC")
                rows = await cur.fetchall()
                await cur.close()
                return [(str(r[0]), int(r[1])) for r in rows]

    async def delete_autodelivery_product(self, product: str) -> int:
        async with self._lock:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute("SELECT COUNT(*) FROM autodelivery_items WHERE product=?", (product,))
                row = await cur.fetchone()
                to_del = int(row[0]) if row else 0
                await cur.close()
                await db.execute("DELETE FROM autodelivery_items WHERE product=?", (product,))
                await db.commit()
                return to_del



from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    def __init__(self, path: str | Path, ttl_seconds: int = 7 * 24 * 3600):
        self.path = str(path)
        self.ttl_seconds = max(3600, int(ttl_seconds))
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(key: StorageKey) -> tuple[int, int, int, int, str, str]:
        return (
            int(key.bot_id),
            int(key.chat_id),
            int(key.user_id),
            int(key.thread_id or 0),
            str(key.business_connection_id or ""),
            str(key.destiny),
        )

    async def init(self) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.path) as database:
                await database.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fsm_storage (
                        bot_id INTEGER NOT NULL,
                        chat_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        thread_id INTEGER NOT NULL,
                        business_connection_id TEXT NOT NULL,
                        destiny TEXT NOT NULL,
                        state TEXT,
                        data TEXT NOT NULL DEFAULT '{}',
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY(
                            bot_id, chat_id, user_id, thread_id,
                            business_connection_id, destiny
                        )
                    )
                    """
                )
                await database.execute(
                    "DELETE FROM fsm_storage WHERE updated_at < ?",
                    (int(time.time()) - self.ttl_seconds,),
                )
                await database.commit()

    async def _read(self, key: StorageKey) -> tuple[str | None, dict[str, Any]]:
        async with aiosqlite.connect(self.path) as database:
            cur = await database.execute(
                "SELECT state, data FROM fsm_storage WHERE "
                "bot_id=? AND chat_id=? AND user_id=? AND thread_id=? "
                "AND business_connection_id=? AND destiny=?",
                self._key(key),
            )
            row = await cur.fetchone()
            await cur.close()
            if not row:
                return None, {}
            try:
                data = json.loads(row[1])
            except (TypeError, json.JSONDecodeError):
                data = {}
            return (str(row[0]) if row[0] is not None else None), data if isinstance(data, dict) else {}

    async def _write(self, key: StorageKey, state: str | None, data: Mapping[str, Any]) -> None:
        async with aiosqlite.connect(self.path) as database:
            await database.execute(
                "INSERT INTO fsm_storage("
                "bot_id, chat_id, user_id, thread_id, business_connection_id, destiny, "
                "state, data, updated_at"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(bot_id, chat_id, user_id, thread_id, business_connection_id, destiny) "
                "DO UPDATE SET state=excluded.state, data=excluded.data, updated_at=excluded.updated_at",
                (*self._key(key), state, json.dumps(dict(data), ensure_ascii=False), int(time.time())),
            )
            await database.commit()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        state_value = state.state if isinstance(state, State) else (str(state) if state is not None else None)
        async with self._lock:
            _, data = await self._read(key)
            await self._write(key, state_value, data)

    async def get_state(self, key: StorageKey) -> str | None:
        async with self._lock:
            state, _ = await self._read(key)
            return state

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async with self._lock:
            state, _ = await self._read(key)
            await self._write(key, state, data)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self._lock:
            _, data = await self._read(key)
            return data.copy()

    async def update_data(self, key: StorageKey, data: Mapping[str, Any]) -> dict[str, Any]:
        async with self._lock:
            state, current = await self._read(key)
            current.update(data)
            await self._write(key, state, current)
            return current.copy()

    async def close(self) -> None:
        return None


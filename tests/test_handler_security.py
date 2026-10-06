import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import tg_bot_exfa.app as app
from tg_bot_exfa.handlers.plugins import MAX_PLUGIN_BYTES, _stage_and_load_plugin
from tg_bot_exfa.config import hash_password
from tg_bot_exfa.handlers.start import _run_password_work, on_change_password, on_password
from tg_bot_exfa.plugins.manager import PluginManager
from tg_bot_exfa.states.auth import StartFlow
from tg_bot_exfa.storage.db import Database


class PasswordWorkTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_work_uses_threads_with_bounded_concurrency(self):
        active = 0
        peak = 0
        two_started = asyncio.Event()
        release = asyncio.Event()

        async def fake_to_thread(func, *args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
            await release.wait()
            try:
                return func(*args)
            finally:
                active -= 1

        with patch("tg_bot_exfa.handlers.start.asyncio.to_thread", new=fake_to_thread):
            tasks = [asyncio.create_task(_run_password_work(lambda value: value, index)) for index in range(4)]
            await asyncio.wait_for(two_started.wait(), timeout=1)
            await asyncio.sleep(0)
            self.assertEqual(peak, 2)
            release.set()
            self.assertEqual(await asyncio.gather(*tasks), [0, 1, 2, 3])


class PasswordChangeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "bot.sqlite3"))
        await self.db.init()
        self.config = SimpleNamespace(password_md5="old-hash", default_language="ru")
        app.app_context = SimpleNamespace(db=self.db, config=self.config)
        self.storage = MemoryStorage()

    async def asyncTearDown(self):
        app.app_context = None
        self.tmp.cleanup()

    def _context(self, user_id: int) -> FSMContext:
        return FSMContext(self.storage, StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))

    def _message(self, user_id: int, text: str):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            chat=SimpleNamespace(id=user_id),
            message_id=50,
            text=text,
            delete=AsyncMock(),
            answer=AsyncMock(),
            bot=SimpleNamespace(edit_message_text=AsyncMock()),
        )

    async def _authorize(self, *user_ids: int):
        for user_id in user_ids:
            await self.db.get_user(user_id)
            await self.db.set_authorized(user_id, True)

    async def test_revoked_user_with_stale_dialog_cannot_change_password(self):
        await self._authorize(1, 2)
        stale = self._context(2)
        await stale.set_state(StartFlow.changing_password)
        await self.db.revoke_authorizations(except_user_id=1)
        message = self._message(2, "takeover")

        with patch("tg_bot_exfa.handlers.start.save_config") as save:
            await on_change_password(message, stale)

        save.assert_not_called()
        self.assertEqual(self.config.password_md5, "old-hash")
        self.assertTrue((await self.db.get_user(1))["authorized"])
        self.assertIsNone(await stale.get_state())
        message.delete.assert_awaited_once()

    async def test_password_change_revokes_others_and_resets_their_dialogs(self):
        await self._authorize(1, 2)
        owner, other = self._context(1), self._context(2)
        await owner.set_state(StartFlow.changing_password)
        await other.set_state(StartFlow.changing_session)
        await other.update_data(last_message_id=7)

        with (
            patch("tg_bot_exfa.handlers.start.save_config") as save,
            patch("tg_bot_exfa.handlers.start.hash_password", lambda value: f"hash:{value}"),
        ):
            await on_change_password(self._message(1, "new-password"), owner)

        save.assert_called_once()
        self.assertEqual(self.config.password_md5, "hash:new-password")
        self.assertTrue((await self.db.get_user(1))["authorized"])
        self.assertFalse((await self.db.get_user(2))["authorized"])
        self.assertIsNone(await other.get_state())
        self.assertEqual(await other.get_data(), {})
        self.assertIsNone(await owner.get_state())


class LoginLockoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "bot.sqlite3"))
        await self.db.init()
        app.app_context = SimpleNamespace(
            db=self.db,
            config=SimpleNamespace(password_md5=hash_password("right", iterations=1_000), default_language="ru"),
        )

    async def asyncTearDown(self):
        app.app_context = None
        self.tmp.cleanup()

    async def test_block_resets_the_attempt_counter(self):
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=5, user_id=5))
        for _ in range(5):
            await state.set_state(StartFlow.waiting_password)
            await state.update_data(last_message_id=9)
            message = SimpleNamespace(
                from_user=SimpleNamespace(id=5, username="guest"),
                chat=SimpleNamespace(id=5),
                text="wrong",
                delete=AsyncMock(),
                answer=AsyncMock(),
                bot=SimpleNamespace(edit_message_text=AsyncMock()),
            )
            with patch("tg_bot_exfa.handlers.start.send_security_auth_blocked", AsyncMock()):
                await on_password(message, state)

        user = await self.db.get_user(5)
        self.assertGreater(user["blocked_until"], 0)
        self.assertEqual(user["failed_attempts"], 0)


class _FakePluginManager:
    def __init__(self, *, load_error: str | None = None):
        self.load_error = load_error
        self.plugins = {}
        self.load_paths: list[Path] = []
        self.removed: list[str] = []

    def _extract_meta_text(self, _path: str):
        return {"UUID": "plugin-uuid"}

    def load_one(self, path: str):
        plugin_path = Path(path)
        self.load_paths.append(plugin_path)
        meta = SimpleNamespace(
            name="Plugin",
            uuid="plugin-uuid",
            version="1.0",
            module=None if self.load_error else object(),
            load_error=self.load_error,
            path=str(plugin_path),
        )
        self.plugins[meta.uuid] = meta
        return meta

    def remove(self, uuid: str):
        self.removed.append(uuid)
        meta = self.plugins.pop(uuid, None)
        if meta:
            Path(meta.path).unlink(missing_ok=True)
        return True


class PluginUploadSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_plugin_manager_loads_only_staged_valid_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugins_dir = root / "plugins"
            manager = PluginManager(str(plugins_dir), str(root / "state" / "plugins.json"))
            source = b'NAME = "Plugin"\nUUID = "plugin-uuid"\nVERSION = "1.0"\n'

            async def download_file(_remote_path, destination):
                Path(destination).write_bytes(source)

            bot = SimpleNamespace(
                get_file=AsyncMock(return_value=SimpleNamespace(file_path="remote/plugin.py")),
                download_file=AsyncMock(side_effect=download_file),
            )
            document = SimpleNamespace(file_name="plugin.py", file_size=len(source), file_id="doc")

            with patch("tg_bot_exfa.handlers.plugins.PLUGINS_PATH", plugins_dir):
                meta = await _stage_and_load_plugin(bot, document, manager)

            self.assertIs(manager.plugins["plugin-uuid"], meta)
            self.assertIsNotNone(meta.module)
            self.assertEqual(Path(meta.path), plugins_dir / "plugin.py")
            self.assertEqual(Path(meta.module.__file__), plugins_dir / "plugin.py")
            self.assertTrue((plugins_dir / "plugin.py").is_file())
            self.assertFalse(any(".plugin-upload-" in item for item in sys.path))

    async def test_valid_plugin_is_loaded_from_staging_then_atomically_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            plugins_dir = Path(directory) / "plugins"
            manager = _FakePluginManager()
            source = b'NAME = "Plugin"\nUUID = "plugin-uuid"\nVERSION = "1.0"\n'

            async def download_file(_remote_path, destination):
                Path(destination).write_bytes(source)

            bot = SimpleNamespace(
                get_file=AsyncMock(return_value=SimpleNamespace(file_path="remote/plugin.py")),
                download_file=AsyncMock(side_effect=download_file),
            )
            document = SimpleNamespace(file_name="plugin.py", file_size=len(source), file_id="doc")

            with patch("tg_bot_exfa.handlers.plugins.PLUGINS_PATH", plugins_dir):
                meta = await _stage_and_load_plugin(bot, document, manager)

            destination = plugins_dir / "plugin.py"
            self.assertEqual(destination.read_bytes(), source)
            self.assertEqual(Path(meta.path), destination)
            self.assertEqual(len(manager.load_paths), 1)
            self.assertNotEqual(manager.load_paths[0], destination)
            self.assertFalse(manager.load_paths[0].exists())

    async def test_load_failure_leaves_no_active_plugin_file_or_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            plugins_dir = Path(directory) / "plugins"
            manager = _FakePluginManager(load_error="import failed")
            source = b'NAME = "Plugin"\nUUID = "plugin-uuid"\nVERSION = "1.0"\n'

            async def download_file(_remote_path, destination):
                Path(destination).write_bytes(source)

            bot = SimpleNamespace(
                get_file=AsyncMock(return_value=SimpleNamespace(file_path="remote/plugin.py")),
                download_file=AsyncMock(side_effect=download_file),
            )
            document = SimpleNamespace(file_name="plugin.py", file_size=len(source), file_id="doc")

            with (
                patch("tg_bot_exfa.handlers.plugins.PLUGINS_PATH", plugins_dir),
                self.assertRaisesRegex(RuntimeError, "import failed"),
            ):
                await _stage_and_load_plugin(bot, document, manager)

            self.assertFalse((plugins_dir / "plugin.py").exists())
            self.assertEqual(manager.plugins, {})
            self.assertEqual(manager.removed, ["plugin-uuid"])

    async def test_existing_plugin_and_oversized_upload_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            plugins_dir = Path(directory) / "plugins"
            plugins_dir.mkdir()
            destination = plugins_dir / "plugin.py"
            destination.write_text("working", encoding="utf-8")
            manager = _FakePluginManager()
            bot = SimpleNamespace(get_file=AsyncMock(), download_file=AsyncMock())
            document = SimpleNamespace(
                file_name="plugin.py",
                file_size=MAX_PLUGIN_BYTES + 1,
                file_id="doc",
            )

            with (
                patch("tg_bot_exfa.handlers.plugins.PLUGINS_PATH", plugins_dir),
                self.assertRaisesRegex(ValueError, "too large"),
            ):
                await _stage_and_load_plugin(bot, document, manager)

            self.assertEqual(destination.read_text(encoding="utf-8"), "working")
            bot.get_file.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

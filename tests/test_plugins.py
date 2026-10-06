import asyncio
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from tg_bot_exfa.plugins.manager import PluginManager


PLUGIN_SOURCE = """
NAME = "Example"
UUID = "plugin-1"
VERSION = "1.0"

from pathlib import Path
Path({marker!r}).write_text("imported", encoding="utf-8")

def command(message, args, ctx):
    return "ok"

def register_commands():
    return [("hello", command, "Hello")]
"""


class PluginManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_plugin_is_not_imported_until_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugins"
            root.mkdir()
            marker = Path(directory) / "imported.txt"
            plugin = root / "example.py"
            plugin.write_text(PLUGIN_SOURCE.format(marker=str(marker)), encoding="utf-8")
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({"disabled": ["plugin-1"]}), encoding="utf-8")
            manager = PluginManager(str(root), str(state))

            manager.load_all()

            self.assertFalse(marker.exists())
            self.assertFalse(manager.plugins["plugin-1"].enabled)
            self.assertIsNone(manager.plugins["plugin-1"].module)
            self.assertNotIn("hello", manager.commands)

            self.assertTrue(manager.enable("plugin-1"))
            self.assertTrue(marker.exists())
            self.assertIn("hello", manager.commands)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)

    async def test_disabled_plugin_with_info_dict_is_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugins"
            root.mkdir()
            marker = Path(directory) / "imported.txt"
            source = (
                'INFO = {"name": "Info plugin", "uuid": "info-1", "version": "2.0", "credits": "@dev"}\n'
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
            )
            (root / "info_plugin.py").write_text(source, encoding="utf-8")
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({"disabled": ["info-1"]}), encoding="utf-8")
            manager = PluginManager(str(root), str(state))

            manager.load_all()

            self.assertFalse(marker.exists())
            meta = manager.plugins["info-1"]
            self.assertEqual((meta.name, meta.version, meta.credits), ("Info plugin", "2.0", "@dev"))
            self.assertIsNone(meta.module)

    def test_metadata_is_read_without_execution_and_constants_win(self):
        manager = PluginManager(tempfile.gettempdir(), str(Path(tempfile.gettempdir()) / "state.json"))
        text = 'INFO = {"uuid": "from-info", "name": "Info"}\nUUID = "from-constant"\nraise SystemExit("never run")\n'

        self.assertEqual(manager._extract_meta_ast(text), {"UUID": "from-constant", "NAME": "Info"})

    async def test_duplicate_and_reserved_commands_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginManager(directory, str(Path(directory) / "state.json"))

            def handler(*args):
                return None

            class First:
                @staticmethod
                def register_commands():
                    return [("start", handler), ("shared", handler)]

            class Second:
                @staticmethod
                def register_commands():
                    return [("shared", handler)]

            manager._register_commands_for_module(First, "first")
            manager._register_commands_for_module(Second, "second")

            self.assertNotIn("start", manager.commands)
            self.assertEqual(manager.commands["shared"]["uuid"], "first")

    async def test_synchronous_plugin_hook_does_not_block_event_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = PluginManager(directory, str(Path(directory) / "state.json"))
            manager.hook_timeout = 0.03

            def slow_hook():
                time.sleep(0.2)

            heartbeat = asyncio.Event()

            async def tick():
                await asyncio.sleep(0.01)
                heartbeat.set()

            started = time.monotonic()
            await asyncio.gather(manager._maybe_call(slow_hook), tick())

            self.assertTrue(heartbeat.is_set())
            self.assertLess(time.monotonic() - started, 0.15)

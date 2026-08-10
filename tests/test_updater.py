import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tg_bot_exfa.updater import (
    apply_update_tree,
    install_requirements_if_needed,
    locate_repository_root,
    safe_extract_zip,
)


def _make_project(root: Path, marker: str) -> None:
    (root / "api").mkdir(parents=True)
    (root / "tg_bot_exfa").mkdir(parents=True)
    (root / "api" / "auth.py").write_text(f"VALUE = {marker!r}\n", encoding="utf-8")
    (root / "tg_bot_exfa" / "bot.py").write_text(f"VALUE = {marker!r}\n", encoding="utf-8")
    (root / "run_bot.py").write_text("pass\n", encoding="utf-8")
    (root / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")


class UpdaterTests(unittest.TestCase):
    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../owned.txt", "bad")

            with self.assertRaisesRegex(ValueError, "unsafe"):
                safe_extract_zip(archive, Path(directory) / "output")

    def test_repository_root_requires_full_project_sentinels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api").mkdir()
            (root / "api" / "auth.py").write_text("pass\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "complete"):
                locate_repository_root(root)

    def test_update_preserves_database_and_unmanaged_config(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            local = base / "local"
            remote = base / "remote"
            _make_project(local, "old")
            _make_project(remote, "new")
            (local / "config").mkdir()
            (local / "config" / "osnova.json").write_text("secret", encoding="utf-8")
            (local / "tg_bot_exfa" / "bot.sqlite3").write_bytes(b"database")

            changes = apply_update_tree(remote, local)

            self.assertIn("api/auth.py", changes["updated"])
            self.assertIn("new", (local / "api" / "auth.py").read_text(encoding="utf-8"))
            self.assertEqual((local / "tg_bot_exfa" / "bot.sqlite3").read_bytes(), b"database")
            self.assertEqual((local / "config" / "osnova.json").read_text(encoding="utf-8"), "secret")

    def test_failed_component_swap_rolls_back_prior_components(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            local = base / "local"
            remote = base / "remote"
            _make_project(local, "old")
            _make_project(remote, "new")
            real_replace = os.replace

            def fail_tg_swap(source, destination):
                source_path = Path(source)
                if source_path.name == "tg_bot_exfa" and source_path.parent.name == "staged":
                    raise OSError("simulated swap failure")
                return real_replace(source, destination)

            with (
                patch("tg_bot_exfa.updater.os.replace", side_effect=fail_tg_swap),
                self.assertRaisesRegex(OSError, "simulated"),
            ):
                apply_update_tree(remote, local)

            self.assertIn("old", (local / "api" / "auth.py").read_text(encoding="utf-8"))
            self.assertIn("old", (local / "tg_bot_exfa" / "bot.py").read_text(encoding="utf-8"))

    def test_changed_requirements_are_installed_before_source_swap(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            local = base / "local"
            remote = base / "remote"
            _make_project(local, "old")
            _make_project(remote, "new")
            (remote / "requirements.txt").write_text("aiohttp\naiosqlite\n", encoding="utf-8")

            with patch("tg_bot_exfa.updater.subprocess.run") as run:
                changed = install_requirements_if_needed(remote, local)

            self.assertTrue(changed)
            run.assert_called_once()
            self.assertTrue(run.call_args.kwargs["check"])

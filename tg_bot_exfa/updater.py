from __future__ import annotations

import ast
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


UPDATE_COMPONENTS = (
    "api",
    "tg_bot_exfa",
    "run_bot.py",
    "requirements.txt",
    "version.py",
    "README.md",
    "setup_starvell.sh",
    "install_starvell.sh",
    "setup_bot.bat",
    "start_bot.bat",
)
REQUIRED_SENTINELS = (
    "api/auth.py",
    "tg_bot_exfa/bot.py",
    "run_bot.py",
    "requirements.txt",
)


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive, "r") as bundle:
        for member in bundle.infolist():
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe archive member: {member.filename}")
            unix_mode = (member.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise ValueError(f"archive symlink is not allowed: {member.filename}")
            target = (destination / Path(*relative.parts)).resolve()
            if target != destination and destination not in target.parents:
                raise ValueError(f"unsafe archive member: {member.filename}")
        bundle.extractall(destination)


def locate_repository_root(extracted_dir: Path) -> Path:
    candidates = [extracted_dir, *(path for path in extracted_dir.iterdir() if path.is_dir())]
    for candidate in candidates:
        if all((candidate / sentinel).is_file() for sentinel in REQUIRED_SENTINELS):
            return candidate
    raise ValueError("release archive does not contain a complete Starvell project")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_for_components(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for component in UPDATE_COMPONENTS:
        path = root / component
        if path.is_file():
            result[component] = path
        elif path.is_dir():
            for file_path in path.rglob("*"):
                if file_path.is_file() and "__pycache__" not in file_path.parts:
                    result[file_path.relative_to(root).as_posix()] = file_path
    return result


def _validate_python(root: Path) -> None:
    for relative, path in _files_for_components(root).items():
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=relative)


def install_requirements_if_needed(remote_root: Path, project_root: Path) -> bool:
    remote = remote_root / "requirements.txt"
    local = project_root / "requirements.txt"
    if local.is_file() and _file_hash(remote) == _file_hash(local):
        return False
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(remote)],
        check=True,
        timeout=300,
    )
    return True


def apply_update_tree(remote_root: Path, project_root: Path) -> dict[str, list[str]]:
    remote_root = remote_root.resolve()
    project_root = project_root.resolve()
    if not all((remote_root / sentinel).is_file() for sentinel in REQUIRED_SENTINELS):
        raise ValueError("release is missing required project files")
    _validate_python(remote_root)

    remote_files = _files_for_components(remote_root)
    local_files = _files_for_components(project_root)
    changes = {
        "new": sorted(path for path in remote_files if path not in local_files),
        "updated": sorted(
            path
            for path in remote_files.keys() & local_files.keys()
            if _file_hash(remote_files[path]) != _file_hash(local_files[path])
        ),
        "deleted": sorted(path for path in local_files if path not in remote_files),
    }

    transaction = Path(tempfile.mkdtemp(prefix=".starvell-update-", dir=str(project_root.parent)))
    staged = transaction / "staged"
    backup = transaction / "backup"
    discarded = transaction / "discarded"
    staged.mkdir()
    backup.mkdir()
    discarded.mkdir()
    installed: list[tuple[Path, Path | None]] = []
    try:
        for component in UPDATE_COMPONENTS:
            source = remote_root / component
            if not source.exists():
                continue
            destination = staged / component
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(source, destination)

        live_database = project_root / "tg_bot_exfa" / "bot.sqlite3"
        staged_database = staged / "tg_bot_exfa" / "bot.sqlite3"
        if live_database.is_file():
            shutil.copy2(live_database, staged_database)

        _validate_python(staged)
        for component in UPDATE_COMPONENTS:
            source = staged / component
            if not source.exists():
                continue
            target = project_root / component
            saved = backup / component
            saved.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                os.replace(target, saved)
                saved_target: Path | None = saved
            else:
                saved_target = None
            try:
                os.replace(source, target)
            except BaseException:
                if saved_target is not None:
                    os.replace(saved_target, target)
                raise
            installed.append((target, saved_target))
        return changes
    except BaseException:
        for target, saved in reversed(installed):
            if target.exists():
                failed_target = discarded / target.name
                if failed_target.exists():
                    if failed_target.is_dir():
                        shutil.rmtree(failed_target)
                    else:
                        failed_target.unlink()
                os.replace(target, failed_target)
            if saved is not None and saved.exists():
                os.replace(saved, target)
        raise
    finally:
        shutil.rmtree(transaction, ignore_errors=True)

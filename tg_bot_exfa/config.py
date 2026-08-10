import json
import os
import hashlib
import hmac
import secrets
import tempfile
from pathlib import Path

from tg_bot_exfa.paths import CONFIG_PATH


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000


class BotConfig:
    def __init__(self, token: str, password_md5: str, default_language: str, path: str,
                 author_username: str | None = None,
                 channel_url: str | None = None,
                 chat_url: str | None = None,
                 debug: bool = True,
                 watermark_on: bool = True,
                 watermark_text: str = "[CXH BOT]",
                 welcome_enabled: bool = True,
                 welcome_text: str = "CXH BOT это автоматический бот по заказам / cообщения с сайта starvell, наш бот может многое",
                 welcome_cooldown_minutes: int = 1900):
        self.token = token
        self.password_md5 = password_md5
        self.default_language = default_language
        self.path = path
        self.author_username = author_username or "@exfador"
        self.channel_url = channel_url or "https://t.me/starvellapi"
        self.chat_url = chat_url or "https://t.me/community_starvell"
        self.debug = bool(debug)
        self.watermark_on = bool(watermark_on)
        self.watermark_text = watermark_text or "[CXH BOT]"
        self.welcome_enabled = bool(welcome_enabled)
        self.welcome_text = welcome_text or "CXH BOT это автоматический бот по заказам / cообщения с сайта starvell, наш бот может многое"
        try:
            self.welcome_cooldown_minutes = int(welcome_cooldown_minutes)
        except Exception:
            self.welcome_cooldown_minutes = 1900


def md5_hex(text: str) -> str:
    """Legacy hash retained only for reading old configurations."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def hash_password(text: str, *, iterations: int = PASSWORD_ITERATIONS, salt: str | None = None) -> str:
    normalized = text.strip()
    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalized.encode("utf-8"),
        salt_value.encode("ascii"),
        int(iterations),
    ).hex()
    return f"{PASSWORD_SCHEME}${int(iterations)}${salt_value}${digest}"


def verify_password(text: str, encoded: str) -> bool:
    stored = (encoded or "").strip()
    if not stored:
        return False
    if stored.startswith(f"{PASSWORD_SCHEME}$"):
        try:
            scheme, raw_iterations, salt, expected = stored.split("$", 3)
            if scheme != PASSWORD_SCHEME:
                return False
            actual = hash_password(text, iterations=int(raw_iterations), salt=salt).rsplit("$", 1)[-1]
            return hmac.compare_digest(actual, expected)
        except (TypeError, ValueError):
            return False
    return hmac.compare_digest(md5_hex(text).lower(), stored.lower())


def password_needs_rehash(encoded: str) -> bool:
    stored = (encoded or "").strip()
    if not stored.startswith(f"{PASSWORD_SCHEME}$"):
        return True
    try:
        return int(stored.split("$", 3)[1]) < PASSWORD_ITERATIONS
    except (IndexError, ValueError):
        return True


def _write_json_atomic(path: str | Path, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".tmp", dir=str(target.parent), text=True)
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def update_config_values(path: str | Path, **values) -> dict:
    target = Path(path)
    data: dict = {}
    if target.exists():
        with target.open("r", encoding="utf-8") as f:
            loaded = json.load(f) or {}
        if not isinstance(loaded, dict):
            raise ValueError("Configuration root must be a JSON object")
        data = loaded
    data.update(values)
    _write_json_atomic(target, data)
    return data


def load_config() -> BotConfig:
    path = str(CONFIG_PATH)
    data = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    token = os.getenv("BOT_TOKEN") or data.get("BOT_TOKEN", "")
    password_md5 = os.getenv("BOT_PASSWORD_MD5") or data.get("BOT_PASSWORD_MD5", "")
    if not password_md5:
        plain = os.getenv("BOT_PASSWORD") or data.get("BOT_PASSWORD", "")
        if plain:
            password_md5 = hash_password(plain)
    default_language = data.get("DEFAULT_LANGUAGE", "ru")
    author_username = data.get("AUTHOR_USERNAME") or os.getenv("AUTHOR_USERNAME") or "@exfador"
    channel_url = data.get("CHANNEL_URL") or os.getenv("CHANNEL_URL") or "https://t.me/starvellapi"
    chat_url = data.get("CHAT_URL") or os.getenv("CHAT_URL") or "https://t.me/community_starvell"
    debug = bool(data.get("DEBUG", True))
    watermark_on = bool(data.get("WATERMARK_ON", True))
    watermark_text = str(data.get("WATERMARK_TEXT") or "[CXH BOT]")

    welcome_enabled = bool(data.get("WELCOME_ENABLED", True))
    welcome_text = str(data.get("WELCOME_TEXT") or "CXH BOT это автоматический бот по заказам / cообщения с сайта starvell, наш бот может многое")
    try:
        welcome_cooldown_minutes = int(data.get("WELCOME_COOLDOWN_MINUTES", 1900))
    except Exception:
        welcome_cooldown_minutes = 1900
    return BotConfig(
        token=token,
        password_md5=password_md5,
        default_language=default_language,
        path=path,
        author_username=author_username,
        channel_url=channel_url,
        chat_url=chat_url,
        debug=debug,
        watermark_on=watermark_on,
        watermark_text=watermark_text,
        welcome_enabled=welcome_enabled,
        welcome_text=welcome_text,
        welcome_cooldown_minutes=welcome_cooldown_minutes,
    )


def save_config(cfg: BotConfig) -> None:
    data = {}
    cfg_dir = os.path.dirname(cfg.path)
    if cfg_dir and not os.path.exists(cfg_dir):
        os.makedirs(cfg_dir, exist_ok=True)

    if os.path.exists(cfg.path):
        try:
            with open(cfg.path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
    data.update(
        {
            "BOT_TOKEN": cfg.token,
            "BOT_PASSWORD_MD5": cfg.password_md5,
            "DEFAULT_LANGUAGE": cfg.default_language or "ru",
            "DEBUG": bool(cfg.debug),
            "WATERMARK_ON": bool(getattr(cfg, "watermark_on", True)),
            "WATERMARK_TEXT": str(getattr(cfg, "watermark_text", "[CXH BOT]")),
            "WELCOME_ENABLED": bool(getattr(cfg, "welcome_enabled", True)),
            "WELCOME_TEXT": str(
                getattr(
                    cfg,
                    "welcome_text",
                    "CXH BOT это автоматический бот по заказам / cообщения с сайта starvell, наш бот может многое",
                )
            ),
            "WELCOME_COOLDOWN_MINUTES": int(getattr(cfg, "welcome_cooldown_minutes", 1900)),
        }
    )
    _write_json_atomic(cfg.path, data)

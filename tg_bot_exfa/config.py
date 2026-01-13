import json
import os
import hashlib


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
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def load_config() -> BotConfig:
    path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "config", "osnova.json"))
    data = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    token = os.getenv("BOT_TOKEN") or data.get("BOT_TOKEN", "")
    password_md5 = os.getenv("BOT_PASSWORD_MD5") or data.get("BOT_PASSWORD_MD5", "")
    if not password_md5:
        plain = os.getenv("BOT_PASSWORD") or data.get("BOT_PASSWORD", "")
        if plain:
            password_md5 = md5_hex(plain)
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
    with open(cfg.path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)



import json
import os
import hashlib


class BotConfig:
    def __init__(self, token: str, password_md5: str, default_language: str, path: str,
                 author_username: str | None = None,
                 channel_url: str | None = None,
                 chat_url: str | None = None,
                 debug: bool = True):
        self.token = token
        self.password_md5 = password_md5
        self.default_language = default_language
        self.path = path
        self.author_username = author_username or "@exfador"
        self.channel_url = channel_url or "https://t.me/starvellapi"
        self.chat_url = chat_url or "https://t.me/community_starvell"
        self.debug = bool(debug)


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
    return BotConfig(
        token=token,
        password_md5=password_md5,
        default_language=default_language,
        path=path,
        author_username=author_username,
        channel_url=channel_url,
        chat_url=chat_url,
        debug=debug,
    )


def save_config(cfg: BotConfig) -> None:
    data = {}
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
        }
    )
    with open(cfg.path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)



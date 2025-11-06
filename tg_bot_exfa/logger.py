import logging
import os
import sys
from datetime import datetime


class _ColorFormatter(logging.Formatter):
    COLORS = {
        "RESET": "\033[0m",
        "GRAY": "\033[90m",
        "RED": "\033[91m",
        "GREEN": "\033[92m",
        "YELLOW": "\033[93m",
        "BLUE": "\033[94m",
        "MAGENTA": "\033[95m",
        "CYAN": "\033[96m",
    }

    def __init__(self, fmt: str, datefmt: str | None = None, enable_color: bool = True):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.enable_color = enable_color and _supports_color()

    def format(self, record: logging.LogRecord) -> str:
        if self.enable_color:
            color = self._color_for(record)
            record.levelname = f"{color}{record.levelname}{self.COLORS['RESET']}"
            record.name = f"{self.COLORS['BLUE']}{record.name}{self.COLORS['RESET']}"
            if record.name.startswith("exfador.pretty"):
                record.msg = f"{self.COLORS['MAGENTA']}{record.msg}{self.COLORS['RESET']}"
        return super().format(record)

    def _color_for(self, record: logging.LogRecord) -> str:
        if record.levelno >= logging.ERROR:
            return self.COLORS["RED"]
        if record.levelno >= logging.WARNING:
            return self.COLORS["YELLOW"]
        if record.levelno >= logging.INFO:
            return self.COLORS["GREEN"]
        return self.COLORS["GRAY"]


def _supports_color() -> bool:
    if os.name != "nt":
        return True
    try:
        import colorama 

        colorama.just_fix_windows_console()
        return True
    except Exception:
        return False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger()
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    formatter = _ColorFormatter(fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s", datefmt="%H:%M:%S")
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    try:
        root_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
        logs_root = os.path.join(root_dir, "logs")
        os.makedirs(logs_root, exist_ok=True)

        class _DateFolderFileHandler(logging.Handler):
            def __init__(self, logs_root_dir: str, filename: str = "bot.log", retention_days: int = 30):
                super().__init__(level=logging.DEBUG)
                self.logs_root_dir = logs_root_dir
                self.filename = filename
                self.retention_days = max(0, int(retention_days))
                self._stream = None
                self._current_date = None
                self._open_stream_for_today()

            def _today(self) -> str:
                return datetime.now().strftime("%Y-%m-%d")

            def _cleanup_old(self) -> None:
                if self.retention_days <= 0:
                    return
                try:
                    items = [d for d in os.listdir(self.logs_root_dir) if os.path.isdir(os.path.join(self.logs_root_dir, d))]
                    dates = []
                    for d in items:
                        try:
                            datetime.strptime(d, "%Y-%m-%d")
                            dates.append(d)
                        except Exception:
                            continue
                    dates.sort()
                    to_remove = dates[:-self.retention_days]
                    for d in to_remove:
                        full = os.path.join(self.logs_root_dir, d)
                        try:
                            for name in os.listdir(full):
                                try:
                                    os.remove(os.path.join(full, name))
                                except Exception:
                                    pass
                            os.rmdir(full)
                        except Exception:
                            pass
                except Exception:
                    pass

            def _open_stream_for_today(self) -> None:
                new_date = self._today()
                if self._current_date == new_date and self._stream is not None:
                    return
                try:
                    if self._stream is not None:
                        try:
                            self._stream.flush()
                            self._stream.close()
                        except Exception:
                            pass
                        self._stream = None
                    day_dir = os.path.join(self.logs_root_dir, new_date)
                    os.makedirs(day_dir, exist_ok=True)
                    path = os.path.join(day_dir, self.filename)
                    self._stream = open(path, mode="a", encoding="utf-8", buffering=1)
                    self._current_date = new_date
                    self._cleanup_old()
                except Exception:
                    self._stream = None

            def emit(self, record: logging.LogRecord) -> None:
                try:
                    self._open_stream_for_today()
                    if self._stream is None:
                        return
                    msg = self.format(record)
                    self._stream.write(msg + "\n")
                except Exception:
                    pass

            def flush(self) -> None:
                try:
                    if self._stream is not None:
                        self._stream.flush()
                except Exception:
                    pass

            def close(self) -> None:
                try:
                    if self._stream is not None:
                        try:
                            self._stream.flush()
                        except Exception:
                            pass
                        self._stream.close()
                        self._stream = None
                finally:
                    super().close()

        file_handler = _DateFolderFileHandler(logs_root)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass

    try:
        logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    except Exception:
        pass
    try:
        logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    except Exception:
        pass
    try:
        logging.getLogger("aiohttp.client").setLevel(logging.WARNING)
    except Exception:
        pass
    try:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)
    except Exception:
        pass

    banner = "EXFADOR STARVELL BOT started | author: t.me/exfador | channel: https://t.me/starvellapi"
    logging.getLogger("exfador").info(banner)
    return logger



import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
_configured_data_root = os.getenv("STARVELL_DATA_DIR", "").strip()
DATA_ROOT = Path(_configured_data_root).expanduser().resolve() if _configured_data_root else None

CONFIG_PATH = (DATA_ROOT / "config" / "osnova.json") if DATA_ROOT else (PROJECT_ROOT / "config" / "osnova.json")
DATABASE_PATH = (DATA_ROOT / "bot.sqlite3") if DATA_ROOT else (PACKAGE_ROOT / "bot.sqlite3")
PLUGINS_PATH = (DATA_ROOT / "plugins") if DATA_ROOT else (PROJECT_ROOT / "plugins")
PLUGIN_STATE_PATH = (
    (DATA_ROOT / "storage" / "plugins" / "state.json")
    if DATA_ROOT
    else (PROJECT_ROOT / "storage" / "plugins" / "state.json")
)
LOGS_PATH = (DATA_ROOT / "logs") if DATA_ROOT else (PROJECT_ROOT / "logs")

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RuntimePathTests(unittest.TestCase):
    def test_data_root_environment_moves_all_mutable_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, STARVELL_DATA_DIR=tmp)
            script = (
                "import json; from tg_bot_exfa.paths import "
                "CONFIG_PATH,DATABASE_PATH,PLUGINS_PATH,PLUGIN_STATE_PATH,LOGS_PATH; "
                "print(json.dumps([str(CONFIG_PATH),str(DATABASE_PATH),str(PLUGINS_PATH),"
                "str(PLUGIN_STATE_PATH),str(LOGS_PATH)]))"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                check=True,
                text=True,
                capture_output=True,
            )

        paths = [Path(value) for value in json.loads(result.stdout)]
        root = Path(tmp).resolve()
        self.assertTrue(all(path == root or root in path.parents for path in paths))


if __name__ == "__main__":
    unittest.main()

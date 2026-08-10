import asyncio
import os
import sys

from tg_bot_exfa.paths import PROJECT_ROOT


async def restart_process_after(delay: float = 1.0) -> None:
    await asyncio.sleep(max(0.0, delay))
    run_path = str(PROJECT_ROOT / "run_bot.py")
    os.execv(sys.executable, [sys.executable, run_path])


def schedule_restart(delay: float = 1.0) -> asyncio.Task:
    return asyncio.create_task(restart_process_after(delay))

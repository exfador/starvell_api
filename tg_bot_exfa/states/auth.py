from aiogram.fsm.state import StatesGroup, State


class StartFlow(StatesGroup):
    waiting_password = State()
    choosing_language = State()
    changing_password = State()
    changing_session = State()
    changing_token = State()
    changing_prefix = State()
    changing_welcome_text = State()
    changing_welcome_cooldown = State()



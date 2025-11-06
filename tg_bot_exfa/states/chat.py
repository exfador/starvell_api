from aiogram.fsm.state import State, StatesGroup


class ChatReply(StatesGroup):
    waiting_text = State()
    choosing_template = State()



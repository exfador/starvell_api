from aiogram.fsm.state import StatesGroup, State


class AutodeliveryFlow(StatesGroup):
    adding_name = State()
    waiting_file = State()





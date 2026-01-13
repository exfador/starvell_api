from aiogram.fsm.state import StatesGroup, State


class PluginsFlow(StatesGroup):
	waiting_upload = State()



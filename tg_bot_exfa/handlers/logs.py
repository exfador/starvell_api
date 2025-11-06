from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
import logging


router = Router()
log = logging.getLogger("exfador.actions")


@router.message()
async def log_any_message(message: Message, state: FSMContext):
    state_name = await state.get_state()
    content = message.text if message.text is not None else f"<{message.content_type}>"
    log.info(f"msg user_id={message.from_user.id} chat_id={message.chat.id} state={state_name} text={content}")


@router.callback_query()
async def log_any_callback(callback: CallbackQuery, state: FSMContext):
    state_name = await state.get_state()
    data = callback.data
    log.info(f"cb user_id={callback.from_user.id} chat_id={callback.message.chat.id if callback.message else '-'} state={state_name} data={data}")
    await callback.answer()



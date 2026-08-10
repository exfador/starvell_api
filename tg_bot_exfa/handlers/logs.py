from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
import logging
from tg_bot_exfa.middleware import protect_router


router = Router()
protect_router(router)
log = logging.getLogger("exfador.actions")


@router.message()
async def log_any_message(message: Message, state: FSMContext):
    state_name = await state.get_state()
    log.info(
        "msg user_id=%s chat_id=%s state=%s content_type=%s",
        message.from_user.id,
        message.chat.id,
        state_name,
        message.content_type,
    )


@router.callback_query()
async def log_any_callback(callback: CallbackQuery, state: FSMContext):
    state_name = await state.get_state()
    data = callback.data
    log.info(f"cb user_id={callback.from_user.id} chat_id={callback.message.chat.id if callback.message else '-'} state={state_name} data={data}")
    await callback.answer()



import os
import asyncio
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, LinkPreviewOptions
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

import tg_bot_exfa.app as app
from tg_bot_exfa.exf_langue.strings import Translations
from tg_bot_exfa.keyboards.menus import Keyboards
from tg_bot_exfa.states.plugins import PluginsFlow
from tg_bot_exfa.plugins import PluginContext


router = Router()
tr = Translations()
kb = Keyboards()


@router.callback_query(F.data == "menu:plugins")
async def open_plugins_menu(callback: CallbackQuery, state: FSMContext):
	cfg = app.app_context.config
	user = await app.app_context.db.get_user(callback.from_user.id)
	lang = user.get("language") or cfg.default_language
	await state.clear()
	await callback.message.edit_text(tr.t(lang, "plugins_title"), reply_markup=kb.plugins_menu(lambda k: tr.t(lang, k)).as_markup())


@router.callback_query(F.data == "plugins:add")
async def start_add_plugin(callback: CallbackQuery, state: FSMContext):
	cfg = app.app_context.config
	user = await app.app_context.db.get_user(callback.from_user.id)
	lang = user.get("language") or cfg.default_language
	await state.set_state(PluginsFlow.waiting_upload)
	await state.update_data(last_message_id=callback.message.message_id, last_chat_id=callback.message.chat.id)
	builder = InlineKeyboardBuilder()
	builder.button(text=tr.t(lang, "btn_cancel"), callback_data="plugins:cancel")
	text = f"{tr.t(lang, 'plugins_add_prompt')}\n\n{tr.t(lang, 'plugins_add_warning')}"
	await callback.message.edit_text(text, reply_markup=builder.as_markup(), link_preview_options=LinkPreviewOptions(is_disabled=True))


@router.callback_query(F.data == "plugins:cancel")
async def cancel_add_plugin(callback: CallbackQuery, state: FSMContext):
	await state.clear()
	await open_plugins_menu(callback, state)


def _safe_file_name(name: str) -> str:
	base = os.path.basename(name).strip()
	allowed = []
	for ch in base:
		if ch.isalnum() or ch in ("-", "_", ".",):
			allowed.append(ch)
	file = "".join(allowed) or "plugin.py"
	if not file.lower().endswith(".py"):
		file += ".py"
	return file


@router.message(PluginsFlow.waiting_upload, F.document)
async def handle_plugin_upload(message: Message, state: FSMContext):
	cfg = app.app_context.config
	user = await app.app_context.db.get_user(message.from_user.id)
	lang = user.get("language") or cfg.default_language
	doc = message.document
	if not doc or not str(doc.file_name or "").lower().endswith(".py"):
		await message.answer(tr.t(lang, "plugins_add_failed", error="wrong file"))
		return
	try:
		file = await message.bot.get_file(doc.file_id)
		dest_name = _safe_file_name(doc.file_name or "plugin.py")
		dest_path = os.path.join("plugins", dest_name)
		await message.bot.download_file(file.file_path, destination=dest_path)
	except Exception as e:
		await message.answer(tr.t(lang, "plugins_add_failed", error=str(e)))
		return
	try:
		pm = app.app_context.plugin_manager
		meta = pm.load_one(dest_path)
		pm.enable(meta.uuid)
		try:
			from tg_bot_exfa.monitor import load_config as load_osnova_config
			cfg2 = load_osnova_config()
		except Exception:
			cfg2 = {}
		ctx = PluginContext(session_cookie=(cfg2 or {}).get("SESSION_COOKIE", ""), db=app.app_context.db, config=cfg2 or {})
		await pm.dispatch_init(ctx)
		try:
			base_cmds = [
				BotCommand(command="start", description="Запуск"),
				BotCommand(command="restart", description="Перезапуск"),
				BotCommand(command="update", description="Обновление"),
			]
			plugin_cmds: list[BotCommand] = []
			seen = {c.command for c in base_cmds}
			for name, meta_cmd in pm.commands.items():
				cmd = str(name or "").strip().lower()
				if not cmd or cmd in seen:
					continue
				desc = str(meta_cmd.get("description") or "").strip()[:256]
				plugin_cmds.append(BotCommand(command=cmd, description=desc or "Plugin"))
				seen.add(cmd)
			await message.bot.set_my_commands(base_cmds + plugin_cmds)
		except Exception:
			pass
		data = await state.get_data()
		target_chat = data.get("last_chat_id") or message.chat.id
		target_msg = data.get("last_message_id") or message.message_id
		back_kb = InlineKeyboardBuilder()
		back_kb.button(text=tr.t(lang, "btn_back"), callback_data="plugins:list:1")
		try:
			await message.bot.edit_message_text(
				tr.t(lang, "plugins_add_success", name=meta.name, version=meta.version),
				chat_id=target_chat,
				message_id=target_msg,
				reply_markup=back_kb.as_markup(),
			)
		except Exception:
			await message.answer(tr.t(lang, "plugins_add_success", name=meta.name, version=meta.version), reply_markup=back_kb.as_markup())
	except Exception as e:
		data = await state.get_data()
		target_chat = data.get("last_chat_id") or message.chat.id
		target_msg = data.get("last_message_id") or message.message_id
		back_kb = InlineKeyboardBuilder()
		back_kb.button(text=tr.t(lang, "btn_back"), callback_data="plugins:list:1")
		try:
			await message.bot.edit_message_text(
				tr.t(lang, "plugins_add_failed", error=str(e)),
				chat_id=target_chat,
				message_id=target_msg,
				reply_markup=back_kb.as_markup(),
			)
		except Exception:
			await message.answer(tr.t(lang, "plugins_add_failed", error=str(e)), reply_markup=back_kb.as_markup())
	await state.clear()


@router.callback_query(F.data.startswith("plugins:list"))
async def list_plugins(callback: CallbackQuery, state: FSMContext):
	cfg = app.app_context.config
	user = await app.app_context.db.get_user(callback.from_user.id)
	lang = user.get("language") or cfg.default_language
	pm = app.app_context.plugin_manager
	items_all = []
	for meta in sorted(pm.plugins.values(), key=lambda m: (m.name or "").lower()):
		status = tr.t(lang, "plugin_enabled") if meta.enabled else tr.t(lang, "plugin_disabled")
		if meta.module is None and meta.load_error:
			status = tr.t(lang, "plugin_load_failed")
		items_all.append((meta.uuid, f"{meta.name} v{meta.version} — {status}"))
	total = len(items_all)
	PAGE_SIZE = 8
	page = 1
	try:
		parts = (callback.data or "").split(":")
		if len(parts) >= 3:
			page = max(1, int(parts[2]))
	except Exception:
		page = 1
	total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
	if page > total_pages:
		page = total_pages
	start = (page - 1) * PAGE_SIZE
	stop = start + PAGE_SIZE
	items = items_all[start:stop]
	if total == 0:
		builder = InlineKeyboardBuilder()
		builder.button(text=tr.t(lang, "btn_back"), callback_data="menu:plugins")
		await callback.message.edit_text(tr.t(lang, "plugins_list_empty"), reply_markup=builder.as_markup())
		return
	builder = InlineKeyboardBuilder()
	for uuid, label in items:
		builder.button(text=label, callback_data=f"plugins:item:{uuid}")
	nav_added = False
	if total_pages > 1:
		if page > 1:
			builder.button(text=tr.t(lang, "btn_prev_page"), callback_data=f"plugins:list:{page-1}")
		if page < total_pages:
			builder.button(text=tr.t(lang, "btn_next_page"), callback_data=f"plugins:list:{page+1}")
		nav_added = True
	builder.button(text=tr.t(lang, "btn_back"), callback_data="menu:plugins")
	if nav_added:
		builder.adjust(1, 2, 1)
	else:
		builder.adjust(1, 1)
	title_lines = [tr.t(lang, "plugins_list_title")]
	try:
		title_lines.append(tr.t(lang, "templates_page", current=page, total=total_pages))
	except Exception:
		pass
	await callback.message.edit_text("\n".join(title_lines), reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("plugins:item:"))
async def plugin_item(callback: CallbackQuery, state: FSMContext):
	cfg = app.app_context.config
	user = await app.app_context.db.get_user(callback.from_user.id)
	lang = user.get("language") or cfg.default_language
	uuid = callback.data.split(":")[-1]
	pm = app.app_context.plugin_manager
	meta = pm.plugins.get(uuid)
	if not meta:
		await list_plugins(callback, state)
		return
	builder = InlineKeyboardBuilder()
	if meta.enabled:
		builder.button(text=tr.t(lang, "btn_plugin_disable"), callback_data=f"plugins:toggle:{uuid}")
	else:
		builder.button(text=tr.t(lang, "btn_plugin_enable"), callback_data=f"plugins:toggle:{uuid}")
	builder.button(text=tr.t(lang, "btn_plugin_remove"), callback_data=f"plugins:remove:{uuid}")
	builder.button(text=tr.t(lang, "btn_back"), callback_data="plugins:list")
	builder.adjust(1, 1, 1)
	lines = []
	lines.append(f"{tr.t(lang, 'plugin_label_name')}: <code>{meta.name}</code>")
	lines.append(f"{tr.t(lang, 'plugin_label_uuid')}: <code>{meta.uuid}</code>")
	lines.append(f"{tr.t(lang, 'plugin_label_version')}: <code>{meta.version}</code>")
	lines.append(f"{tr.t(lang, 'plugin_label_creator')}: <code>{meta.credits or '-'}</code>")
	desc = (meta.description or "").strip()
	text = "\n".join(lines) + ("\n\n" + desc if desc else "")
	await callback.message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("plugins:toggle:"))
async def plugin_toggle(callback: CallbackQuery, state: FSMContext):
	cfg = app.app_context.config
	user = await app.app_context.db.get_user(callback.from_user.id)
	lang = user.get("language") or cfg.default_language
	uuid = callback.data.split(":")[-1]
	pm = app.app_context.plugin_manager
	meta = pm.plugins.get(uuid)
	if not meta:
		await list_plugins(callback, state)
		return
	if meta.enabled:
		pm.disable(uuid)
		await callback.message.edit_text(tr.t(lang, "plugin_toggled_off"))
	else:
		pm.enable(uuid)
		await callback.message.edit_text(tr.t(lang, "plugin_toggled_on"))
	try:
		base_cmds = [
			BotCommand(command="start", description="Запуск"),
			BotCommand(command="restart", description="Перезапуск"),
			BotCommand(command="update", description="Обновление"),
		]
		plugin_cmds: list[BotCommand] = []
		seen = {c.command for c in base_cmds}
		for name, meta_cmd in pm.commands.items():
			cmd = str(name or "").strip().lower()
			if not cmd or cmd in seen:
				continue
			desc = str(meta_cmd.get("description") or "").strip()[:256]
			plugin_cmds.append(BotCommand(command=cmd, description=desc or "Plugin"))
			seen.add(cmd)
		await callback.message.bot.set_my_commands(base_cmds + plugin_cmds)
	except Exception:
		pass
	await asyncio.sleep(1)
	await list_plugins(callback, state)


@router.callback_query(F.data.startswith("plugins:remove:"))
async def plugin_remove(callback: CallbackQuery, state: FSMContext):
	cfg = app.app_context.config
	user = await app.app_context.db.get_user(callback.from_user.id)
	lang = user.get("language") or cfg.default_language
	uuid = callback.data.split(":")[-1]
	pm = app.app_context.plugin_manager
	pm.remove(uuid)
	try:
		base_cmds = [
			BotCommand(command="start", description="Запуск"),
			BotCommand(command="restart", description="Перезапуск"),
			BotCommand(command="update", description="Обновление"),
		]
		plugin_cmds: list[BotCommand] = []
		seen = {c.command for c in base_cmds}
		for name, meta_cmd in pm.commands.items():
			cmd = str(name or "").strip().lower()
			if not cmd or cmd in seen:
				continue
			desc = str(meta_cmd.get("description") or "").strip()[:256]
			plugin_cmds.append(BotCommand(command=cmd, description=desc or "Plugin"))
			seen.add(cmd)
		await callback.message.bot.set_my_commands(base_cmds + plugin_cmds)
	except Exception:
		pass
	await callback.message.edit_text(tr.t(lang, "plugin_removed"))
	await asyncio.sleep(1)
	await list_plugins(callback, state)



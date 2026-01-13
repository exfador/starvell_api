import asyncio
import importlib.util
import json
import os
from dataclasses import dataclass
from types import ModuleType
from typing import Any
import logging
import re
import sys


@dataclass
class PluginMeta:
	name: str
	uuid: str
	version: str
	description: str
	credits: str | None
	path: str
	module: ModuleType | None
	enabled: bool
	load_error: str | None = None


class PluginContext:
	def __init__(self, session_cookie: str, db: Any, config: dict[str, Any]):
		self.session_cookie = session_cookie
		self.db = db
		self.config = config


class PluginManager:
	def __init__(self, root_dir: str, state_path: str):
		self.root_dir = root_dir
		self.state_path = state_path
		self.plugins: dict[str, PluginMeta] = {}
		self.order_handlers: list[tuple[str, Any]] = []
		self.message_handlers: list[tuple[str, Any]] = []
		self.disabled: set[str] = set()
		self.commands: dict[str, dict[str, Any]] = {}
		self._logger = logging.getLogger("exfador.plugins")

	def _ensure_dirs(self) -> None:
		os.makedirs(self.root_dir, exist_ok=True)
		state_dir = os.path.dirname(self.state_path)
		if state_dir:
			os.makedirs(state_dir, exist_ok=True)
		try:
			abs_plugins = os.path.abspath(self.root_dir)
			if abs_plugins not in sys.path:
				sys.path.insert(0, abs_plugins)
		except Exception:
			pass

	def _load_state(self) -> None:
		self._ensure_dirs()
		if not os.path.exists(self.state_path):
			self.disabled = set()
			return
		try:
			with open(self.state_path, "r", encoding="utf-8") as f:
				data = json.load(f) or {}
			disabled = data.get("disabled") or []
			self.disabled = set([str(x) for x in disabled if isinstance(x, str)])
		except Exception:
			self.disabled = set()

	def _save_state(self) -> None:
		self._ensure_dirs()
		data = {"disabled": sorted(self.disabled)}
		tmp_path = self.state_path + ".tmp"
		with open(tmp_path, "w", encoding="utf-8") as f:
			json.dump(data, f, ensure_ascii=False, indent=2)
		os.replace(tmp_path, self.state_path)

	def _import_module_from_file(self, file_path: str) -> ModuleType:
		try:
			plugin_dir = os.path.abspath(os.path.dirname(file_path))
			if plugin_dir and plugin_dir not in sys.path:
				sys.path.insert(0, plugin_dir)
			abs_plugins = os.path.abspath(self.root_dir)
			if abs_plugins not in sys.path:
				sys.path.insert(0, abs_plugins)
			if "plugins" not in sys.modules:
				import types as _types
				_pkg = _types.ModuleType("plugins")
				_pkg.__path__ = [abs_plugins]
				sys.modules["plugins"] = _pkg
		except Exception:
			pass
		stem = os.path.splitext(os.path.basename(file_path))[0]
		mod_name = f"plugins.{stem}"
		spec = importlib.util.spec_from_file_location(mod_name, file_path)
		if spec is None or spec.loader is None:
			raise RuntimeError("spec error")
		module = importlib.util.module_from_spec(spec)
		sys.modules[mod_name] = module
		spec.loader.exec_module(module)
		return module

	def _extract_meta_text(self, file_path: str) -> dict[str, str]:
		try:
			with open(file_path, "r", encoding="utf-8") as f:
				text = f.read(10000)
		except Exception:
			return {}
		pat = re.compile(r'^\s*(NAME|UUID|VERSION|DESCRIPTION|CREDITS)\s*=\s*[\'"](.+?)[\'"]\s*$', re.MULTILINE)
		data: dict[str, str] = {}
		for m in pat.finditer(text):
			k = m.group(1)
			v = m.group(2)
			if k and v:
				data[k] = v
		return data

	def _validate_module(self, module: ModuleType) -> tuple[str, str, str, str, str | None]:
		name = getattr(module, "NAME", None)
		uuid = getattr(module, "UUID", None)
		version = getattr(module, "VERSION", None)
		description = getattr(module, "DESCRIPTION", "")
		credits = getattr(module, "CREDITS", None)
		if not (name and uuid and version):
			info = getattr(module, "INFO", None)
			if isinstance(info, dict):
				name = name or info.get("name") or info.get("NAME")
				uuid = uuid or info.get("uuid") or info.get("UUID")
				version = version or info.get("version") or info.get("VERSION")
				description = description or info.get("description") or info.get("DESCRIPTION") or ""
				credits = credits or info.get("credits") or info.get("CREDITS")
		if not (name and uuid and version):
			for fn_name in ("plugin_info", "get_plugin_info", "about", "info"):
				fn = getattr(module, fn_name, None)
				if callable(fn):
					try:
						meta = fn()
						if isinstance(meta, dict):
							name = name or meta.get("name") or meta.get("NAME")
							uuid = uuid or meta.get("uuid") or meta.get("UUID")
							version = version or meta.get("version") or meta.get("VERSION")
							description = description or meta.get("description") or meta.get("DESCRIPTION") or ""
							credits = credits or meta.get("credits") or meta.get("CREDITS")
						elif isinstance(meta, (list, tuple)):
							try:
								if len(meta) >= 1 and not name:
									name = meta[0]
								if len(meta) >= 2 and not uuid:
									uuid = meta[1]
								if len(meta) >= 3 and not version:
									version = meta[2]
								if len(meta) >= 4 and not description:
									description = meta[3]
								if len(meta) >= 5 and not credits:
									credits = meta[4]
							except Exception:
								pass
					except Exception:
						continue
		if not isinstance(name, str) or not name.strip():
			raise ValueError("NAME")
		if not isinstance(uuid, str) or not uuid.strip():
			raise ValueError("UUID")
		if not isinstance(version, str) or not version.strip():
			raise ValueError("VERSION")
		credits_str = str(credits).strip() if isinstance(credits, str) else None
		return name.strip(), uuid.strip(), version.strip(), str(description or "").strip(), credits_str

	def _register_commands_for_module(self, module: ModuleType, uuid: str) -> None:
		fn = getattr(module, "register_commands", None)
		if not callable(fn):
			return
		try:
			registered = fn()
		except Exception:
			return
		if not isinstance(registered, (list, tuple)):
			return
		for item in registered:
			try:
				if isinstance(item, dict):
					name = str(item.get("name") or "").strip().lstrip("/").lower()
					handler = item.get("handler")
					desc = str(item.get("description") or "").strip()
				elif isinstance(item, (list, tuple)) and len(item) >= 2:
					name = str(item[0] or "").strip().lstrip("/").lower()
					handler = item[1]
					desc = str(item[2]).strip() if len(item) >= 3 else ""
				else:
					continue
				if not name or not callable(handler):
					continue
				self.commands[name] = {"uuid": uuid, "handler": handler, "description": desc}
			except Exception:
				continue

	def _unregister_commands_by_uuid(self, uuid: str) -> None:
		to_del = [cmd for cmd, meta in self.commands.items() if meta.get("uuid") == uuid]
		for cmd in to_del:
			self.commands.pop(cmd, None)

	def _register_handlers_for_module(self, module: ModuleType, uuid: str) -> None:
		try:
			bind_orders = getattr(module, "NEW_ORDER_CXH", None)
			if isinstance(bind_orders, (list, tuple)):
				for fn in bind_orders:
					if callable(fn):
						self.order_handlers.append((uuid, fn))
		except Exception:
			pass
		try:
			bind_messages = getattr(module, "NEW_MESSAGE_CXH", None)
			if isinstance(bind_messages, (list, tuple)):
				for fn in bind_messages:
					if callable(fn):
						self.message_handlers.append((uuid, fn))
		except Exception:
			pass

	def _unregister_handlers_by_uuid(self, uuid: str) -> None:
		self.order_handlers = [(u, fn) for (u, fn) in self.order_handlers if u != uuid]
		self.message_handlers = [(u, fn) for (u, fn) in self.message_handlers if u != uuid]

	async def dispatch_command(self, name: str, message: Any, args: list[str], ctx: Any) -> Any:
		meta = self.commands.get(name.lower())
		if not meta:
			return None
		handler = meta.get("handler")
		try:
			if asyncio.iscoroutinefunction(handler):
				return await handler(message, args, ctx)
			res = handler(message, args, ctx)
			if asyncio.iscoroutine(res):
				return await res
			return res
		except Exception:
			return None

	def load_all(self) -> None:
		self._load_state()
		self.plugins.clear()
		self.order_handlers.clear()
		self.message_handlers.clear()
		self.commands.clear()
		if not os.path.exists(self.root_dir):
			return
		for file in os.listdir(self.root_dir):
			lower = file.lower()
			if not (lower.endswith(".py") or lower.endswith(".pyc")):
				continue
			full = os.path.join(self.root_dir, file)
			try:
				module = self._import_module_from_file(full)
				name, uuid, version, description, credits = self._validate_module(module)
				if uuid in self.plugins:
					try:
						existing = self.plugins[uuid]
						self._logger.error("duplicate_uuid uuid=%s skip_file=%s kept=%s", uuid, full, existing.path)
					except Exception:
						self._logger.error("duplicate_uuid uuid=%s skip_file=%s", uuid, full)
					continue
				enabled = uuid not in self.disabled
				self.plugins[uuid] = PluginMeta(name, uuid, version, description, credits, full, module, enabled, None)
				self._register_commands_for_module(module, uuid)
				self._register_handlers_for_module(module, uuid)
				try:
					self._logger.info("plugin_loaded name=%s version=%s uuid=%s enabled=%s path=%s", name, version, uuid, enabled, full)
				except Exception:
					pass
			except Exception as e:
				meta_guess = self._extract_meta_text(full)
				name = meta_guess.get("NAME") or os.path.basename(full)
				uuid = meta_guess.get("UUID") or f"invalid:{full}"
				version = meta_guess.get("VERSION") or "unknown"
				description = meta_guess.get("DESCRIPTION") or ""
				credits = meta_guess.get("CREDITS")
				if uuid in self.plugins:
					continue
				enabled = uuid not in self.disabled
				self.plugins[uuid] = PluginMeta(name, uuid, version, description, credits, full, None, enabled, str(e))
				try:
					self._logger.warning("plugin_load_failed name=%s uuid=%s path=%s error=%s", name, uuid, full, e)
				except Exception:
					pass

	def load_one(self, file_path: str) -> PluginMeta:
		self._load_state()
		try:
			module = self._import_module_from_file(file_path)
			name, uuid, version, description, credits = self._validate_module(module)
		except Exception as e:
			meta_guess = self._extract_meta_text(file_path)
			name = meta_guess.get("NAME") or os.path.basename(file_path)
			uuid = meta_guess.get("UUID") or f"invalid:{file_path}"
			version = meta_guess.get("VERSION") or "unknown"
			description = meta_guess.get("DESCRIPTION") or ""
			credits = meta_guess.get("CREDITS")
			if uuid in self.plugins:
				raise ValueError(f"Duplicate UUID: {uuid}")
			enabled = uuid not in self.disabled
			meta = PluginMeta(name, uuid, version, description, credits, file_path, None, enabled, str(e))
			self.plugins[uuid] = meta
			try:
				self._logger.warning("plugin_load_failed name=%s uuid=%s path=%s error=%s", name, uuid, file_path, e)
			except Exception:
				pass
			return meta
		if uuid in self.plugins:
			raise ValueError(f"Duplicate UUID: {uuid}")
		enabled = uuid not in self.disabled
		meta = PluginMeta(name, uuid, version, description, credits, file_path, module, enabled, None)
		self.plugins[uuid] = meta
		self._register_commands_for_module(module, uuid)
		self._register_handlers_for_module(module, uuid)
		try:
			self._logger.info("plugin_loaded name=%s version=%s uuid=%s enabled=%s path=%s", name, version, uuid, enabled, file_path)
		except Exception:
			pass
		return meta

	def enable(self, uuid: str) -> bool:
		if uuid in self.disabled:
			self.disabled.remove(uuid)
			self._save_state()
		if uuid in self.plugins:
			self.plugins[uuid].enabled = True
			try:
				self._register_commands_for_module(self.plugins[uuid].module, uuid)
			except Exception:
				pass
			try:
				meta = self.plugins[uuid]
				self._logger.info("plugin_enabled name=%s version=%s uuid=%s", meta.name, meta.version, uuid)
			except Exception:
				pass
			return True
		return False

	def disable(self, uuid: str) -> bool:
		self.disabled.add(uuid)
		self._save_state()
		if uuid in self.plugins:
			self.plugins[uuid].enabled = False
			self._unregister_commands_by_uuid(uuid)
			try:
				meta = self.plugins[uuid]
				self._logger.info("plugin_disabled name=%s version=%s uuid=%s", meta.name, meta.version, uuid)
			except Exception:
				pass
			return True
		return False

	def remove(self, uuid: str) -> bool:
		if uuid in self.plugins:
			try:
				os.remove(self.plugins[uuid].path)
			except Exception:
				pass
			self._unregister_handlers_by_uuid(uuid)
			self.plugins.pop(uuid, None)
		self._unregister_commands_by_uuid(uuid)
		if uuid in self.disabled:
			self.disabled.remove(uuid)
			self._save_state()
		try:
			self._logger.info("plugin_removed uuid=%s", uuid)
		except Exception:
			pass
		return True

	async def _maybe_call(self, fn, *args, **kwargs):
		try:
			if asyncio.iscoroutinefunction(fn):
				return await fn(*args, **kwargs)
			res = fn(*args, **kwargs)
			if asyncio.iscoroutine(res):
				return await res
			return res
		except Exception:
			return None

	async def dispatch_init(self, ctx: PluginContext) -> None:
		tasks = []
		for meta in list(self.plugins.values()):
			if not meta.enabled:
				continue
			fn = getattr(meta.module, "on_init", None)
			if callable(fn):
				tasks.append(self._maybe_call(fn, ctx))
		if tasks:
			await asyncio.gather(*tasks, return_exceptions=True)

	async def dispatch_order_created(self, order: dict, ctx: PluginContext) -> None:
		tasks = []
		for uuid, fn in list(self.order_handlers):
			meta = self.plugins.get(uuid)
			if not meta or not meta.enabled:
				continue
			tasks.append(self._maybe_call(fn, order, ctx))
		if tasks:
			await asyncio.gather(*tasks, return_exceptions=True)

	async def dispatch_chat_message(self, text: str, chat_id: str, ctx: PluginContext) -> None:
		tasks = []
		for uuid, fn in list(self.message_handlers):
			meta = self.plugins.get(uuid)
			if not meta or not meta.enabled:
				continue
			tasks.append(self._maybe_call(fn, text, chat_id, ctx))
		if tasks:
			await asyncio.gather(*tasks, return_exceptions=True)

	async def dispatch_callback(self, callback: Any, state: Any, ctx: PluginContext) -> None:
		for uuid, meta in self.plugins.items():
			if not meta.enabled or not meta.module:
				continue
			fn = getattr(meta.module, "handle_callback", None)
			if callable(fn):
				try:
					await self._maybe_call(fn, callback, state, ctx)
				except Exception:
					pass

	async def dispatch_message(self, message: Any, state: Any, ctx: PluginContext) -> None:
		for uuid, meta in self.plugins.items():
			if not meta.enabled or not meta.module:
				continue
			fn = getattr(meta.module, "handle_message", None)
			if callable(fn):
				try:
					await self._maybe_call(fn, message, state, ctx)
				except Exception:
					pass



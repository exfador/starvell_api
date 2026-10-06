import asyncio
import logging
import urllib.error
import urllib.request

import socketio

from api.auth_cookies import authentication_cookies

STARVELL_ORIGIN = "https://starvell.com"
QRATOR_SOCKET_ORIGIN = "https://starvell.com:8443"
CHATS_NAMESPACE = "/chats"
NOTIFICATIONS_NAMESPACE = "/user-notifications"
NAMESPACES = (CHATS_NAMESPACE, NOTIFICATIONS_NAMESPACE)
CHAT_EVENTS = frozenset({"message_created"})
ORDER_EVENTS = frozenset({"sale_update"})
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
CONNECT_TIMEOUT_SECONDS = 15
SESSION_CHECK_SECONDS = 15
RETRY_MIN_SECONDS = 5
RETRY_MAX_SECONDS = 300
UNAUTHORIZED_RETRY_SECONDS = 300
DISABLED_CHECK_SECONDS = 60

log = logging.getLogger("exfador.realtime")


class RealtimeSignals:
    def __init__(self) -> None:
        self.chats = asyncio.Event()
        self.orders = asyncio.Event()
        self.connected = False
        self.confirmed = False

    def handle(self, namespace: str, event: str) -> None:
        if namespace == CHATS_NAMESPACE and event in CHAT_EVENTS:
            self.chats.set()
        elif namespace == NOTIFICATIONS_NAMESPACE and event in ORDER_EVENTS:
            self.orders.set()
        else:
            return
        self.confirmed = True

    def wake_all(self) -> None:
        self.chats.set()
        self.orders.set()

    def reset(self) -> None:
        self.connected = False
        self.confirmed = False


signals = RealtimeSignals()


def socket_origin(timeout: float = 10) -> str:
    request = urllib.request.Request(STARVELL_ORIGIN + "/", method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            server = response.headers.get("server") or ""
    except urllib.error.HTTPError as error:
        server = error.headers.get("server") or ""
    except (OSError, ValueError):
        server = ""
    return QRATOR_SOCKET_ORIGIN if server.strip().upper() == "QRATOR" else STARVELL_ORIGIN


def _cookie_header(session_cookie: str) -> str:
    return "; ".join(f"{name}={value}" for name, value in authentication_cookies(session_cookie).items())


def _enabled(cfg: dict) -> bool:
    return bool(cfg.get("REALTIME_ENABLED", True)) and bool(str(cfg.get("SESSION_COOKIE") or "").strip())


async def _connect_once(session_cookie: str, config_loader, events: RealtimeSignals, origin: str | None = None) -> str:
    try:
        cookie_header = _cookie_header(session_cookie)
    except ValueError as exc:
        log.warning("realtime_invalid_session error=%s", exc)
        return "unauthorized"
    origin = origin or await asyncio.to_thread(socket_origin)
    client = socketio.AsyncClient(reconnection=False)
    rejections: list = []

    def bind(namespace: str) -> None:
        async def on_event(event, *_payload):
            events.handle(namespace, event)

        async def on_connect_error(data=None):
            rejections.append(data)

        client.on("*", on_event, namespace=namespace)
        client.on("connect_error", on_connect_error, namespace=namespace)

    for namespace in NAMESPACES:
        bind(namespace)
    try:
        try:
            await client.connect(
                origin,
                headers={"Cookie": cookie_header, "Origin": STARVELL_ORIGIN, "User-Agent": USER_AGENT},
                transports=["websocket"],
                namespaces=list(NAMESPACES),
                wait_timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except socketio.exceptions.ConnectionError as exc:
            if any("unauthorized" in str(item).lower() for item in rejections):
                log.warning("realtime_unauthorized origin=%s: Starvell rejected the session", origin)
                return "unauthorized"
            log.warning("realtime_connect_failed origin=%s error=%s", origin, exc)
            return "failed"
        events.connected = True
        log.info("realtime_connected origin=%s namespaces=%s", origin, ",".join(NAMESPACES))
        events.wake_all()
        while client.connected:
            await asyncio.sleep(SESSION_CHECK_SECONDS)
            cfg = config_loader()
            if not _enabled(cfg) or str(cfg.get("SESSION_COOKIE") or "") != session_cookie:
                return "session_changed"
        log.info("realtime_disconnected origin=%s", origin)
        return "closed"
    finally:
        events.reset()
        try:
            await client.disconnect()
        except Exception:
            pass


async def run_realtime(config_loader, events: RealtimeSignals = signals) -> None:
    delay = RETRY_MIN_SECONDS
    while True:
        try:
            cfg = config_loader()
            if not _enabled(cfg):
                events.reset()
                await asyncio.sleep(DISABLED_CHECK_SECONDS)
                continue
            outcome = await _connect_once(str(cfg.get("SESSION_COOKIE") or ""), config_loader, events)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("realtime_failed error=%s", exc)
            outcome = "failed"
        if outcome in ("closed", "session_changed"):
            delay = RETRY_MIN_SECONDS
            wait = 0 if outcome == "session_changed" else RETRY_MIN_SECONDS
        elif outcome == "unauthorized":
            wait = UNAUTHORIZED_RETRY_SECONDS
        else:
            wait = delay
            delay = min(delay * 2, RETRY_MAX_SECONDS)
        await asyncio.sleep(wait)


async def probe(session_cookie: str, origin: str | None = None) -> dict:
    origin = origin or await asyncio.to_thread(socket_origin)
    client = socketio.AsyncClient(reconnection=False)
    rejections: list = []
    for namespace in NAMESPACES:
        client.on("connect_error", lambda data=None, ns=namespace: rejections.append((ns, data)), namespace=namespace)
    try:
        await client.connect(
            origin,
            headers={"Cookie": _cookie_header(session_cookie), "Origin": STARVELL_ORIGIN, "User-Agent": USER_AGENT},
            transports=["websocket"],
            namespaces=list(NAMESPACES),
            wait_timeout=CONNECT_TIMEOUT_SECONDS,
        )
        return {"origin": origin, "connected": sorted(client.namespaces), "rejected": []}
    except socketio.exceptions.ConnectionError:
        return {"origin": origin, "connected": [], "rejected": [f"{ns}: {data}" for ns, data in rejections]}
    finally:
        await client.disconnect()

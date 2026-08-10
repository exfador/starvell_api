from collections.abc import Mapping
from typing import Any


class StarvellApiError(RuntimeError):
    pass


class StarvellAuthenticationError(StarvellApiError):
    pass


class StarvellResponseError(StarvellApiError):
    pass


def _error_status(data: Mapping) -> int | None:
    for key in ("errorCode", "statusCode", "status"):
        try:
            status = int(data.get(key))
        except (TypeError, ValueError):
            continue
        if status >= 400:
            return status
    return None


def page_props(data: Any, operation: str) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise StarvellResponseError(f"{operation}: expected a JSON object")
    props = data.get("pageProps")
    if not isinstance(props, Mapping):
        next_props = data.get("props")
        if isinstance(next_props, Mapping):
            props = next_props.get("pageProps")
    if not isinstance(props, Mapping):
        raise StarvellResponseError(f"{operation}: response has no pageProps")
    props = dict(props)
    redirect = props.get("__N_REDIRECT")
    if redirect:
        raise StarvellAuthenticationError(f"{operation}: authentication required ({redirect})")
    status_code = _error_status(props)
    if props.get("error") or props.get("ok") is False or props.get("success") is False or status_code is not None:
        detail = f"HTTP {status_code}" if status_code else "server returned an error"
        raise StarvellResponseError(f"{operation}: {detail}")
    return props


def normalize_page_collection(data: Any, key: str, operation: str) -> dict[str, Any]:
    props = page_props(data, operation)
    candidates = [props.get(key)]
    for container_key in ("bff", "data", "result"):
        container = props.get(container_key)
        if isinstance(container, Mapping):
            candidates.append(container.get(key))
    for value in candidates:
        if isinstance(value, list):
            normalized = dict(data)
            normalized_props = dict(props)
            normalized_props[key] = value
            normalized["pageProps"] = normalized_props
            return normalized
    raise StarvellResponseError(f"{operation}: response has no {key} list")


def normalize_page_object(data: Any, key: str, operation: str) -> dict[str, Any]:
    props = page_props(data, operation)
    candidates = [props.get(key)]
    for container_key in ("bff", "data", "result"):
        container = props.get(container_key)
        if isinstance(container, Mapping):
            candidates.append(container.get(key))
    for value in candidates:
        if isinstance(value, Mapping):
            normalized = dict(data)
            normalized_props = dict(props)
            normalized_props[key] = dict(value)
            normalized["pageProps"] = normalized_props
            return normalized
    raise StarvellResponseError(f"{operation}: response has no {key} object")


def response_items(data: Any, operation: str) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, Mapping):
        raise StarvellResponseError(f"{operation}: expected a list or JSON object")
    ensure_success(data, operation)
    candidates: list[Any] = [data.get("items")]
    for container_key in ("messagesListResult", "result", "data"):
        container = data.get(container_key)
        if isinstance(container, Mapping):
            candidates.append(container.get("items"))
    for value in candidates:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    raise StarvellResponseError(f"{operation}: response has no items list")


def ensure_success(data: Any, operation: str) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise StarvellResponseError(f"{operation}: expected a JSON object")
    status_code = _error_status(data)
    if data.get("error") or data.get("ok") is False or data.get("success") is False or status_code is not None:
        detail = data.get("message") or status_code or "server returned an error"
        raise StarvellResponseError(f"{operation}: {detail}")
    return dict(data)

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping


_BRACED_VARIABLE = re.compile(r"(?<!\{)\{([a-z][a-z0-9_]*)\}(?!\})")
_LEGACY_VARIABLE = re.compile(
    r"\$(full_date_text|full_time|date_text|message_text|order_id|chat_id|"
    r"username|buyer|product|quantity|game|category|date|time)\b"
)

_ALIASES = {
    "username": "buyer",
}


def _clock_values(now: datetime) -> dict[str, str]:
    return {
        "date": now.strftime("%d.%m.%Y"),
        "date_text": now.strftime("%d.%m.%Y"),
        "full_date_text": now.strftime("%d.%m.%Y"),
        "time": now.strftime("%H:%M"),
        "full_time": now.strftime("%H:%M:%S"),
    }


def render_template(
    template: str,
    values: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> str:
    """Render only explicitly named placeholders without evaluating expressions.

    Missing and unknown placeholders are deliberately preserved so a typo is visible
    to the operator instead of silently deleting part of a customer message.
    """

    source = str(template or "")
    available: dict[str, Any] = _clock_values(now or datetime.now())
    if values:
        available.update(values)

    def lookup(name: str, original: str) -> str:
        canonical = _ALIASES.get(name, name)
        value = available.get(canonical)
        return original if value is None else str(value)

    source = _BRACED_VARIABLE.sub(
        lambda match: lookup(match.group(1), match.group(0)),
        source,
    )
    return _LEGACY_VARIABLE.sub(
        lambda match: lookup(match.group(1), match.group(0)),
        source,
    )

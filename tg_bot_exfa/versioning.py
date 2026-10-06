import re

import requests

GITHUB_TAGS_URL = "https://api.github.com/repos/exfador/starvell_api/tags?per_page=100"
GITHUB_HEADERS = {"accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_VERSION_PATTERN = re.compile(r"v?(\d+(?:\.\d+)*)")


def parse_version(value) -> tuple[int, ...] | None:
    match = _VERSION_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def latest_version(names) -> str | None:
    candidates = [(parse_version(name), str(name).strip()) for name in names or []]
    valid = [(version, name) for version, name in candidates if version is not None]
    return max(valid)[1] if valid else None


def is_newer(candidate, current) -> bool:
    candidate_version = parse_version(candidate)
    current_version = parse_version(current)
    return candidate_version is not None and (current_version is None or candidate_version > current_version)


def fetch_latest_tag(timeout: float = 10) -> str | None:
    response = requests.get(GITHUB_TAGS_URL, headers=GITHUB_HEADERS, timeout=timeout)
    if response.status_code != 200:
        return None
    items = response.json() or []
    return latest_version(str((item or {}).get("name") or "") for item in items if isinstance(item, dict))

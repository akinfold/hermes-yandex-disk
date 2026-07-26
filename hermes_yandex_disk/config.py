"""Configuration, credentials and the action allow-list.

The allow-list is applied at *registration* time, not inside the handlers: a
tool that was never registered cannot be called, cannot be talked into being
called, and does not spend context.
"""

from __future__ import annotations

from ._compat import get_provider_env
from .client import API_BASE, DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT, YandexDiskClient
from .paths import normalize_root

TOKEN_ENV = "YANDEX_DISK_OAUTH_TOKEN"
TOKEN_ENV_ALIAS = "YANDEX_DISK_API_KEY"
ROOT_ENV = "YANDEX_DISK_ROOT"
ACTIONS_ENV = "YANDEX_DISK_ACTIONS"
BASE_URL_ENV = "YANDEX_DISK_BASE_URL"
TIMEOUT_ENV = "YANDEX_DISK_TIMEOUT"
MAX_READ_BYTES_ENV = "YANDEX_DISK_MAX_READ_BYTES"
MAX_DOWNLOAD_BYTES_ENV = "YANDEX_DISK_MAX_DOWNLOAD_BYTES"

DEFAULT_MAX_READ_BYTES = 1024 * 1024
DEFAULT_MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
ACTIONS: tuple[str, ...] = (
    "disk_info",
    "list",
    "read_file",
    "trash_list",
    "download",
    "mkdir",
    "write_file",
    "upload",
    "copy",
    "move",
    "trash_restore",
    "publish",
    "delete",
    "trash_empty",
)

ACTION_GROUPS: dict[str, frozenset[str]] = {
    "all": frozenset(ACTIONS),
    "read": frozenset({"disk_info", "list", "read_file", "trash_list"}),
    "download": frozenset({"download"}),
    "write": frozenset({"mkdir", "write_file", "upload", "copy", "move", "trash_restore"}),
    "share": frozenset({"publish"}),
    "delete": frozenset({"delete", "trash_empty"}),
}


class ConfigError(RuntimeError):
    """Required configuration is missing or unusable."""


def token() -> str:
    return get_provider_env(TOKEN_ENV) or get_provider_env(TOKEN_ENV_ALIAS)


def credentials_present() -> bool:
    """Cheap availability check — no network. Used as the tools' ``check_fn``."""
    return bool(token())


def root() -> str:
    return normalize_root(get_provider_env(ROOT_ENV))


def _positive_int(name: str, default: int) -> int:
    raw = get_provider_env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def max_read_bytes() -> int:
    return _positive_int(MAX_READ_BYTES_ENV, DEFAULT_MAX_READ_BYTES)


def max_download_bytes() -> int:
    return _positive_int(MAX_DOWNLOAD_BYTES_ENV, DEFAULT_MAX_DOWNLOAD_BYTES)


def timeout() -> float:
    raw = get_provider_env(TIMEOUT_ENV)
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def allowed_actions() -> frozenset[str]:
    """Which actions this deployment permits.

    Unset means everything, so adding the switch never breaks an existing
    install. Unknown names are dropped rather than expanded, so a typo can only
    ever withhold a tool — never grant one.
    """
    raw = get_provider_env(ACTIONS_ENV)
    if not raw.strip():
        return frozenset(ACTIONS)
    allowed: set[str] = set()
    for item in raw.split(","):
        key = item.strip().lower().replace("-", "_").removeprefix("yadisk_")
        if key in ACTION_GROUPS:
            allowed |= ACTION_GROUPS[key]
        elif key in ACTIONS:
            allowed.add(key)
    return frozenset(allowed)


def build_client() -> YandexDiskClient:
    """Create a client from the environment. Raises :class:`ConfigError` if unset."""
    value = token()
    if not value:
        raise ConfigError(
            f"{TOKEN_ENV} is not set. Create an OAuth token with the "
            "cloud_api:disk.* scopes at https://yandex.ru/dev/disk/poligon/ "
            "and put it in ~/.hermes/.env."
        )
    return YandexDiskClient(
        value,
        base_url=get_provider_env(BASE_URL_ENV) or API_BASE,
        timeout=timeout(),
        max_retries=DEFAULT_MAX_RETRIES,
    )

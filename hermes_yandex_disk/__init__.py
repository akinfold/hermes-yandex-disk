"""Yandex Disk plugin for Hermes Agent.

Gives the agent the user's Yandex Disk: browse it, read files into the
conversation, write and upload new ones, move things around, share public links,
and manage the bin.

Everything goes over the Yandex Disk REST API v1. Relative imports throughout, so
the package loads both as a pip install and as a directory plugin that Hermes
imports under ``hermes_plugins.<slug>``.
"""

from __future__ import annotations

from typing import Any

from . import config, schemas, tools

__version__ = "0.3.0"

__all__ = ["__version__", "register"]

#: action -> (schema, handler, description, emoji). The action name is what
#: ``YANDEX_DISK_ACTIONS`` filters on; see :mod:`.config`.
_TOOLS: tuple[tuple[str, dict[str, Any], Any, str, str], ...] = (
    (
        "disk_info",
        schemas.DISK_INFO,
        tools.handle_disk_info,
        "Yandex Disk quota and account.",
        "💽",
    ),
    ("list", schemas.LIST, tools.handle_list, "List a Yandex Disk folder or file.", "📂"),
    ("search", schemas.SEARCH, tools.handle_search, "Search Yandex Disk by file name.", "🔎"),
    (
        "read_file",
        schemas.READ_FILE,
        tools.handle_read_file,
        "Read a text file from Yandex Disk.",
        "📄",
    ),
    (
        "trash_list",
        schemas.TRASH_LIST,
        tools.handle_trash_list,
        "List the Yandex Disk bin.",
        "🗑️",
    ),
    (
        "download",
        schemas.DOWNLOAD,
        tools.handle_download,
        "Download a Yandex Disk file to this machine.",
        "⬇️",
    ),
    ("mkdir", schemas.MKDIR, tools.handle_mkdir, "Create a folder on Yandex Disk.", "📁"),
    (
        "write_file",
        schemas.WRITE_FILE,
        tools.handle_write_file,
        "Write a text file to Yandex Disk.",
        "📝",
    ),
    ("upload", schemas.UPLOAD, tools.handle_upload, "Upload a file to Yandex Disk.", "⬆️"),
    ("copy", schemas.COPY, tools.handle_copy, "Copy a file or folder on Yandex Disk.", "📑"),
    ("move", schemas.MOVE, tools.handle_move, "Move or rename on Yandex Disk.", "🔀"),
    (
        "trash_restore",
        schemas.TRASH_RESTORE,
        tools.handle_trash_restore,
        "Restore an item from the Yandex Disk bin.",
        "♻️",
    ),
    (
        "publish",
        schemas.PUBLISH,
        tools.handle_publish,
        "Publish or unpublish a Yandex Disk share link.",
        "🔗",
    ),
    (
        "delete",
        schemas.DELETE,
        tools.handle_delete,
        "Delete a file or folder on Yandex Disk.",
        "❌",
    ),
    (
        "trash_empty",
        schemas.TRASH_EMPTY,
        tools.handle_trash_empty,
        "Permanently destroy items in the Yandex Disk bin.",
        "🔥",
    ),
)

_REQUIRES_ENV = [config.TOKEN_ENV]


def _action_available(action: str) -> bool:
    """Whether an action clears its per-action capability gate, beyond the allow-list.

    Search is gated on a live probe of the token (see :mod:`.capabilities`): a
    token that cannot use the search endpoint never has the tool registered, so
    the agent only discovers search when the token actually supports it. Every
    other action is unconditional.
    """
    if action == "search":
        return config.search_supported()
    return True


def register(ctx: Any) -> None:
    """Called by Hermes at load time with a PluginContext.

    Only the actions ``YANDEX_DISK_ACTIONS`` permits are registered at all — a
    deployment that sets ``read`` never exposes a tool that can modify the disk,
    so there is nothing to refuse at call time. Filtering happens here, at load,
    so changing the variable needs a Hermes restart. Search carries an extra
    capability gate on top; see :func:`_action_available`.
    """
    allowed = config.allowed_actions()
    for action, schema, handler, description, emoji in _TOOLS:
        if action not in allowed or not _action_available(action):
            continue
        ctx.register_tool(
            name=schema["name"],
            toolset=tools.TOOLSET,
            schema=schema,
            handler=handler,
            check_fn=config.credentials_present,
            requires_env=_REQUIRES_ENV,
            description=description,
            emoji=emoji,
        )

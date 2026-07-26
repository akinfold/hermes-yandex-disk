"""OpenAI-style function schemas for the Yandex Disk tools.

Kept apart from the handlers so the wire contract is readable in one place.

Two rules the function-calling validators care about: no union types anywhere
(``{"type": ["string", "object"]}`` is legal JSON Schema but strict validators
reject it), and every read result advertises the identifier the write tools
consume — here, the ``path``.
"""

from __future__ import annotations

from typing import Any

_PATH = {
    "type": "string",
    "description": (
        "Path on Yandex Disk, e.g. '/Documents/report.pdf'. Leading slash optional. "
        "Paths returned by other tools ('disk:/...') can be passed back verbatim."
    ),
}
_SORT = {
    "type": "string",
    "enum": [
        "name",
        "path",
        "created",
        "modified",
        "size",
        "-name",
        "-created",
        "-modified",
        "-size",
    ],
    "description": "Sort order for the listing. Prefix with '-' to reverse.",
}


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


DISK_INFO = _schema(
    "yadisk_disk_info",
    "Show the Yandex Disk account: total, used and bin space, and the account login. "
    "Use this to answer 'how much space is left on my disk'.",
    {},
    [],
)

LIST = _schema(
    "yadisk_list",
    "List a folder on Yandex Disk, or show the metadata of a single file. "
    "Returns each entry's path, which the other tools take as input. "
    "Call it with no arguments to see the root of the disk.",
    {
        "path": _PATH,
        "limit": {"type": "integer", "description": "Max entries to return (default 50)."},
        "offset": {"type": "integer", "description": "Entries to skip, for paging."},
        "sort": _SORT,
    },
    [],
)

SEARCH = _schema(
    "yadisk_search",
    "Search Yandex Disk for files matching a query, anywhere on the disk. Yandex indexes "
    "both file names and file contents — including text extracted from documents, OCR of "
    "images and scans, and metadata — so this finds a file by a phrase inside it, not only "
    "by its name. Returns files only (not folders), most relevant first, each with its path "
    "and a search_scope saying whether the name or the contents matched. Recently uploaded "
    "files may take a little while to be indexed. Use yadisk_list to browse a known folder.",
    {
        "query": {
            "type": "string",
            "description": (
                "What to look for, in the file name or its contents, e.g. 'quarterly "
                "report', an invoice number, or a phrase from inside a document."
            ),
        },
        "path": {
            "type": "string",
            "description": "Optional folder to confine results to, including its subfolders.",
        },
        "media_type": {
            "type": "string",
            "enum": [
                "audio",
                "backup",
                "book",
                "compressed",
                "data",
                "development",
                "diskimage",
                "document",
                "encoded",
                "executable",
                "flash",
                "font",
                "image",
                "settings",
                "spreadsheet",
                "text",
                "unknown",
                "video",
                "web",
            ],
            "description": "Restrict to one Yandex Disk media type.",
        },
        "limit": {"type": "integer", "description": "Max matches to return (default 20, max 100)."},
    },
    ["query"],
)

READ_FILE = _schema(
    "yadisk_read_file",
    "Read the contents of a text file stored on Yandex Disk and return it as text. "
    "For binary files or large files use yadisk_download instead.",
    {
        "path": _PATH,
        "encoding": {
            "type": "string",
            "description": "Text encoding to decode with (default 'utf-8').",
        },
        "max_bytes": {
            "type": "integer",
            "description": "Refuse files larger than this (default 1048576).",
        },
    },
    ["path"],
)

TRASH_LIST = _schema(
    "yadisk_trash_list",
    "List what is in the Yandex Disk bin. Each entry shows the path to pass to "
    "yadisk_trash_restore and the origin_path it was deleted from.",
    {
        "limit": {"type": "integer", "description": "Max entries to return (default 50)."},
        "offset": {"type": "integer", "description": "Entries to skip, for paging."},
    },
    [],
)

DOWNLOAD = _schema(
    "yadisk_download",
    "Download a file from Yandex Disk and save it to a path on the local machine.",
    {
        "path": _PATH,
        "save_to": {
            "type": "string",
            "description": "Local destination path. A directory here keeps the original file name.",
        },
        "overwrite": {
            "type": "boolean",
            "description": "Replace the local file if it already exists (default false).",
        },
    },
    ["path", "save_to"],
)

MKDIR = _schema(
    "yadisk_mkdir",
    "Create a folder on Yandex Disk. Missing parent folders are created too.",
    {"path": _PATH},
    ["path"],
)

WRITE_FILE = _schema(
    "yadisk_write_file",
    "Create or replace a text file on Yandex Disk with the given contents. "
    "Missing parent folders are created. Overwriting an existing file requires "
    "overwrite=true, so nothing is replaced by accident.",
    {
        "path": _PATH,
        "content": {"type": "string", "description": "The full text to store in the file."},
        "overwrite": {
            "type": "boolean",
            "description": "Replace the file if it already exists (default false).",
        },
        "encoding": {"type": "string", "description": "Text encoding to write (default 'utf-8')."},
    },
    ["path", "content"],
)

UPLOAD = _schema(
    "yadisk_upload",
    "Upload a file to Yandex Disk from the local machine (local_path) or straight from "
    "a public web address (url) — a URL is fetched by Yandex itself, not downloaded here. "
    "Give exactly one of local_path or url.",
    {
        "path": _PATH,
        "local_path": {"type": "string", "description": "Path to a file on the local machine."},
        "url": {"type": "string", "description": "Public http(s) URL for Yandex to fetch."},
        "overwrite": {
            "type": "boolean",
            "description": "Replace the destination if it already exists (default false).",
        },
    },
    ["path"],
)

COPY = _schema(
    "yadisk_copy",
    "Copy a file or folder to another location on Yandex Disk. The copy happens on "
    "Yandex's side — nothing is transferred through this machine.",
    {
        "source": _PATH,
        "destination": {"type": "string", "description": "Destination path on Yandex Disk."},
        "overwrite": {
            "type": "boolean",
            "description": "Replace the destination if it already exists (default false).",
        },
    },
    ["source", "destination"],
)

MOVE = _schema(
    "yadisk_move",
    "Move or rename a file or folder on Yandex Disk.",
    {
        "source": _PATH,
        "destination": {"type": "string", "description": "New path on Yandex Disk."},
        "overwrite": {
            "type": "boolean",
            "description": "Replace the destination if it already exists (default false).",
        },
    },
    ["source", "destination"],
)

TRASH_RESTORE = _schema(
    "yadisk_trash_restore",
    "Restore an item from the Yandex Disk bin back to where it was deleted from. "
    "Use the 'path' reported by yadisk_trash_list, not the original path.",
    {
        "path": {
            "type": "string",
            "description": "The bin path from yadisk_trash_list, e.g. 'trash:/report.txt_1a2b3c'.",
        },
        "name": {"type": "string", "description": "Optional new name to restore under."},
        "overwrite": {
            "type": "boolean",
            "description": "Replace an existing file at the origin path (default false).",
        },
    },
    ["path"],
)

PUBLISH = _schema(
    "yadisk_publish",
    "Publish a file or folder on Yandex Disk and return the public share link, or "
    "revoke an existing link with unpublish=true. Anyone with the link can read a "
    "published resource.",
    {
        "path": _PATH,
        "unpublish": {
            "type": "boolean",
            "description": "Revoke the existing public link instead of creating one.",
        },
    },
    ["path"],
)

DELETE = _schema(
    "yadisk_delete",
    "Delete a file or folder on Yandex Disk. It goes to the bin and can be restored "
    "with yadisk_trash_restore unless permanently=true, which cannot be undone.",
    {
        "path": _PATH,
        "permanently": {
            "type": "boolean",
            "description": "Skip the bin and destroy the data irreversibly (default false).",
        },
    },
    ["path"],
)

TRASH_EMPTY = _schema(
    "yadisk_trash_empty",
    "Permanently destroy items in the Yandex Disk bin. Without a path the whole bin is "
    "emptied. This cannot be undone.",
    {
        "path": {
            "type": "string",
            "description": "A single bin path from yadisk_trash_list. Omit to empty the whole bin.",
        }
    },
    [],
)

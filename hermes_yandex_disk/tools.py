"""Tool handlers.

Every handler returns a JSON string and never raises: :func:`tool_handler` owns
the client lifetime, translates the failure modes into ``{"error": ...}`` and
catches whatever is left, so a bad path or a rate-limited API degrades into a
message the model can act on instead of an exception in the host.

Where ``YANDEX_DISK_ROOT`` is set every path — in and out — is folded through
:mod:`.paths`, so the sandbox holds for arguments the model invents as well as
for identifiers it copies back from an earlier result.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from . import config
from .client import YandexDiskClient, YandexDiskError
from .paths import (
    DISK_SCHEME,
    TRASH_SCHEME,
    PathError,
    display,
    is_inside_root,
    resolve,
    split_scheme,
)

TOOLSET = "yandex_disk"

Handler = Callable[[dict[str, Any], Any], str]

#: Metadata fields worth showing the model; anything else is noise in context.
_RESOURCE_FIELDS = (
    "name",
    "type",
    "size",
    "modified",
    "created",
    "mime_type",
    "media_type",
    "md5",
    "public_url",
    "origin_path",
    "deleted",
)


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _fail(message: str, **extra: Any) -> str:
    return _dumps({"error": message, **extra})


def tool_handler(fn: Callable[[YandexDiskClient, dict[str, Any], str], dict[str, Any]]) -> Handler:
    """Wrap a handler body with the client lifetime and the never-raise contract."""

    def wrapper(args: dict[str, Any] | None = None, **_kwargs: Any) -> str:
        try:
            client = config.build_client()
        except config.ConfigError as exc:
            return _fail(str(exc))
        try:
            with client:
                return _dumps(fn(client, args or {}, config.root()))
        except PathError as exc:
            return _fail(str(exc))
        except YandexDiskError as exc:
            return _dumps(exc.as_dict())
        except OSError as exc:
            return _fail(f"Local file error: {exc}")
        except Exception as exc:
            return _fail(f"Unexpected {type(exc).__name__}: {exc}")

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# -- shaping --------------------------------------------------------------


def _shape(item: dict[str, Any], root: str) -> dict[str, Any]:
    """Trim an API resource to the fields the model needs, hiding the sandbox prefix."""
    out: dict[str, Any] = {"path": display(str(item.get("path", "")), root)}
    for key in _RESOURCE_FIELDS:
        if item.get(key) is not None:
            out[key] = item[key]
    if item.get("origin_path"):
        out["origin_path"] = display(str(item["origin_path"]), root)
    return out


def _children(meta: dict[str, Any], root: str) -> list[dict[str, Any]]:
    embedded = meta.get("_embedded") or {}
    items = embedded.get("items") or []
    return [_shape(item, root) for item in items if isinstance(item, dict)]


def _clamp(value: Any, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, maximum))


def _flag(args: dict[str, Any], key: str) -> bool:
    return bool(args.get(key))


def _text(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    return value.strip() if isinstance(value, str) else ""


# -- read -----------------------------------------------------------------


@tool_handler
def handle_disk_info(client: YandexDiskClient, _args: dict[str, Any], root: str) -> dict[str, Any]:
    info = client.disk_info()
    user = info.get("user") or {}
    return {
        "login": user.get("login"),
        "total_space": info.get("total_space"),
        "used_space": info.get("used_space"),
        "free_space": (info.get("total_space") or 0) - (info.get("used_space") or 0),
        "trash_size": info.get("trash_size"),
        "max_file_size": info.get("max_file_size"),
        "is_paid": info.get("is_paid"),
        "root": f"{DISK_SCHEME}{root}" if root else f"{DISK_SCHEME}/",
    }


@tool_handler
def handle_list(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    path = resolve(args.get("path"), root)
    limit = _clamp(args.get("limit"), 50, 1000)
    meta = client.get_meta(
        path,
        limit=limit,
        offset=_clamp(args.get("offset"), 0, 100000) if args.get("offset") else 0,
        sort=_text(args, "sort") or None,
    )
    result = _shape(meta, root)
    if meta.get("type") == "dir":
        embedded = meta.get("_embedded") or {}
        result["items"] = _children(meta, root)
        result["total"] = embedded.get("total")
    return result


@tool_handler
def handle_read_file(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    path = resolve(args.get("path"), root)
    cap = _clamp(args.get("max_bytes"), config.max_read_bytes(), config.max_read_bytes())
    encoding = _text(args, "encoding") or "utf-8"
    raw = client.download_bytes(path, max_bytes=cap)
    try:
        content = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return {
            "error": (
                f"{display(path, root)} is not decodable as {encoding} text. "
                "Use yadisk_download to fetch it as a file instead."
            )
        }
    return {"path": display(path, root), "size": len(raw), "content": content}


@tool_handler
def handle_trash_list(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    payload = client.trash_list(
        limit=_clamp(args.get("limit"), 50, 1000),
        offset=_clamp(args.get("offset"), 0, 100000) if args.get("offset") else 0,
    )
    items = [
        item
        for item in _children(payload, "")
        if is_inside_root(str(item.get("origin_path") or ""), root)
    ]
    return {"items": [_shape(i, root) for i in items], "total": len(items)}


# -- download -------------------------------------------------------------


def _destination(save_to: str, path: str, overwrite: bool) -> str:
    target = os.path.expanduser(save_to)
    if os.path.isdir(target):
        target = os.path.join(target, split_scheme(path)[1].rsplit("/", 1)[-1])
    if os.path.exists(target) and not overwrite:
        raise FileExistsError(f"{target} already exists; pass overwrite=true to replace it.")
    parent = os.path.dirname(os.path.abspath(target))
    os.makedirs(parent, exist_ok=True)
    return target


@tool_handler
def handle_download(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    path = resolve(args.get("path"), root)
    save_to = _text(args, "save_to")
    if not save_to:
        return {"error": "save_to is required: the local path to write the file to."}
    target = _destination(save_to, path, _flag(args, "overwrite"))
    written = client.download_to_file(path, target, max_bytes=config.max_download_bytes())
    return {"path": display(path, root), "saved_to": target, "bytes": written}


# -- write ----------------------------------------------------------------


def _ensure_dir(client: YandexDiskClient, full_path: str) -> list[str]:
    """Create ``full_path`` and any missing parents. Existing folders are left alone."""
    scheme, rest = split_scheme(full_path)
    segments = [s for s in rest.split("/") if s]
    created: list[str] = []
    for depth in range(len(segments)):
        candidate = f"{scheme}/" + "/".join(segments[: depth + 1])
        try:
            client.mkdir(candidate)
        except YandexDiskError as exc:
            if exc.status == 409:  # already exists
                continue
            raise
        created.append(candidate)
    return created


def _parent_of(path: str) -> str:
    scheme, rest = split_scheme(path)
    head = rest.rsplit("/", 1)[0]
    return f"{scheme}{head or '/'}"


@tool_handler
def handle_mkdir(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    path = resolve(args.get("path"), root)
    created = _ensure_dir(client, path)
    return {
        "path": display(path, root),
        "created": [display(p, root) for p in created],
        "status": "created" if created else "already existed",
    }


@tool_handler
def handle_write_file(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    path = resolve(args.get("path"), root)
    content = args.get("content")
    if not isinstance(content, str):
        return {"error": "content is required and must be a string."}
    encoding = _text(args, "encoding") or "utf-8"
    _ensure_dir(client, _parent_of(path))
    data = content.encode(encoding)
    client.upload(path, data, overwrite=_flag(args, "overwrite"))
    return {"path": display(path, root), "bytes": len(data), "status": "written"}


def _upload_local(client: YandexDiskClient, path: str, local: str, overwrite: bool) -> int:
    source = os.path.expanduser(local)
    size = os.path.getsize(source)
    with open(source, "rb") as handle:
        client.upload(path, handle, overwrite=overwrite)
    return size


@tool_handler
def handle_upload(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    path = resolve(args.get("path"), root)
    local = _text(args, "local_path")
    url = _text(args, "url")
    if bool(local) == bool(url):
        return {"error": "Give exactly one of local_path or url."}
    _ensure_dir(client, _parent_of(path))
    if url:
        client.upload_from_url(path, url)
        return {"path": display(path, root), "source": url, "status": "uploaded"}
    size = _upload_local(client, path, local, _flag(args, "overwrite"))
    return {"path": display(path, root), "source": local, "bytes": size, "status": "uploaded"}


def _transfer(
    client: YandexDiskClient, args: dict[str, Any], root: str, verb: str
) -> dict[str, Any]:
    source = resolve(args.get("source"), root)
    destination = resolve(args.get("destination"), root)
    getattr(client, verb)(source, destination, overwrite=_flag(args, "overwrite"))
    return {
        "source": display(source, root),
        "destination": display(destination, root),
        "status": f"{verb}d",
    }


@tool_handler
def handle_copy(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    return _transfer(client, args, root, "copy")


@tool_handler
def handle_move(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    return _transfer(client, args, root, "move")


# -- bin ------------------------------------------------------------------


def _trash_target(client: YandexDiskClient, args: dict[str, Any], root: str) -> str:
    """Resolve a bin path and refuse it if its origin lies outside the sandbox."""
    path = resolve(args.get("path"), scheme=TRASH_SCHEME)
    if not root:
        return path
    meta = client.get_trash_meta(path)
    origin = str(meta.get("origin_path") or "")
    if not is_inside_root(origin, root):
        raise PathError(
            f"{path} was deleted from {origin}, which is outside the configured root "
            f"{DISK_SCHEME}{root}."
        )
    return path


@tool_handler
def handle_trash_restore(
    client: YandexDiskClient, args: dict[str, Any], root: str
) -> dict[str, Any]:
    path = _trash_target(client, args, root)
    client.trash_restore(path, name=_text(args, "name") or None, overwrite=_flag(args, "overwrite"))
    return {"path": path, "status": "restored"}


# -- sharing --------------------------------------------------------------


@tool_handler
def handle_publish(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    path = resolve(args.get("path"), root)
    if _flag(args, "unpublish"):
        client.unpublish(path)
        return {"path": display(path, root), "status": "unpublished", "public_url": None}
    meta = client.publish(path)
    return {
        "path": display(path, root),
        "status": "published",
        "public_url": meta.get("public_url"),
        "public_key": meta.get("public_key"),
    }


# -- delete ---------------------------------------------------------------


@tool_handler
def handle_delete(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    path = resolve(args.get("path"), root)
    permanently = _flag(args, "permanently")
    client.delete(path, permanently=permanently)
    return {
        "path": display(path, root),
        "status": "deleted permanently" if permanently else "moved to the bin",
        "recoverable": not permanently,
    }


@tool_handler
def handle_trash_empty(client: YandexDiskClient, args: dict[str, Any], root: str) -> dict[str, Any]:
    if not _text(args, "path"):
        if root:
            return {
                "error": (
                    f"YANDEX_DISK_ROOT is set to {DISK_SCHEME}{root}, so the whole bin cannot "
                    "be emptied from here — it holds items deleted from outside that folder. "
                    "Pass a single path from yadisk_trash_list instead."
                )
            }
        client.trash_delete()
        return {"status": "the bin was emptied", "recoverable": False}
    path = _trash_target(client, args, root)
    client.trash_delete(path)
    return {"path": path, "status": "destroyed permanently", "recoverable": False}


__all__ = [
    "TOOLSET",
    "handle_copy",
    "handle_delete",
    "handle_disk_info",
    "handle_download",
    "handle_list",
    "handle_mkdir",
    "handle_move",
    "handle_publish",
    "handle_read_file",
    "handle_trash_empty",
    "handle_trash_list",
    "handle_trash_restore",
    "handle_upload",
    "handle_write_file",
]

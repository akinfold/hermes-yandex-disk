"""Yandex Disk path normalisation and root confinement.

No Hermes imports — this module is pure logic and unit-testable on its own.

Yandex Disk addresses resources with a scheme: ``disk:/a/b`` for the user's
files, ``trash:/x`` for the bin. Users (and the model) say ``/a/b`` or ``a/b``.
Everything entering the client goes through :func:`resolve` first, so a single
function decides what a path means — including whether it escapes the optional
``YANDEX_DISK_ROOT`` sandbox.
"""

from __future__ import annotations

DISK_SCHEME = "disk:"
TRASH_SCHEME = "trash:"
APP_SCHEME = "app:"
_SCHEMES = (DISK_SCHEME, TRASH_SCHEME, APP_SCHEME)


class PathError(ValueError):
    """A path is malformed or escapes the configured root."""


def split_scheme(path: str) -> tuple[str, str]:
    """Split ``disk:/a/b`` into ``("disk:", "/a/b")``. Missing scheme -> ``("", ...)``."""
    stripped = path.strip()
    for scheme in _SCHEMES:
        if stripped.lower().startswith(scheme):
            return scheme, stripped[len(scheme) :]
    return "", stripped


def _normalize_segments(raw: str) -> str:
    """Collapse ``.``/``..``/duplicate separators. Returns a path with a leading ``/``."""
    out: list[str] = []
    for segment in raw.replace("\\", "/").split("/"):
        if not segment or segment == ".":
            continue
        if segment == "..":
            if not out:
                raise PathError("Path escapes the root of the disk (too many '..' segments).")
            out.pop()
            continue
        out.append(segment)
    return "/" + "/".join(out)


def normalize_root(root: str) -> str:
    """Normalise ``YANDEX_DISK_ROOT``. Empty/``/`` means the whole disk (``""``)."""
    if not root or not root.strip():
        return ""
    _, rest = split_scheme(root)
    normalized = _normalize_segments(rest)
    return "" if normalized == "/" else normalized


def _is_inside(candidate: str, root: str) -> bool:
    return candidate == root or candidate.startswith(root + "/")


def resolve(path: str | None, root: str = "", *, scheme: str = DISK_SCHEME) -> str:
    """Turn a user-supplied path into a full ``<scheme>/...`` address.

    Without a scheme the path is taken as relative to ``root``; with one it is
    absolute and must already sit inside ``root``. Either way the result is
    verified to stay inside ``root``, so ``..`` cannot climb out of the sandbox.
    """
    given_scheme, rest = split_scheme(path or "/")
    if given_scheme and given_scheme != scheme:
        raise PathError(f"Expected a {scheme}/ path, got {given_scheme}/.")

    # With a scheme the path is already absolute; without one it hangs off the root.
    absolute = _normalize_segments(rest if given_scheme else f"{root}/{rest}")

    if root and not _is_inside(absolute, root):
        raise PathError(f"Path {scheme}{absolute} is outside the configured root {scheme}{root}.")
    return f"{scheme}{absolute}"


def is_inside_root(path: str, root: str) -> bool:
    """True when an API-returned path lies inside ``root`` (empty root: always)."""
    if not root:
        return True
    _, rest = split_scheme(path or "")
    try:
        return _is_inside(_normalize_segments(rest), root)
    except PathError:
        return False


def display(path: str, root: str) -> str:
    """Present an API path to the model, hiding the sandbox prefix when one is set."""
    if not root:
        return path
    scheme, rest = split_scheme(path or "")
    try:
        absolute = _normalize_segments(rest)
    except PathError:
        return path
    if not _is_inside(absolute, root):
        return path
    relative = absolute[len(root) :] or "/"
    return f"{scheme}{relative}"

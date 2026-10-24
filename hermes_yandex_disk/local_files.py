"""Local paths: the files on this machine that two tools read or write.

``yadisk_upload`` reads a local file and sends it to Yandex, and ``yadisk_download``
writes one. Hermes' own file tools will not read its credential stores or any
``.env`` file, and will not write credential files, the SSH client configuration
without approval, or outside ``HERMES_WRITE_SAFE_ROOT``. Both tools put the same
questions to Hermes before they touch a local path, so neither is a way around
those rules.

Hermes does not document these checks for plugins (they live in
``agent.file_safety``), so the import is guarded with ``ImportError`` only, as in
:mod:`._compat`. Without them — outside Hermes, or on a Hermes that has moved
them — a stand-in still refuses ``.env`` files in both directions. Like Hermes'
own checks, these are defence in depth, not a sandbox: anything else the agent
runs as the same user can read and write the same files.
"""

from __future__ import annotations

import os

from .paths import PathError

#: Secret-bearing environment files: the names Hermes refuses to read anywhere on
#: disk, refused here in both directions when Hermes' own checks are missing.
_ENV_FILE_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.test",
        ".env.staging",
        ".envrc",
    }
)

try:
    from agent.file_safety import (
        get_read_block_error,
        get_write_denied_error,
        is_write_approval_required,
    )

    HERMES_GUARDS = True
except ImportError:
    HERMES_GUARDS = False

    def _env_file_error(path: str, verb: str) -> str | None:
        if os.path.basename(os.path.realpath(path)).lower() in _ENV_FILE_NAMES:
            return f"{verb} denied: {path} is a secret-bearing environment file."
        return None

    def get_read_block_error(path: str) -> str | None:  # type: ignore[misc]
        return _env_file_error(path, "Read")

    def get_write_denied_error(path: str, *, verb: str = "Write") -> str | None:  # type: ignore[misc]
        return _env_file_error(path, verb)

    def is_write_approval_required(path: str) -> bool:  # type: ignore[misc]
        return False


class LocalPathError(PathError):
    """A local path that Hermes' file rules keep the tools away from."""


def _absolute(path: str) -> str:
    # Relative paths mean the process directory, as they do to open(). The check
    # gets the absolute form so that it and the open() after it name one file.
    return os.path.abspath(os.path.expanduser(path))


def readable(path: str) -> str:
    """*path* made absolute, or :class:`LocalPathError` if Hermes would not read it."""
    absolute = _absolute(path)
    refusal = get_read_block_error(absolute)
    if refusal:
        raise LocalPathError(refusal)
    return absolute


def writable(path: str) -> str:
    """*path* made absolute, or :class:`LocalPathError` if Hermes would not write it."""
    absolute = _absolute(path)
    refusal = get_write_denied_error(absolute)
    if refusal:
        raise LocalPathError(refusal)
    if is_write_approval_required(absolute):
        raise LocalPathError(
            f"Write denied: {path} needs a person's approval in Hermes, which this tool "
            "cannot ask for. Save the file somewhere else and move it there yourself."
        )
    return absolute


__all__ = ["HERMES_GUARDS", "LocalPathError", "readable", "writable"]

"""Local paths: the files on this machine that two tools read or write.

``yadisk_upload`` reads a local file and sends it to Yandex, and ``yadisk_download``
writes one. Two sets of rules decide which local paths they may touch, and a path
either set refuses is refused.

Hermes' shared file rules (``agent.file_safety``) come first. Its file tools will
not read its credential stores or any ``.env`` file, and will not write credential
files, the SSH client configuration without approval, or outside
``HERMES_WRITE_SAFE_ROOT``; both tools put the same questions to Hermes. Hermes
does not document these checks for plugins, so the import is guarded with
``ImportError`` only, as in :mod:`._compat`. Without them — outside Hermes, or on a
Hermes that has moved them — a stand-in still refuses ``.env`` files both ways.

The plugin's own rules follow, because Hermes' file tools check more than the
shared rules do, and because sending a file off the machine is not the same as
reading it into the conversation. Nothing is uploaded from Hermes' own directory
or from the credential directories in the home directory, and nothing is
downloaded into Hermes' own directory, any project ``.hermes`` directory, over a
file that steers future agent sessions, or into system paths. The ``home`` and
``workspace`` folders in Hermes' directory, which the official Docker image gives
the agent, are the agent's and not Hermes' state.

Like Hermes' checks, these are defence in depth, not a sandbox: anything else the
agent runs as the same user can read and write the same files.
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

#: Directories under the home directory that hold keys and logins: the ones Hermes
#: will not write into, and here not read from for an upload either.
_HOME_CREDENTIAL_DIRS = tuple(
    os.path.join(*parts)
    for parts in (
        (".ssh",),
        (".aws",),
        (".gnupg",),
        (".kube",),
        (".docker",),
        (".azure",),
        (".config", "gh"),
        (".config", "gcloud"),
    )
)

#: Files an agent reads as instructions for its next sessions, matched by name in
#: any directory and case-insensitively, as Hermes matches them. Hermes' file tools
#: write them only with a person's approval, which a tool cannot ask for. The names
#: are assembled because the install scanner reads this file too.
_INSTRUCTION_FILE_NAMES = frozenset({stem + ".md" for stem in ("agents", "claude", "soul")}) | {
    ".cursorrules"
}

#: Folders in Hermes' own directory that belong to the agent rather than to Hermes:
#: the official Docker image keeps the agent's home and workspace there.
_AGENT_AREAS = ("home", "workspace")

#: System locations Hermes' file tools refuse to write, matched after realpath.
_SYSTEM_PREFIXES = (
    "/etc/",
    "/boot/",
    "/usr/lib/systemd/",
    "/private/etc/",
    "/private/var/db/",
    "/private/var/root/",
)
_SYSTEM_FILES = frozenset(os.path.join(run, "docker.sock") for run in ("/var/run", "/run"))

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
    """A local path that Hermes' file rules or the plugin's own keep the tools away from."""


def _absolute(path: str) -> str:
    # Relative paths mean the process directory, as they do to open(). The check
    # gets the absolute form so that it and the open() after it name one file.
    return os.path.abspath(os.path.expanduser(path))


def _forms(absolute: str) -> tuple[str, ...]:
    """The path as given and with every symlink resolved: a rule matches either."""
    return tuple(dict.fromkeys((os.path.normpath(absolute), os.path.realpath(absolute))))


def _inside(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _hermes_dirs() -> tuple[str, ...]:
    """Hermes' own directory: the active profile's home and the root above any profile."""
    try:
        from hermes_constants import get_default_hermes_root, get_hermes_home

        homes = [str(get_hermes_home()), str(get_default_hermes_root())]
    except ImportError:
        homes = [os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes")]
    resolved = []
    for home in homes:
        expanded = os.path.abspath(os.path.expanduser(home))
        resolved += [expanded, os.path.realpath(expanded)]
    return tuple(dict.fromkeys(resolved))


def _below_hermes(form: str) -> tuple[str, list[str]] | None:
    """The innermost Hermes directory *form* is in, and its parts below it; or None."""
    bases = [base for base in _hermes_dirs() if _inside(form, base)]
    if not bases:
        return None
    base = max(bases, key=len)
    return base, [part for part in form[len(base) :].split(os.sep) if part]


def _hermes_state(forms: tuple[str, ...]) -> str | None:
    """The Hermes directory a path is in, unless it is in an area that is the agent's."""
    for form in forms:
        below = _below_hermes(form)
        if below and (not below[1] or below[1][0] not in _AGENT_AREAS):
            return below[0]
    return None


def _own_read_refusal(path: str, forms: tuple[str, ...]) -> str | None:
    hermes = _hermes_state(forms)
    if hermes:
        return (
            f"Upload denied: {path} is in Hermes' own directory ({hermes}), where its "
            "configuration, sessions and memories live; nothing there is sent to Yandex."
        )
    # The official Docker image runs the agent's tools with HOME in Hermes' own
    # directory, so the key directories there count as well as the process's own.
    homes = {os.path.realpath(os.path.expanduser("~"))}
    homes |= {os.path.join(base, "home") for base in _hermes_dirs()}
    for home, credentials in ((h, c) for h in sorted(homes) for c in _HOME_CREDENTIAL_DIRS):
        if any(_inside(form, os.path.join(home, credentials)) for form in forms):
            return (
                f"Upload denied: {path} is in a directory that holds keys or logins "
                f"(~/{credentials}); nothing there is sent to Yandex."
            )
    return None


def _own_write_refusal(path: str, forms: tuple[str, ...]) -> str | None:
    hermes = _hermes_state(forms)
    if hermes:
        return (
            f"Write denied: {path} is in Hermes' own directory ({hermes}), where its "
            "configuration, profiles, plugins and skills live; a file from Yandex Disk "
            "is not written there."
        )
    for form in forms:
        parts = form.split(os.sep)
        # Below Hermes' own directory only what lies under it counts: its name may
        # itself be .hermes, and the agent's areas there are allowed.
        below = _below_hermes(form)
        if any(part.lower() == ".hermes" for part in (below[1] if below else parts)[:-1]):
            return f"Write denied: {path} is inside a .hermes directory, which steers Hermes."
        if parts[-1].lower() in _INSTRUCTION_FILE_NAMES:
            return (
                f"Write denied: {path} is a file agents read as instructions, which Hermes' "
                "file tools write only with a person's approval; this tool cannot ask for it."
            )
        if form.startswith(_SYSTEM_PREFIXES) or form in _SYSTEM_FILES:
            return f"Write denied: {path} is a system location."
    return None


def readable(path: str) -> str:
    """*path* made absolute, or :class:`LocalPathError` if it may not be uploaded."""
    absolute = _absolute(path)
    refusal = get_read_block_error(absolute) or _own_read_refusal(path, _forms(absolute))
    if refusal:
        raise LocalPathError(refusal)
    return absolute


def writable(path: str) -> str:
    """*path* made absolute, or :class:`LocalPathError` if it may not be written."""
    absolute = _absolute(path)
    refusal = get_write_denied_error(absolute) or _own_write_refusal(path, _forms(absolute))
    if refusal:
        raise LocalPathError(refusal)
    if is_write_approval_required(absolute):
        raise LocalPathError(
            f"Write denied: {path} needs a person's approval in Hermes, which this tool "
            "cannot ask for. Save the file somewhere else and move it there yourself."
        )
    return absolute


__all__ = ["HERMES_GUARDS", "LocalPathError", "readable", "writable"]

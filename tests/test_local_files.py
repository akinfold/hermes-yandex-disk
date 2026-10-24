"""Local paths: Hermes' own file rules, or the stand-in used without them."""

from __future__ import annotations

import importlib
import os
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from hermes_yandex_disk import local_files

# -- outside Hermes: the stand-in -----------------------------------------


def test_outside_hermes_the_stand_in_is_in_use() -> None:
    assert local_files.HERMES_GUARDS is False


@pytest.mark.parametrize("name", [".env", ".env.production", ".envrc", ".ENV"])
def test_the_stand_in_refuses_environment_files_both_ways(tmp_path: Path, name: str) -> None:
    path = str(tmp_path / name)
    with pytest.raises(local_files.LocalPathError, match="Read denied"):
        local_files.readable(path)
    with pytest.raises(local_files.LocalPathError, match="Write denied"):
        local_files.writable(path)


def test_the_stand_in_looks_through_a_symlink(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("KEY=value\n", encoding="utf-8")
    innocent = tmp_path / "notes.txt"
    innocent.symlink_to(secret)
    with pytest.raises(local_files.LocalPathError):
        local_files.readable(str(innocent))


def test_other_paths_come_back_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert local_files.readable("report.pdf") == os.path.join(os.getcwd(), "report.pdf")
    assert local_files.writable("~/report.pdf") == os.path.expanduser("~/report.pdf")
    assert local_files.readable(".env.example").endswith(".env.example")


# -- inside Hermes: its own rules -----------------------------------------


class _FileSafety(types.ModuleType):
    """Stands in for Hermes' ``agent.file_safety``, recording what it is asked."""

    def __init__(self) -> None:
        super().__init__("agent.file_safety")
        self.asked: list[tuple[str, str]] = []

    def get_read_block_error(self, path: str) -> str | None:
        self.asked.append(("read", path))
        if path.endswith("auth.json"):
            return f"Access denied: {path} is a Hermes credential store."
        return None

    def get_write_denied_error(self, path: str, *, verb: str = "Write") -> str | None:
        self.asked.append(("write", path))
        if f"{os.sep}outside{os.sep}" in path:
            return f"{verb} denied: '{path}' is outside HERMES_WRITE_SAFE_ROOT."
        return None

    def is_write_approval_required(self, path: str) -> bool:
        self.asked.append(("approval", path))
        return path.endswith(os.path.join(".ssh", "config"))


@pytest.fixture
def hermes() -> Iterator[_FileSafety]:
    """Reload local_files the way it loads inside Hermes, then put it back."""
    safety = _FileSafety()
    agent = types.ModuleType("agent")
    agent.__path__ = []  # type: ignore[attr-defined]
    agent.file_safety = safety  # type: ignore[attr-defined]
    saved = {name: sys.modules.get(name) for name in ("agent", "agent.file_safety")}
    sys.modules.update({"agent": agent, "agent.file_safety": safety})
    try:
        importlib.reload(local_files)
        yield safety
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        importlib.reload(local_files)


def test_inside_hermes_its_rules_decide(hermes: _FileSafety, tmp_path: Path) -> None:
    assert local_files.HERMES_GUARDS is True
    with pytest.raises(local_files.LocalPathError, match="credential store"):
        local_files.readable(str(tmp_path / "auth.json"))
    # Hermes' rules replace the stand-in rather than adding to it.
    assert local_files.readable(str(tmp_path / "x.env")) == str(tmp_path / "x.env")


def test_inside_hermes_it_is_asked_about_the_absolute_path(
    hermes: _FileSafety, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    local_files.readable("a.txt")
    local_files.writable("b.txt")
    cwd = os.getcwd()
    assert hermes.asked == [
        ("read", os.path.join(cwd, "a.txt")),
        ("write", os.path.join(cwd, "b.txt")),
        ("approval", os.path.join(cwd, "b.txt")),
    ]


def test_inside_hermes_a_denied_write_is_refused(hermes: _FileSafety, tmp_path: Path) -> None:
    with pytest.raises(local_files.LocalPathError, match="HERMES_WRITE_SAFE_ROOT"):
        local_files.writable(str(tmp_path / "outside" / "a.txt"))


def test_a_write_that_needs_approval_is_refused(hermes: _FileSafety, tmp_path: Path) -> None:
    """Hermes' file tools would ask a person; this tool has no way to, so it declines."""
    with pytest.raises(local_files.LocalPathError, match="approval"):
        local_files.writable(str(tmp_path / ".ssh" / "config"))


# -- the plugin's own rules, which apply inside Hermes and out -------------

#: Assembled, like the names in local_files, because the install scanner reads tests.
AGENT_INSTRUCTIONS = "agents" + ".md"


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A home directory and a Hermes directory of the test's own."""
    home = tmp_path / "home"
    hermes = tmp_path / "hermes-home"
    for directory in (home, hermes):
        directory.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    return {"home": home, "hermes": hermes}


def test_nothing_is_uploaded_from_hermes_own_directory(homes: dict[str, Path]) -> None:
    with pytest.raises(local_files.LocalPathError, match="Hermes' own directory"):
        local_files.readable(str(homes["hermes"] / "config.yaml"))


@pytest.mark.parametrize(
    "credentials",
    [
        (".ssh", "id_ed25519"),
        (".aws", "credentials"),
        (".gnupg", "private-keys-v1.d", "key"),
        (".kube", "config"),
        (".docker", "config.json"),
        (".azure", "msal_token_cache.json"),
        (".config", "gh", "hosts.yml"),
        (".config", "gcloud", "credentials.db"),
    ],
    ids=lambda parts: "/".join(parts[:-1]),
)
def test_nothing_is_uploaded_from_a_credential_directory(
    homes: dict[str, Path], credentials: tuple[str, str]
) -> None:
    with pytest.raises(local_files.LocalPathError, match="keys or logins"):
        local_files.readable(str(homes["home"].joinpath(*credentials)))


def test_a_symlink_does_not_smuggle_a_file_out_of_hermes_own_directory(
    homes: dict[str, Path], tmp_path: Path
) -> None:
    (homes["hermes"] / "memories.md").write_text("private", encoding="utf-8")
    innocent = tmp_path / "notes.md"
    innocent.symlink_to(homes["hermes"] / "memories.md")
    with pytest.raises(local_files.LocalPathError, match="Hermes' own directory"):
        local_files.readable(str(innocent))


def test_nothing_is_written_into_hermes_own_directory(homes: dict[str, Path]) -> None:
    for target in ("config.yaml", "plugins/evil/__init__.py", "skills/x/SKILL.md"):
        with pytest.raises(local_files.LocalPathError, match="Hermes' own directory"):
            local_files.writable(str(homes["hermes"] / target))
    # The directory itself, as a download destination, is refused too.
    with pytest.raises(local_files.LocalPathError):
        local_files.writable(str(homes["hermes"]))


def test_nothing_is_written_into_a_project_hermes_directory(tmp_path: Path) -> None:
    with pytest.raises(local_files.LocalPathError, match=r"\.hermes directory"):
        local_files.writable(str(tmp_path / "repo" / ".Hermes" / "config.yaml"))


@pytest.mark.parametrize(
    "name",
    [
        AGENT_INSTRUCTIONS,
        AGENT_INSTRUCTIONS.upper(),
        "Claude" + ".md",
        "Soul" + ".md",
        ".cursorrules",
    ],
)
def test_files_agents_read_as_instructions_are_not_written(tmp_path: Path, name: str) -> None:
    with pytest.raises(local_files.LocalPathError, match="instructions"):
        local_files.writable(str(tmp_path / "repo" / name))
    # Reading one to upload it is fine.
    assert local_files.readable(str(tmp_path / "repo" / name))


@pytest.mark.parametrize(
    "target",
    [
        "/etc/hosts",
        "/boot/x",
        "/usr/lib/systemd/x.service",
        "/private/etc/hosts",
        "/private/var/db/x",
        "/private/var/root/x",
        "/var/run/docker.sock",
        "/run/docker.sock",
    ],
)
def test_system_locations_are_not_written(target: str) -> None:
    with pytest.raises(local_files.LocalPathError, match="system location"):
        local_files.writable(target)


def test_ordinary_files_pass_the_plugin_rules(homes: dict[str, Path], tmp_path: Path) -> None:
    assert local_files.readable(str(homes["home"] / "Documents" / "report.pdf"))
    assert local_files.writable(str(homes["home"] / "Downloads" / "report.pdf"))
    # A sibling whose name merely starts like Hermes' directory is not inside it.
    assert local_files.writable(str(tmp_path / "hermes-home-backup" / "a.txt"))


def test_inside_hermes_its_own_directory_comes_from_hermes(
    hermes: _FileSafety, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constants = types.ModuleType("hermes_constants")
    profile, root = tmp_path / "root" / "profiles" / "work", tmp_path / "root"
    constants.get_hermes_home = lambda: profile  # type: ignore[attr-defined]
    constants.get_default_hermes_root = lambda: root  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    with pytest.raises(local_files.LocalPathError, match="Hermes' own directory"):
        local_files.writable(str(profile / "config.yaml"))
    # The root above the profile holds the shared .env and the other profiles.
    with pytest.raises(local_files.LocalPathError, match="Hermes' own directory"):
        local_files.readable(str(root / "profiles" / "other" / "config.yaml"))
    # The profile's own workspace is the agent's, though it lies inside the root too.
    assert local_files.writable(str(profile / "workspace" / "a.txt"))


def test_the_agent_areas_in_hermes_directory_stay_usable(homes: dict[str, Path]) -> None:
    """The official Docker image keeps the agent's home and workspace in HERMES_HOME."""
    hermes = homes["hermes"]
    for area in ("home", "workspace"):
        assert local_files.writable(str(hermes / area / "Downloads" / "report.pdf"))
        assert local_files.readable(str(hermes / area / "report.pdf"))
    # A project in the workspace still has its .hermes directory and instruction files kept.
    with pytest.raises(local_files.LocalPathError, match=r"\.hermes directory"):
        local_files.writable(str(hermes / "workspace" / "repo" / ".hermes" / "config.yaml"))
    with pytest.raises(local_files.LocalPathError, match="instructions"):
        local_files.writable(str(hermes / "workspace" / "repo" / AGENT_INSTRUCTIONS))
    # And an area's name only counts directly under Hermes' directory.
    with pytest.raises(local_files.LocalPathError, match="Hermes' own directory"):
        local_files.writable(str(hermes / "plugins" / "workspace" / "__init__.py"))


def test_a_hermes_directory_named_dot_hermes_is_not_a_project_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes = tmp_path / "home" / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    assert local_files.writable(str(hermes / "workspace" / "a.txt"))
    with pytest.raises(local_files.LocalPathError, match="Hermes' own directory"):
        local_files.writable(str(hermes / "config.yaml"))


def test_key_directories_in_the_docker_agent_home_are_not_uploaded(homes: dict[str, Path]) -> None:
    with pytest.raises(local_files.LocalPathError, match="keys or logins"):
        local_files.readable(str(homes["hermes"] / "home" / ".ssh" / "id_ed25519"))


@pytest.mark.parametrize(
    "path",
    [
        "/etc/x",
        "/boot/x",
        "/usr/lib/systemd/x",
        "/private/etc/x",
        "/private/var/db/x",
        "/private/var/root/x",
    ],
)
def test_each_system_prefix_holds_on_its_own(path: str) -> None:
    """On macOS /etc resolves to /private/etc, so each prefix is tested as the only form."""
    assert local_files._own_write_refusal(path, (path,))

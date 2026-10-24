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

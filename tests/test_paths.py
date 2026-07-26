"""Path normalisation and the YANDEX_DISK_ROOT sandbox."""

from __future__ import annotations

import pytest

from hermes_yandex_disk.paths import (
    PathError,
    display,
    is_inside_root,
    normalize_root,
    resolve,
    split_scheme,
)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("/a/b", "disk:/a/b"),
        ("a/b", "disk:/a/b"),
        ("disk:/a/b", "disk:/a/b"),
        ("DISK:/a/b", "disk:/a/b"),
        ("  /a//b/  ", "disk:/a/b"),
        ("/a/./b", "disk:/a/b"),
        ("/a/c/../b", "disk:/a/b"),
        ("", "disk:/"),
        (None, "disk:/"),
        ("/", "disk:/"),
    ],
)
def test_resolve_without_root(given: str | None, expected: str) -> None:
    assert resolve(given) == expected


def test_resolve_rejects_a_foreign_scheme() -> None:
    with pytest.raises(PathError, match="Expected a disk:/ path"):
        resolve("trash:/x")


def test_resolve_accepts_the_requested_scheme() -> None:
    assert resolve("trash:/x_1", scheme="trash:") == "trash:/x_1"


def test_climbing_above_the_disk_root_is_refused() -> None:
    with pytest.raises(PathError, match="escapes the root"):
        resolve("/../secrets")


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("notes.md", "disk:/Hermes/notes.md"),
        ("/notes.md", "disk:/Hermes/notes.md"),
        ("disk:/Hermes/notes.md", "disk:/Hermes/notes.md"),
        ("sub/../notes.md", "disk:/Hermes/notes.md"),
    ],
)
def test_relative_paths_hang_off_the_root(given: str, expected: str) -> None:
    assert resolve(given, "/Hermes") == expected


@pytest.mark.parametrize("escape", ["../elsewhere", "disk:/Other/file", "disk:/"])
def test_paths_outside_the_root_are_refused(escape: str) -> None:
    with pytest.raises(PathError):
        resolve(escape, "/Hermes")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("/", ""),
        ("disk:/", ""),
        ("/Hermes/", "/Hermes"),
        ("Hermes", "/Hermes"),
    ],
)
def test_normalize_root(raw: str, expected: str) -> None:
    assert normalize_root(raw) == expected


def test_split_scheme() -> None:
    assert split_scheme("disk:/a") == ("disk:", "/a")
    assert split_scheme("trash:/a") == ("trash:", "/a")
    assert split_scheme("/a") == ("", "/a")


def test_is_inside_root() -> None:
    assert is_inside_root("disk:/Hermes/a", "/Hermes")
    assert is_inside_root("disk:/Hermes", "/Hermes")
    assert not is_inside_root("disk:/HermesOther/a", "/Hermes")
    assert not is_inside_root("", "/Hermes")
    assert is_inside_root("anything", "")


def test_is_inside_root_survives_a_malformed_path() -> None:
    assert not is_inside_root("disk:/../x", "/Hermes")


def test_display_hides_the_sandbox_prefix() -> None:
    assert display("disk:/Hermes/a/b", "/Hermes") == "disk:/a/b"
    assert display("disk:/Hermes", "/Hermes") == "disk:/"
    assert display("disk:/a/b", "") == "disk:/a/b"


def test_display_leaves_foreign_and_malformed_paths_alone() -> None:
    assert display("disk:/Other/a", "/Hermes") == "disk:/Other/a"
    assert display("disk:/../a", "/Hermes") == "disk:/../a"

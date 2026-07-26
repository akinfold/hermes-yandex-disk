"""Tool handlers driven against the in-memory disk."""

from __future__ import annotations

import httpx
import pytest

from hermes_yandex_disk import config, tools

from .conftest import FakeDisk, payload


@pytest.fixture(autouse=True)
def _wire(wired: object) -> None:
    """Every test in this module talks to the fake disk."""


# -- read -----------------------------------------------------------------


def test_disk_info(disk: FakeDisk) -> None:
    result = payload(tools.handle_disk_info({}))
    assert result["login"] == "tester"
    assert result["free_space"] == 60
    assert result["root"] == "disk:/"


def test_list_root_by_default(disk: FakeDisk) -> None:
    disk.add_file("disk:/notes.md", b"x")
    disk.add_dir("disk:/Photos")
    result = payload(tools.handle_list({}))
    assert result["path"] == "disk:/"
    assert {i["path"] for i in result["items"]} == {"disk:/Photos", "disk:/notes.md"}
    assert result["total"] == 2


def test_list_a_single_file_returns_metadata_without_items(disk: FakeDisk) -> None:
    disk.add_file("disk:/notes.md", b"hello")
    result = payload(tools.handle_list({"path": "notes.md"}))
    assert result["type"] == "file"
    assert result["size"] == 5
    assert "items" not in result


def test_list_paging_and_sort_are_passed_through(disk: FakeDisk) -> None:
    for name in "abcde":
        disk.add_file(f"disk:/{name}.txt", b"x")
    result = payload(tools.handle_list({"limit": 2, "offset": 2, "sort": "name"}))
    assert [i["name"] for i in result["items"]] == ["c.txt", "d.txt"]


def test_list_reports_a_missing_folder(disk: FakeDisk) -> None:
    assert "not found" in payload(tools.handle_list({"path": "/nope"}))["error"].lower()


def test_search_matches_a_name_substring(disk: FakeDisk) -> None:
    disk.add_file("disk:/Docs/Invoice-2026.pdf", b"x")
    disk.add_file("disk:/Docs/receipt.txt", b"x")
    result = payload(tools.handle_search({"query": "invoice"}))
    assert [i["path"] for i in result["items"]] == ["disk:/Docs/Invoice-2026.pdf"]
    assert result["truncated"] is False


def test_search_can_be_scoped_to_a_folder(disk: FakeDisk) -> None:
    disk.add_file("disk:/A/report.txt", b"x")
    disk.add_file("disk:/B/report.txt", b"x")
    result = payload(tools.handle_search({"query": "report", "path": "/A"}))
    assert [i["path"] for i in result["items"]] == ["disk:/A/report.txt"]


def test_search_scope_does_not_match_a_sibling_by_prefix(disk: FakeDisk) -> None:
    disk.add_file("disk:/Archive2/report.txt", b"x")
    result = payload(tools.handle_search({"query": "report", "path": "/Archive"}))
    assert result["items"] == []


def test_search_on_an_empty_disk(disk: FakeDisk) -> None:
    result = payload(tools.handle_search({"query": "anything"}))
    assert result == {"query": "anything", "scanned": 0, "truncated": False, "items": []}


def test_search_needs_a_query(disk: FakeDisk) -> None:
    assert "non-empty" in payload(tools.handle_search({"query": "  "}))["error"]


def test_search_stops_at_the_scan_ceiling(monkeypatch: pytest.MonkeyPatch, disk: FakeDisk) -> None:
    monkeypatch.setattr(config, "DEFAULT_SEARCH_SCAN", 2)
    for index in range(5):
        disk.add_file(f"disk:/file{index}.txt", b"x")
    result = payload(tools.handle_search({"query": "nomatch"}))
    assert result["scanned"] == 2
    assert result["truncated"] is True


def test_search_honours_the_limit(disk: FakeDisk) -> None:
    for index in range(5):
        disk.add_file(f"disk:/report{index}.txt", b"x")
    result = payload(tools.handle_search({"query": "report", "limit": 2}))
    assert len(result["items"]) == 2


def test_read_file(disk: FakeDisk) -> None:
    disk.add_file("disk:/notes.md", "привет\n".encode())
    result = payload(tools.handle_read_file({"path": "/notes.md"}))
    assert result["content"] == "привет\n"


def test_read_file_refuses_binary_and_points_at_download(disk: FakeDisk) -> None:
    disk.add_file("disk:/image.png", b"\x89PNG\xff\xfe")
    assert "yadisk_download" in payload(tools.handle_read_file({"path": "/image.png"}))["error"]


def test_read_file_rejects_an_unknown_encoding(disk: FakeDisk) -> None:
    disk.add_file("disk:/notes.md", b"hello")
    result = payload(tools.handle_read_file({"path": "/notes.md", "encoding": "klingon"}))
    assert "not decodable" in result["error"]


def test_read_file_is_capped(monkeypatch: pytest.MonkeyPatch, disk: FakeDisk) -> None:
    monkeypatch.setenv(config.MAX_READ_BYTES_ENV, "4")
    disk.add_file("disk:/big.txt", b"0123456789")
    assert "larger than" in payload(tools.handle_read_file({"path": "/big.txt"}))["error"]


def test_trash_list(disk: FakeDisk) -> None:
    disk.add_file("disk:/gone.txt", b"x")
    tools.handle_delete({"path": "/gone.txt"})
    items = payload(tools.handle_trash_list({}))["items"]
    assert items[0]["origin_path"] == "disk:/gone.txt"
    assert items[0]["path"].startswith("trash:/gone.txt_")


# -- download -------------------------------------------------------------


def test_download_to_a_file(disk: FakeDisk, tmp_path) -> None:
    disk.add_file("disk:/a.txt", b"hello")
    target = tmp_path / "a.txt"
    result = payload(tools.handle_download({"path": "/a.txt", "save_to": str(target)}))
    assert result["bytes"] == 5
    assert target.read_bytes() == b"hello"


def test_download_into_a_directory_keeps_the_name(disk: FakeDisk, tmp_path) -> None:
    disk.add_file("disk:/sub/a.txt", b"hello")
    result = payload(tools.handle_download({"path": "/sub/a.txt", "save_to": str(tmp_path)}))
    assert result["saved_to"].endswith("/a.txt")


def test_download_creates_missing_local_directories(disk: FakeDisk, tmp_path) -> None:
    disk.add_file("disk:/a.txt", b"hello")
    target = tmp_path / "deep" / "nested" / "a.txt"
    tools.handle_download({"path": "/a.txt", "save_to": str(target)})
    assert target.exists()


def test_download_will_not_clobber_a_local_file(disk: FakeDisk, tmp_path) -> None:
    disk.add_file("disk:/a.txt", b"new")
    target = tmp_path / "a.txt"
    target.write_bytes(b"precious")
    assert (
        "overwrite=true"
        in payload(tools.handle_download({"path": "/a.txt", "save_to": str(target)}))["error"]
    )
    assert target.read_bytes() == b"precious"
    tools.handle_download({"path": "/a.txt", "save_to": str(target), "overwrite": True})
    assert target.read_bytes() == b"new"


def test_download_requires_a_destination(disk: FakeDisk) -> None:
    assert "save_to is required" in payload(tools.handle_download({"path": "/a.txt"}))["error"]


# -- write ----------------------------------------------------------------


def test_mkdir_creates_missing_parents(disk: FakeDisk) -> None:
    result = payload(tools.handle_mkdir({"path": "/a/b/c"}))
    assert result["created"] == ["disk:/a", "disk:/a/b", "disk:/a/b/c"]
    assert "disk:/a/b/c" in disk.dirs


def test_mkdir_is_idempotent(disk: FakeDisk) -> None:
    tools.handle_mkdir({"path": "/a"})
    result = payload(tools.handle_mkdir({"path": "/a"}))
    assert result["created"] == []
    assert result["status"] == "already existed"


def test_mkdir_surfaces_a_real_failure(disk: FakeDisk) -> None:
    disk.fail_next = httpx.Response(507, json={"description": "Not enough space"})
    assert payload(tools.handle_mkdir({"path": "/a"}))["error"] == "Not enough space"


def test_write_file_creates_parents_and_stores_the_text(disk: FakeDisk) -> None:
    result = payload(tools.handle_write_file({"path": "/notes/2026/q3.md", "content": "# Q3"}))
    assert result["status"] == "written"
    assert disk.files["disk:/notes/2026/q3.md"] == b"# Q3"


def test_write_file_will_not_overwrite_by_default(disk: FakeDisk) -> None:
    disk.add_file("disk:/notes.md", b"original")
    assert (
        "exists"
        in payload(tools.handle_write_file({"path": "/notes.md", "content": "new"}))["error"]
    )
    assert disk.files["disk:/notes.md"] == b"original"


def test_write_file_overwrites_when_asked(disk: FakeDisk) -> None:
    disk.add_file("disk:/notes.md", b"original")
    tools.handle_write_file({"path": "/notes.md", "content": "new", "overwrite": True})
    assert disk.files["disk:/notes.md"] == b"new"


def test_write_file_requires_string_content(disk: FakeDisk) -> None:
    assert (
        "must be a string"
        in payload(tools.handle_write_file({"path": "/a.txt", "content": 42}))["error"]
    )


def test_write_file_honours_the_encoding(disk: FakeDisk) -> None:
    tools.handle_write_file({"path": "/cp.txt", "content": "привет", "encoding": "cp1251"})
    assert disk.files["disk:/cp.txt"] == "привет".encode("cp1251")


def test_upload_from_a_local_file(disk: FakeDisk, tmp_path) -> None:
    source = tmp_path / "local.bin"
    source.write_bytes(b"\x00\x01\x02")
    result = payload(tools.handle_upload({"path": "/up.bin", "local_path": str(source)}))
    assert result["bytes"] == 3
    assert disk.files["disk:/up.bin"] == b"\x00\x01\x02"


def test_upload_from_a_url_is_fetched_by_yandex(disk: FakeDisk) -> None:
    result = payload(tools.handle_upload({"path": "/remote.bin", "url": "https://e.test/f"}))
    assert result["status"] == "uploaded"
    assert disk.files["disk:/remote.bin"] == b"fetched-by-yandex"
    assert not any(r.url.host == "e.test" for r in disk.requests)


@pytest.mark.parametrize("args", [{}, {"local_path": "/tmp/x", "url": "https://e.test/f"}])
def test_upload_needs_exactly_one_source(disk: FakeDisk, args: dict) -> None:
    assert "exactly one" in payload(tools.handle_upload({"path": "/x", **args}))["error"]


def test_upload_reports_a_missing_local_file(disk: FakeDisk, tmp_path) -> None:
    missing = str(tmp_path / "nope.bin")
    assert (
        "Local file error"
        in payload(tools.handle_upload({"path": "/x", "local_path": missing}))["error"]
    )


def test_copy_and_move(disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"x")
    assert payload(tools.handle_copy({"source": "/a.txt", "destination": "/b.txt"}))["status"] == (
        "copyd"
    )
    assert set(disk.files) == {"disk:/a.txt", "disk:/b.txt"}
    tools.handle_move({"source": "/b.txt", "destination": "/c.txt"})
    assert set(disk.files) == {"disk:/a.txt", "disk:/c.txt"}


def test_move_will_not_clobber_by_default(disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"a")
    disk.add_file("disk:/b.txt", b"b")
    assert (
        "exists"
        in payload(tools.handle_move({"source": "/a.txt", "destination": "/b.txt"}))["error"]
    )
    assert disk.files["disk:/b.txt"] == b"b"


def test_trash_restore(disk: FakeDisk) -> None:
    disk.add_file("disk:/gone.txt", b"x")
    tools.handle_delete({"path": "/gone.txt"})
    key = next(iter(disk.trash))
    assert payload(tools.handle_trash_restore({"path": key}))["status"] == "restored"
    assert "disk:/gone.txt" in disk.files


# -- share ----------------------------------------------------------------


def test_publish_and_unpublish(disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"x")
    published = payload(tools.handle_publish({"path": "/a.txt"}))
    assert published["public_url"].startswith("https://disk.yandex.ru/d/")
    revoked = payload(tools.handle_publish({"path": "/a.txt", "unpublish": True}))
    assert revoked["status"] == "unpublished"
    assert revoked["public_url"] is None


# -- delete ---------------------------------------------------------------


def test_delete_goes_to_the_bin_and_says_so(disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"x")
    result = payload(tools.handle_delete({"path": "/a.txt"}))
    assert result["recoverable"] is True
    assert result["status"] == "moved to the bin"
    assert disk.trash


def test_permanent_delete_skips_the_bin(disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"x")
    result = payload(tools.handle_delete({"path": "/a.txt", "permanently": True}))
    assert result["recoverable"] is False
    assert disk.trash == {}


def test_trash_empty_destroys_everything(disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"x")
    tools.handle_delete({"path": "/a.txt"})
    assert payload(tools.handle_trash_empty({}))["recoverable"] is False
    assert disk.trash == {}


def test_trash_empty_can_target_one_item(disk: FakeDisk) -> None:
    for name in ("a", "b"):
        disk.add_file(f"disk:/{name}.txt", b"x")
        tools.handle_delete({"path": f"/{name}.txt"})
    victim = next(iter(disk.trash))
    assert payload(tools.handle_trash_empty({"path": victim}))["status"].startswith("destroyed")
    assert len(disk.trash) == 1


# -- the sandbox ----------------------------------------------------------


@pytest.fixture
def sandboxed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ROOT_ENV, "/Hermes")


def test_relative_paths_land_inside_the_sandbox(sandboxed: None, disk: FakeDisk) -> None:
    tools.handle_write_file({"path": "notes.md", "content": "x"})
    assert "disk:/Hermes/notes.md" in disk.files


def test_results_hide_the_sandbox_prefix(sandboxed: None, disk: FakeDisk) -> None:
    disk.add_file("disk:/Hermes/notes.md", b"x")
    result = payload(tools.handle_list({}))
    assert result["path"] == "disk:/"
    assert [i["path"] for i in result["items"]] == ["disk:/notes.md"]


def test_an_absolute_path_outside_the_sandbox_is_refused(sandboxed: None, disk: FakeDisk) -> None:
    disk.add_file("disk:/Private/secrets.txt", b"x")
    assert (
        "outside the configured root"
        in payload(tools.handle_read_file({"path": "disk:/Private/secrets.txt"}))["error"]
    )


def test_traversal_out_of_the_sandbox_is_refused(sandboxed: None, disk: FakeDisk) -> None:
    assert (
        "outside the configured root"
        in payload(tools.handle_delete({"path": "../Private/secrets.txt"}))["error"]
    )


def test_search_stays_inside_the_sandbox(sandboxed: None, disk: FakeDisk) -> None:
    disk.add_file("disk:/Hermes/report.txt", b"x")
    disk.add_file("disk:/Private/report.txt", b"x")
    result = payload(tools.handle_search({"query": "report"}))
    assert [i["path"] for i in result["items"]] == ["disk:/report.txt"]


def test_the_bin_only_shows_items_deleted_from_the_sandbox(sandboxed: None, disk: FakeDisk) -> None:
    disk.add_file("disk:/Hermes/mine.txt", b"x")
    disk.add_file("disk:/Private/theirs.txt", b"x")
    tools.handle_delete({"path": "mine.txt"})
    disk.trash["trash:/theirs.txt_dead"] = {
        "path": "trash:/theirs.txt_dead",
        "name": "theirs.txt",
        "type": "file",
        "origin_path": "disk:/Private/theirs.txt",
    }
    items = payload(tools.handle_trash_list({}))["items"]
    assert [i["name"] for i in items] == ["mine.txt"]


def test_restoring_something_deleted_from_outside_the_sandbox_is_refused(
    sandboxed: None, disk: FakeDisk
) -> None:
    disk.trash["trash:/theirs.txt_dead"] = {
        "path": "trash:/theirs.txt_dead",
        "name": "theirs.txt",
        "type": "file",
        "origin_path": "disk:/Private/theirs.txt",
    }
    result = payload(tools.handle_trash_restore({"path": "trash:/theirs.txt_dead"}))
    assert "outside the configured root" in result["error"]
    assert disk.trash


def test_emptying_the_whole_bin_is_refused_inside_a_sandbox(
    sandboxed: None, disk: FakeDisk
) -> None:
    disk.add_file("disk:/Hermes/a.txt", b"x")
    tools.handle_delete({"path": "a.txt"})
    assert "YANDEX_DISK_ROOT" in payload(tools.handle_trash_empty({}))["error"]
    assert disk.trash


def test_a_sandboxed_bin_item_can_still_be_destroyed(sandboxed: None, disk: FakeDisk) -> None:
    disk.add_file("disk:/Hermes/a.txt", b"x")
    tools.handle_delete({"path": "a.txt"})
    victim = next(iter(disk.trash))
    assert payload(tools.handle_trash_empty({"path": victim}))["recoverable"] is False
    assert disk.trash == {}


def test_disk_info_reports_the_sandbox(sandboxed: None, disk: FakeDisk) -> None:
    assert payload(tools.handle_disk_info({}))["root"] == "disk:/Hermes"


# -- the never-raise contract ---------------------------------------------


def test_an_unexpected_exception_becomes_an_error_payload(
    monkeypatch: pytest.MonkeyPatch, disk: FakeDisk
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(tools, "resolve", explode)
    assert payload(tools.handle_list({}))["error"] == "Unexpected RuntimeError: kaboom"


def test_handlers_accept_the_registry_kwargs(disk: FakeDisk) -> None:
    assert payload(tools.handle_disk_info({}, parent_agent=None))["login"] == "tester"


def test_handlers_tolerate_no_arguments_at_all(disk: FakeDisk) -> None:
    assert payload(tools.handle_disk_info())["login"] == "tester"

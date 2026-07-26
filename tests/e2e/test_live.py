"""Live tests against a real Yandex Disk account.

Run with ``pytest -m e2e``. They create, publish and destroy real files — always
point them at a throwaway account. Everything lands inside the ``workspace``
fixture's folder, which is removed permanently in teardown.

Assertions print what the API actually returned, because a live failure is only
diagnosable from the server's own answer.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest

from hermes_yandex_disk import config, tools

pytestmark = pytest.mark.e2e

#: Yandex indexes uploads asynchronously; content (OCR/full-text) lags a name.
_NAME_INDEX_TIMEOUT = 45.0
_CONTENT_INDEX_TIMEOUT = 120.0
_POLL_INTERVAL = 4.0


def call(handler: Any, **args: Any) -> dict[str, Any]:
    """Invoke a tool handler and parse its JSON, failing loudly on an error payload."""
    result = json.loads(handler(args))
    assert "error" not in result, f"{handler.__name__}({args}) -> {result}"
    return result


def failing(handler: Any, **args: Any) -> str:
    """Invoke a handler that is expected to refuse, and return the message."""
    result = json.loads(handler(args))
    assert "error" in result, f"{handler.__name__}({args}) unexpectedly succeeded: {result}"
    return str(result["error"])


@pytest.fixture
def require_search() -> None:
    """Skip a search test when the configured token has no search permission.

    Search is gated to certain Yandex apps; a token without the grant (e.g. the
    Poligon playground token) 403s. Those runs skip rather than fail.
    """
    if not config.search_supported():
        pytest.skip("The configured token cannot use search (403); skipping search e2e.")


def poll_search(match: Callable[[dict[str, Any]], bool], *, timeout: float, **args: Any):
    """Search until a hit satisfies ``match`` or ``timeout`` elapses.

    Returns the matching hit, or ``None`` if indexing did not catch up in time —
    the caller decides whether that is a skip (asynchronous indexing) or a fail.
    """
    deadline = time.monotonic() + timeout
    while True:
        for hit in call(tools.handle_search, **args)["items"]:
            if match(hit):
                return hit
        if time.monotonic() >= deadline:
            return None
        time.sleep(_POLL_INTERVAL)


def test_disk_info_matches_the_configured_account() -> None:
    info = call(tools.handle_disk_info)
    assert info["total_space"] > 0, info
    expected = os.environ.get("YANDEX_DISK_E2E_LOGIN", "")
    if expected:
        assert info["login"].lower() == expected.split("@")[0].lower(), info


def test_full_file_lifecycle(workspace: str) -> None:
    """Create, read, copy, move, publish and delete a file the way the agent would."""
    note = f"{workspace}/notes/отчёт.md"
    body = "# Отчёт\n\nЖивой тест Hermes.\n"

    written = call(tools.handle_write_file, path=note, content=body)
    assert written["bytes"] == len(body.encode()), written

    listing = call(tools.handle_list, path=f"{workspace}/notes")
    assert [item["name"] for item in listing["items"]] == ["отчёт.md"], listing

    read_back = call(tools.handle_read_file, path=note)
    assert read_back["content"] == body, read_back

    copied = call(tools.handle_copy, source=note, destination=f"{workspace}/copy.md")
    assert copied["status"] == "copyd", copied

    moved = call(tools.handle_move, source=f"{workspace}/copy.md", destination=f"{workspace}/b.md")
    assert moved["status"] == "moved", moved

    published = call(tools.handle_publish, path=f"{workspace}/b.md")
    assert published["public_url"].startswith("https://"), published

    revoked = call(tools.handle_publish, path=f"{workspace}/b.md", unpublish=True)
    assert revoked["public_url"] is None, revoked

    meta = call(tools.handle_list, path=f"{workspace}/b.md")
    assert meta.get("public_url") is None, meta


def test_write_does_not_clobber_without_permission(workspace: str) -> None:
    path = f"{workspace}/guard.txt"
    call(tools.handle_write_file, path=path, content="original")
    assert "exist" in failing(tools.handle_write_file, path=path, content="replacement").lower()
    assert call(tools.handle_read_file, path=path)["content"] == "original"

    call(tools.handle_write_file, path=path, content="replacement", overwrite=True)
    assert call(tools.handle_read_file, path=path)["content"] == "replacement"


def test_upload_and_download_round_trip(workspace: str, tmp_path) -> None:
    source = tmp_path / "payload.bin"
    source.write_bytes(bytes(range(256)))

    uploaded = call(tools.handle_upload, path=f"{workspace}/payload.bin", local_path=str(source))
    assert uploaded["bytes"] == 256, uploaded

    target = tmp_path / "downloaded.bin"
    downloaded = call(tools.handle_download, path=f"{workspace}/payload.bin", save_to=str(target))
    assert downloaded["bytes"] == 256, downloaded
    assert target.read_bytes() == source.read_bytes()


def test_binary_files_are_refused_by_read_file(workspace: str, tmp_path) -> None:
    source = tmp_path / "payload.bin"
    source.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    call(tools.handle_upload, path=f"{workspace}/payload.bin", local_path=str(source))
    assert "yadisk_download" in failing(tools.handle_read_file, path=f"{workspace}/payload.bin")


def test_delete_goes_to_the_bin_and_can_be_restored(workspace: str) -> None:
    path = f"{workspace}/recoverable.txt"
    call(tools.handle_write_file, path=path, content="precious")

    deleted = call(tools.handle_delete, path=path)
    assert deleted["recoverable"] is True, deleted

    entries = call(tools.handle_trash_list, limit=100)["items"]
    mine = [e for e in entries if e.get("origin_path") == f"disk:{path}"]
    assert mine, f"not in the bin: {entries}"

    call(tools.handle_trash_restore, path=mine[0]["path"])
    assert call(tools.handle_read_file, path=path)["content"] == "precious"

    # And clean the bin entry the restore left behind, if any.
    call(tools.handle_delete, path=path, permanently=True)


def test_bin_entries_can_be_destroyed_individually(workspace: str) -> None:
    path = f"{workspace}/doomed.txt"
    call(tools.handle_write_file, path=path, content="x")
    call(tools.handle_delete, path=path)

    entries = call(tools.handle_trash_list, limit=100)["items"]
    mine = [e for e in entries if e.get("origin_path") == f"disk:{path}"]
    assert mine, f"not in the bin: {entries}"

    call(tools.handle_trash_empty, path=mine[0]["path"])
    remaining = call(tools.handle_trash_list, limit=100)["items"]
    assert not [e for e in remaining if e.get("origin_path") == f"disk:{path}"], remaining


def test_the_sandbox_holds_against_a_real_disk(
    workspace: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With YANDEX_DISK_ROOT set, nothing outside the folder is reachable."""
    monkeypatch.setenv(config.ROOT_ENV, workspace)

    call(tools.handle_write_file, path="inside.txt", content="fine")
    assert call(tools.handle_list)["items"][0]["path"] == "disk:/inside.txt"

    # An absolute path elsewhere on the real disk is refused before any request.
    outside = "disk:/Загрузки/anything.txt"
    assert "outside the configured root" in failing(tools.handle_read_file, path=outside)
    assert "outside the configured root" in failing(tools.handle_delete, path="disk:/")
    assert "escapes the root" in failing(tools.handle_list, path="../../..")
    assert config.ROOT_ENV in failing(tools.handle_trash_empty)


def test_a_missing_resource_reports_the_english_description(workspace: str) -> None:
    message = failing(tools.handle_read_file, path=f"{workspace}/definitely-not-here.txt")
    assert "not found" in message.lower(), message


# -- search (needs a token whose app is allowlisted for it) ----------------


def test_search_finds_a_file_by_name(require_search: None, workspace: str) -> None:
    marker = f"needle{uuid.uuid4().hex[:10]}"
    call(tools.handle_write_file, path=f"{workspace}/{marker}-report.txt", content="body")

    # The marker is in the name only (content is "body"), so a hit proves name
    # search. The exact search_scope label is Yandex's own classification and is
    # not stable (a filename token can come back as "tags"), so it is not asserted.
    hit = poll_search(
        lambda h: h["name"] == f"{marker}-report.txt",
        timeout=_NAME_INDEX_TIMEOUT,
        query=marker,
        path=workspace,
    )
    if hit is None:
        pytest.skip(f"Name index did not catch up within {_NAME_INDEX_TIMEOUT}s (async).")
    assert hit["path"] == f"disk:{workspace}/{marker}-report.txt", hit
    assert hit.get("search_scope"), hit


def test_search_finds_a_file_by_its_contents(require_search: None, workspace: str) -> None:
    """The real value of the endpoint: the query is inside the file, not its name."""
    marker = f"cephalopod{uuid.uuid4().hex[:10]}"
    call(
        tools.handle_write_file,
        path=f"{workspace}/plain-name.txt",  # name deliberately has no marker
        content=f"This document mentions a rare {marker} from the deep sea.",
    )

    hit = poll_search(
        lambda h: h["name"] == "plain-name.txt",
        timeout=_CONTENT_INDEX_TIMEOUT,
        query=marker,
        path=workspace,
    )
    if hit is None:
        pytest.skip(f"Content index did not catch up within {_CONTENT_INDEX_TIMEOUT}s (async).")
    # The marker appears nowhere in the name, so finding the file at all proves
    # content indexing (full text / OCR / metadata). A "name" scope here would be
    # a contradiction, so that one value is worth ruling out.
    assert hit.get("search_scope") != "name", hit


def test_search_is_confined_to_the_given_folder(require_search: None, workspace: str) -> None:
    marker = f"scoped{uuid.uuid4().hex[:10]}"
    call(tools.handle_write_file, path=f"{workspace}/inside/{marker}.txt", content="x")
    call(tools.handle_write_file, path=f"/{marker}-outside.txt", content="x")
    try:
        inside = poll_search(
            lambda h: marker in h["name"],
            timeout=_NAME_INDEX_TIMEOUT,
            query=marker,
            path=f"{workspace}/inside",
        )
        if inside is None:
            pytest.skip(f"Name index did not catch up within {_NAME_INDEX_TIMEOUT}s (async).")
        # Scanned across the disk, but only the in-scope hit is returned.
        scoped = call(tools.handle_search, query=marker, path=f"{workspace}/inside")
        assert [h["name"] for h in scoped["items"]] == [f"{marker}.txt"], scoped
    finally:
        call(tools.handle_delete, path=f"/{marker}-outside.txt", permanently=True)

"""The REST client: request shape, error translation, retries, deferred operations."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from hermes_yandex_disk.client import (
    API_BASE,
    DownloadTooLarge,
    YandexDiskClient,
    YandexDiskError,
)

from .conftest import API, FakeDisk


def _query(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


def test_disk_info(client: YandexDiskClient, disk: FakeDisk) -> None:
    assert client.disk_info()["user"]["login"] == "tester"
    assert disk.requests[0].headers["Authorization"] == "OAuth test-token"


def test_get_meta_sends_only_the_parameters_it_was_given(
    client: YandexDiskClient, disk: FakeDisk
) -> None:
    disk.add_dir("disk:/a")
    client.get_meta("disk:/a", limit=5)
    query = _query(disk.requests[-1])
    assert query == {"path": "disk:/a", "limit": "5"}


def test_missing_resource_raises_with_the_english_description(client: YandexDiskClient) -> None:
    with pytest.raises(YandexDiskError) as excinfo:
        client.get_meta("disk:/nope")
    assert excinfo.value.status == 404
    assert excinfo.value.code == "DiskNotFoundError"
    assert "not found" in excinfo.value.message.lower()
    assert excinfo.value.as_dict() == {
        "error": "Resource not found.",
        "code": "DiskNotFoundError",
        "status": 404,
    }


def test_error_without_a_json_body_falls_back_to_the_status(disk: FakeDisk) -> None:
    disk.fail_next = httpx.Response(500, content=b"<html>oops</html>")
    client = YandexDiskClient(
        "t",
        base_url=API,
        client=httpx.Client(transport=httpx.MockTransport(disk.handler)),
        max_retries=0,
    )
    with pytest.raises(YandexDiskError, match="HTTP 500"):
        client.disk_info()


def test_transient_statuses_are_retried(monkeypatch: pytest.MonkeyPatch, disk: FakeDisk) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    disk.fail_next = httpx.Response(503, json={"error": "x", "description": "busy"})
    client = YandexDiskClient(
        "t",
        base_url=API,
        client=httpx.Client(transport=httpx.MockTransport(disk.handler)),
        max_retries=2,
    )
    assert client.disk_info()["user"]["login"] == "tester"
    assert len(disk.requests) == 2


def test_retry_honours_retry_after(monkeypatch: pytest.MonkeyPatch, disk: FakeDisk) -> None:
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    disk.fail_next = httpx.Response(
        429, json={"description": "slow down"}, headers={"Retry-After": "2"}
    )
    client = YandexDiskClient(
        "t",
        base_url=API,
        client=httpx.Client(transport=httpx.MockTransport(disk.handler)),
        max_retries=1,
    )
    client.disk_info()
    assert slept == [2.0]


def test_a_nonsense_retry_after_falls_back_to_backoff(
    monkeypatch: pytest.MonkeyPatch, disk: FakeDisk
) -> None:
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    disk.fail_next = httpx.Response(429, json={}, headers={"Retry-After": "soon"})
    client = YandexDiskClient(
        "t",
        base_url=API,
        client=httpx.Client(transport=httpx.MockTransport(disk.handler)),
        max_retries=1,
    )
    client.disk_info()
    assert slept == [1.0]


def test_transport_failures_are_retried_then_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    attempts: list[int] = []

    def boom(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("no route to host")

    client = YandexDiskClient(
        "t", base_url=API, client=httpx.Client(transport=httpx.MockTransport(boom)), max_retries=2
    )
    with pytest.raises(YandexDiskError, match="Could not reach Yandex Disk"):
        client.disk_info()
    assert len(attempts) == 3


def test_deleting_a_folder_waits_for_the_deferred_operation(
    client: YandexDiskClient, disk: FakeDisk
) -> None:
    disk.add_dir("disk:/a")
    assert client.delete("disk:/a") == {"status": "success"}
    assert "disk:/a" not in disk.dirs
    assert any("/operations/" in str(r.url) for r in disk.requests)


def test_a_failed_operation_is_reported(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_dir("disk:/a")
    disk.operations["op1"] = "failed"
    with pytest.raises(YandexDiskError, match="failed"):
        client.delete("disk:/a")


def test_an_operation_that_never_finishes_times_out(
    client: YandexDiskClient, disk: FakeDisk
) -> None:
    disk.operations["op1"] = "in-progress"
    with pytest.raises(YandexDiskError, match="still processing"):
        client.wait_for_operation(f"{API}/operations/op1", timeout=0)


def test_waiting_on_an_empty_href_is_a_no_op(client: YandexDiskClient, disk: FakeDisk) -> None:
    assert client.wait_for_operation("") == {"status": "success"}
    assert disk.requests == []


def test_polling_surfaces_an_api_error(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.fail_next = httpx.Response(401, json={"description": "Unauthorized"})
    with pytest.raises(YandexDiskError, match="Unauthorized"):
        client.wait_for_operation(f"{API}/operations/op1")


def test_upload_round_trip(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_dir("disk:/a")
    client.upload("disk:/a/b.txt", b"payload")
    assert disk.files["disk:/a/b.txt"] == b"payload"


def test_upload_reports_a_rejected_transfer(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_dir("disk:/a")
    original = disk.handler

    def fail_on_the_uploader(request: httpx.Request) -> httpx.Response:
        if request.url.host == "uploader.example.net":
            return httpx.Response(507, json={"description": "Not enough space"})
        return original(request)

    client._client = httpx.Client(transport=httpx.MockTransport(fail_on_the_uploader))
    with pytest.raises(YandexDiskError, match="Not enough space"):
        client.upload("disk:/a/c.txt", b"x")
    assert "disk:/a/c.txt" not in disk.files


def test_an_operation_in_progress_is_polled_until_it_finishes(
    monkeypatch: pytest.MonkeyPatch, client: YandexDiskClient, disk: FakeDisk
) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    disk.add_dir("disk:/a")
    statuses = iter(["in-progress", "in-progress", "success"])
    original = disk.handler

    def slow_operation(request: httpx.Request) -> httpx.Response:
        if "/operations/" in str(request.url):
            return httpx.Response(200, json={"status": next(statuses)})
        return original(request)

    client._client = httpx.Client(transport=httpx.MockTransport(slow_operation))
    assert client.delete("disk:/a") == {"status": "success"}


def test_upload_from_url_defers_to_yandex(client: YandexDiskClient, disk: FakeDisk) -> None:
    client.upload_from_url("disk:/fetched.bin", "https://example.com/f")
    assert disk.files["disk:/fetched.bin"] == b"fetched-by-yandex"


def test_download_bytes(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"hello")
    assert client.download_bytes("disk:/a.txt", max_bytes=100) == b"hello"


def test_download_refuses_to_exceed_the_budget(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"hello world")
    with pytest.raises(DownloadTooLarge):
        client.download_bytes("disk:/a.txt", max_bytes=3)


def test_download_to_file(client: YandexDiskClient, disk: FakeDisk, tmp_path) -> None:
    disk.add_file("disk:/a.txt", b"hello")
    target = tmp_path / "out.txt"
    assert client.download_to_file("disk:/a.txt", str(target), max_bytes=100) == 5
    assert target.read_bytes() == b"hello"


def test_download_to_file_refuses_to_exceed_the_budget(
    client: YandexDiskClient, disk: FakeDisk, tmp_path
) -> None:
    disk.add_file("disk:/a.txt", b"hello world")
    with pytest.raises(DownloadTooLarge):
        client.download_to_file("disk:/a.txt", str(tmp_path / "out.txt"), max_bytes=3)


def test_download_link_errors_are_surfaced(client: YandexDiskClient) -> None:
    with pytest.raises(YandexDiskError, match="not found"):
        client.download_bytes("disk:/missing.txt", max_bytes=10)


def test_download_link_requires_an_href(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.fail_next = httpx.Response(200, json={})
    with pytest.raises(YandexDiskError, match="did not return a download link"):
        client.download_link("disk:/a.txt")


def test_download_stream_error_is_translated(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"hello")
    original = disk.handler

    def fail_on_the_downloader(request: httpx.Request) -> httpx.Response:
        if request.url.host == "downloader.example.net":
            return httpx.Response(410, json={"description": "Link expired"})
        return original(request)

    client._client = httpx.Client(transport=httpx.MockTransport(fail_on_the_downloader))
    with pytest.raises(YandexDiskError, match="Link expired"):
        client.download_bytes("disk:/a.txt", max_bytes=100)


def test_download_to_file_stream_error_is_translated(
    client: YandexDiskClient, disk: FakeDisk, tmp_path
) -> None:
    disk.add_file("disk:/a.txt", b"hello")
    original = disk.handler

    def fail_on_the_downloader(request: httpx.Request) -> httpx.Response:
        if request.url.host == "downloader.example.net":
            return httpx.Response(410, json={"description": "Link expired"})
        return original(request)

    client._client = httpx.Client(transport=httpx.MockTransport(fail_on_the_downloader))
    with pytest.raises(YandexDiskError, match="Link expired"):
        client.download_to_file("disk:/a.txt", str(tmp_path / "x"), max_bytes=100)


def test_copy_and_move_send_from_and_path(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"x")
    client.copy("disk:/a.txt", "disk:/b.txt")
    assert _query(disk.requests[-1]) == {
        "from": "disk:/a.txt",
        "path": "disk:/b.txt",
        "overwrite": "false",
    }
    client.move("disk:/b.txt", "disk:/c.txt", overwrite=True)
    assert _query(disk.requests[-1])["overwrite"] == "true"
    assert set(disk.files) == {"disk:/a.txt", "disk:/c.txt"}


def test_publish_returns_the_public_url(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"x")
    assert client.publish("disk:/a.txt")["public_url"].startswith("https://disk.yandex.ru/d/")
    assert client.unpublish("disk:/a.txt").get("public_url") is None


def test_trash_helpers(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"x")
    client.delete("disk:/a.txt")
    key = next(iter(disk.trash))
    assert client.get_trash_meta(key)["origin_path"] == "disk:/a.txt"
    assert client.trash_list()["_embedded"]["items"][0]["name"] == "a.txt"
    client.trash_restore(key)
    assert "disk:/a.txt" in disk.files


def test_trash_delete_empties_everything(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_file("disk:/a.txt", b"x")
    client.delete("disk:/a.txt")
    client.trash_delete()
    assert disk.trash == {}


def test_search_returns_matching_files(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.add_file("disk:/Docs/alpha.txt", b"x")
    disk.add_file("disk:/beta.txt", b"x")
    hits = client.search("alpha")
    assert [h["path"] for h in hits] == ["disk:/Docs/alpha.txt"]
    query = _query(disk.requests[-1])
    assert query["query"] == "alpha" and query["limit"] == "20"


def test_search_passes_media_type_and_paging(client: YandexDiskClient, disk: FakeDisk) -> None:
    client.search("x", limit=5, offset=10, media_type="image", sort="name")
    query = _query(disk.requests[-1])
    assert query == {
        "query": "x",
        "limit": "5",
        "offset": "10",
        "media_type": "image",
        "sort": "name",
    }


def test_search_forbidden_raises_403(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.search_allowed = False
    with pytest.raises(YandexDiskError) as excinfo:
        client.search("x")
    assert excinfo.value.status == 403


def test_search_non_list_items_is_empty(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.fail_next = httpx.Response(200, json={"items": "nope"})
    assert client.search("x") == []


def test_client_owns_its_default_http_client() -> None:
    client = YandexDiskClient("t")
    assert client.base_url == API_BASE
    with client:
        pass
    assert client._client.is_closed


def test_non_dict_json_is_treated_as_empty(client: YandexDiskClient, disk: FakeDisk) -> None:
    disk.fail_next = httpx.Response(200, content=json.dumps([1, 2]).encode())
    assert client.disk_info() == {}

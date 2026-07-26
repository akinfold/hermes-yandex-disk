"""Shared fixtures: an in-memory stand-in for the Yandex Disk REST API.

The fake speaks the parts of the protocol the plugin depends on — deferred
operations, the two-step upload/download handshake, the ``{error, description}``
envelope — so the tool tests drive the real client and the real handlers, and
only the network is fictional.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from hermes_yandex_disk import config
from hermes_yandex_disk.client import YandexDiskClient

API = "https://cloud-api.yandex.net/v1/disk"
UPLOAD_HOST = "https://uploader.example.net/target"
DOWNLOAD_HOST = "https://downloader.example.net/file"

_ENV_VARS = (
    config.TOKEN_ENV,
    config.TOKEN_ENV_ALIAS,
    config.ROOT_ENV,
    config.ACTIONS_ENV,
    config.BASE_URL_ENV,
    config.TIMEOUT_ENV,
    config.MAX_READ_BYTES_ENV,
    config.MAX_DOWNLOAD_BYTES_ENV,
)


@pytest.fixture(autouse=True)
def clean_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient Yandex Disk configuration leaks into a unit test.

    Live e2e tests are exempt — they need the real credentials this would erase.
    """
    if request.node.get_closest_marker("e2e"):
        return
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(config.TOKEN_ENV, "test-token")


def _json(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _error(status: int, code: str, description: str) -> httpx.Response:
    return _json({"error": code, "description": description, "message": description}, status)


def _parent(path: str) -> str:
    head = path.rpartition("/")[0]
    return f"{head}/" if head.endswith(":") else head


class FakeDisk:
    """A tiny Yandex Disk: a flat map of paths to bytes, plus a bin."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"disk:/"}
        self.trash: dict[str, dict[str, Any]] = {}
        self.public: dict[str, str] = {}
        self.uploads: dict[str, tuple[str, bool]] = {}
        self.operations: dict[str, str] = {}
        self.requests: list[httpx.Request] = []
        self.fail_next: httpx.Response | None = None
        self._deferred = 0

    # -- helpers ------------------------------------------------------

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.dirs

    def add_file(self, path: str, data: bytes = b"") -> None:
        self.files[path] = data
        self.dirs.update(self._ancestors(path))

    def add_dir(self, path: str) -> None:
        self.dirs.add(path)
        self.dirs.update(self._ancestors(path))

    @staticmethod
    def _ancestors(path: str) -> set[str]:
        rest = path.split(":", 1)[1].strip("/")
        parts = rest.split("/")[:-1]
        return {"disk:/" + "/".join(parts[: i + 1]) for i in range(len(parts))} | {"disk:/"}

    def _meta(self, path: str) -> dict[str, Any]:
        name = path.rstrip("/").rsplit("/", 1)[-1] or "disk"
        if path in self.files:
            return {
                "path": path,
                "name": name,
                "type": "file",
                "size": len(self.files[path]),
                "mime_type": "text/plain",
                "media_type": "document",
                "modified": "2026-01-01T00:00:00+00:00",
                "public_url": self.public.get(path),
            }
        return {"path": path, "name": name, "type": "dir", "modified": "2026-01-01T00:00:00+00:00"}

    def _children(self, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/"
        out = []
        for candidate in sorted(self.files | dict.fromkeys(self.dirs)):
            if candidate == path:
                continue
            if candidate.startswith(prefix) and "/" not in candidate[len(prefix) :]:
                out.append(candidate)
        return out

    # -- routing ------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next is not None:
            response, self.fail_next = self.fail_next, None
            return response
        url = urlparse(str(request.url))
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if str(request.url).startswith(UPLOAD_HOST):
            return self._put_bytes(request)
        if str(request.url).startswith(DOWNLOAD_HOST):
            return self._get_bytes(query)
        route = url.path.replace("/v1/disk", "", 1) or "/"
        return self._dispatch(request.method, route, query)

    def _dispatch(self, method: str, route: str, query: dict[str, str]) -> httpx.Response:
        table = {
            ("GET", "/"): lambda: _json(self._disk_info()),
            ("GET", "/resources"): lambda: self._get_resource(query),
            ("PUT", "/resources"): lambda: self._mkdir(query),
            ("DELETE", "/resources"): lambda: self._delete(query),
            ("POST", "/resources/copy"): lambda: self._transfer(query, keep=True),
            ("POST", "/resources/move"): lambda: self._transfer(query, keep=False),
            ("GET", "/resources/upload"): lambda: self._upload_link(query),
            ("POST", "/resources/upload"): lambda: self._upload_from_url(query),
            ("GET", "/resources/download"): lambda: self._download_link(query),
            ("GET", "/resources/files"): lambda: self._flat_files(query),
            ("PUT", "/resources/publish"): lambda: self._publish(query, True),
            ("PUT", "/resources/unpublish"): lambda: self._publish(query, False),
            ("GET", "/trash/resources"): lambda: self._trash_get(query),
            ("DELETE", "/trash/resources"): lambda: self._trash_delete(query),
            ("PUT", "/trash/resources/restore"): lambda: self._trash_restore(query),
        }
        if route.startswith("/operations/"):
            return _json({"status": self.operations.get(route.rsplit("/", 1)[-1], "success")})
        action = table.get((method, route))
        if action is None:  # pragma: no cover - a test asked for an unrouted endpoint
            return _error(404, "NotImplemented", f"Fake has no route for {method} {route}")
        return action()

    # -- endpoints ----------------------------------------------------

    def _disk_info(self) -> dict[str, Any]:
        return {
            "total_space": 100,
            "used_space": 40,
            "trash_size": 5,
            "max_file_size": 1000,
            "is_paid": False,
            "user": {"login": "tester"},
        }

    def _get_resource(self, query: dict[str, str]) -> httpx.Response:
        path = query.get("path", "disk:/")
        if not self.exists(path):
            return _error(404, "DiskNotFoundError", "Resource not found.")
        meta = self._meta(path)
        if meta["type"] == "dir":
            children = self._children(path)
            offset = int(query.get("offset", 0))
            limit = int(query.get("limit", 20))
            window = children[offset : offset + limit]
            meta["_embedded"] = {"total": len(children), "items": [self._meta(c) for c in window]}
        return _json(meta)

    def _mkdir(self, query: dict[str, str]) -> httpx.Response:
        path = query["path"]
        if path in self.dirs:
            return _error(409, "DiskPathPointsToExistentDirectoryError", "Already exists.")
        if _parent(path) not in self.dirs:
            return _error(409, "DiskPathDoesntExistsError", "Parent folder does not exist.")
        self.dirs.add(path)
        return _json({"href": f"{API}/resources?path={path}", "method": "GET"}, 201)

    def _delete(self, query: dict[str, str]) -> httpx.Response:
        path = query["path"]
        if not self.exists(path):
            return _error(404, "DiskNotFoundError", "Resource not found.")
        permanently = query.get("permanently") == "true"
        is_dir = path in self.dirs
        data = self.files.pop(path, None)
        self.dirs.discard(path)
        if not permanently:
            key = f"trash:/{path.rsplit('/', 1)[-1]}_deadbeef"
            self.trash[key] = {
                "path": key,
                "name": path.rsplit("/", 1)[-1],
                "type": "dir" if is_dir else "file",
                "origin_path": path,
                "deleted": "2026-01-01T00:00:00+00:00",
                "_data": data,
            }
        if is_dir:
            return self._defer()
        return httpx.Response(204)

    def _defer(self) -> httpx.Response:
        self._deferred += 1
        op = f"op{self._deferred}"
        self.operations.setdefault(op, "success")
        return _json({"href": f"{API}/operations/{op}", "method": "GET"}, 202)

    def _transfer(self, query: dict[str, str], *, keep: bool) -> httpx.Response:
        source, target = query["from"], query["path"]
        if not self.exists(source):
            return _error(404, "DiskNotFoundError", "Resource not found.")
        if self.exists(target) and query.get("overwrite") != "true":
            return _error(409, "DiskResourceAlreadyExistsError", "Destination already exists.")
        if source in self.files:
            self.files[target] = self.files[source] if keep else self.files.pop(source)
        else:
            self.dirs.add(target)
            if not keep:
                self.dirs.discard(source)
        return _json({"href": f"{API}/resources?path={target}", "method": "GET"}, 201)

    def _upload_link(self, query: dict[str, str]) -> httpx.Response:
        path = query["path"]
        overwrite = query.get("overwrite") == "true"
        if path in self.files and not overwrite:
            return _error(409, "DiskResourceAlreadyExistsError", "File already exists.")
        if _parent(path) not in self.dirs:
            return _error(409, "DiskPathDoesntExistsError", "Parent folder does not exist.")
        token = f"t{len(self.uploads) + 1}"
        self.uploads[token] = (path, overwrite)
        return _json({"href": f"{UPLOAD_HOST}/{token}", "method": "PUT", "operation_id": token})

    def _put_bytes(self, request: httpx.Request) -> httpx.Response:
        token = str(request.url).rsplit("/", 1)[-1]
        path, _ = self.uploads[token]
        self.add_file(path, request.read())
        self.operations[token] = "success"
        return httpx.Response(201)

    def _upload_from_url(self, query: dict[str, str]) -> httpx.Response:
        self.add_file(query["path"], b"fetched-by-yandex")
        return self._defer()

    def _download_link(self, query: dict[str, str]) -> httpx.Response:
        path = query["path"]
        if path not in self.files:
            return _error(404, "DiskNotFoundError", "Resource not found.")
        return _json({"href": f"{DOWNLOAD_HOST}?p={path}", "method": "GET"})

    def _get_bytes(self, query: dict[str, str]) -> httpx.Response:
        return httpx.Response(200, content=self.files[query["p"]])

    def _flat_files(self, query: dict[str, str]) -> httpx.Response:
        offset = int(query.get("offset", 0))
        limit = int(query.get("limit", 20))
        wanted = query.get("media_type")
        items = [self._meta(p) for p in sorted(self.files)]
        if wanted:
            items = [i for i in items if i.get("media_type") == wanted]
        return _json({"items": items[offset : offset + limit], "offset": offset})

    def _publish(self, query: dict[str, str], on: bool) -> httpx.Response:
        path = query["path"]
        if not self.exists(path):
            return _error(404, "DiskNotFoundError", "Resource not found.")
        if on:
            self.public[path] = f"https://disk.yandex.ru/d/{abs(hash(path)) % 10**10}"
        else:
            self.public.pop(path, None)
        return _json({"href": f"{API}/resources?path={path}", "method": "GET"})

    def _trash_get(self, query: dict[str, str]) -> httpx.Response:
        path = query.get("path", "trash:/")
        if path not in ("trash:/", "/"):
            entry = self.trash.get(path)
            if entry is None:
                return _error(404, "DiskNotFoundError", "Resource not found.")
            return _json({k: v for k, v in entry.items() if k != "_data"})
        items = [{k: v for k, v in e.items() if k != "_data"} for e in self.trash.values()]
        return _json({"path": "trash:/", "type": "dir", "_embedded": {"items": items}})

    def _trash_delete(self, query: dict[str, str]) -> httpx.Response:
        path = query.get("path")
        if path is None:
            self.trash.clear()
        elif path in self.trash:
            del self.trash[path]
        else:
            return _error(404, "DiskNotFoundError", "Resource not found.")
        return httpx.Response(204)

    def _trash_restore(self, query: dict[str, str]) -> httpx.Response:
        entry = self.trash.pop(query["path"], None)
        if entry is None:
            return _error(404, "DiskNotFoundError", "Resource not found.")
        target = str(entry["origin_path"])
        if entry["type"] == "dir":
            self.add_dir(target)
        else:
            self.add_file(target, entry.get("_data") or b"")
        return _json({"href": f"{API}/resources?path={target}", "method": "GET"}, 201)


@pytest.fixture
def disk() -> FakeDisk:
    return FakeDisk()


@pytest.fixture
def http_client(disk: FakeDisk) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(disk.handler))


@pytest.fixture
def client(http_client: httpx.Client) -> YandexDiskClient:
    return YandexDiskClient("test-token", base_url=API, client=http_client, max_retries=0)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, client: YandexDiskClient) -> YandexDiskClient:
    """Make ``config.build_client()`` hand the handlers the fake-backed client."""
    monkeypatch.setattr(config, "build_client", lambda: client)
    return client


def payload(raw: str) -> dict[str, Any]:
    """Parse a handler's JSON string result."""
    return json.loads(raw)

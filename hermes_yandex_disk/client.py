"""Yandex Disk REST API v1 client.

No Hermes imports — this module talks HTTP and nothing else, so it can be unit
tested against ``httpx.MockTransport`` without the host installed.

Everything the plugin does goes over the REST API (``cloud-api.yandex.net``);
WebDAV is never used. The REST API is a strict superset for our purposes: it is
the only one that exposes the bin, public links, the flat file index and
server-side copy/move, and it is what rclone's Yandex backend uses too.

Two API shapes matter and are handled here rather than in every caller:

* **Deferred operations.** Bulk endpoints answer ``202 Accepted`` with an
  ``operations/<id>`` link instead of doing the work inline. :meth:`_run` polls
  it to completion so callers always observe a finished operation.
* **Two-step transfers.** Uploads and downloads first ask the API for a
  single-use URL on a separate host, then transfer the bytes there — without the
  OAuth token, which that host neither needs nor should see.
"""

from __future__ import annotations

import time
from typing import Any, BinaryIO

import httpx

API_BASE = "https://cloud-api.yandex.net/v1/disk"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
OPERATION_POLL_INTERVAL = 0.5
OPERATION_TIMEOUT = 60.0


class YandexDiskError(Exception):
    """An API call failed. Carries the HTTP status and Yandex's error code."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.message}
        if self.code:
            out["code"] = self.code
        if self.status:
            out["status"] = self.status
        return out


class DownloadTooLarge(YandexDiskError):
    """The resource exceeds the caller's byte budget."""


def _error_from_response(response: httpx.Response) -> YandexDiskError:
    """Build an error from Yandex's ``{error, description, message}`` envelope."""
    code = None
    text = f"HTTP {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        code = payload.get("error")
        # `description` is English; `message` is localised to the account language.
        text = payload.get("description") or payload.get("message") or text
    return YandexDiskError(text, status=response.status_code, code=code)


def _retry_after(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After", "")
    try:
        return min(float(raw), 30.0)
    except ValueError:
        return min(2.0**attempt, 8.0)


class YandexDiskClient:
    """Thin, synchronous client for the endpoints the plugin needs."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client: httpx.Client | None = None,
    ):
        self._token = token
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._owns_client = client is None

    # -- plumbing ---------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> YandexDiskClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Issue one request, retrying transient statuses and transport errors."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    break
                time.sleep(min(2.0**attempt, 8.0))
                continue
            if response.status_code in RETRY_STATUSES and attempt < self._max_retries:
                time.sleep(_retry_after(response, attempt))
                continue
            return response
        raise YandexDiskError(f"Could not reach Yandex Disk: {last_error}") from last_error

    def _api(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """Call an authenticated API endpoint and raise on any error status."""
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        response = self._send(
            method,
            f"{self.base_url}{endpoint}",
            params=clean,
            json=json,
            headers={"Authorization": f"OAuth {self._token}", "Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise _error_from_response(response)
        return response

    def _json(self, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        payload = self._api(method, endpoint, **kwargs).json()
        return payload if isinstance(payload, dict) else {}

    def _run(self, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        """Call an endpoint that may defer, and wait for the deferred half."""
        response = self._api(method, endpoint, **kwargs)
        if response.status_code != 202:
            return {"status": "success"}
        link = response.json() if response.content else {}
        return self.wait_for_operation(link.get("href", ""))

    def wait_for_operation(
        self, href: str, *, timeout: float = OPERATION_TIMEOUT
    ) -> dict[str, Any]:
        """Poll a deferred operation until it succeeds, fails or the wait times out."""
        if not href:
            return {"status": "success"}
        deadline = time.monotonic() + timeout
        while True:
            status = self._poll(href)
            if status == "success":
                return {"status": "success"}
            if status == "failed":
                raise YandexDiskError("Yandex Disk reported the operation as failed.")
            if time.monotonic() >= deadline:
                raise YandexDiskError(
                    "Yandex Disk is still processing the operation; it may finish on its own."
                )
            time.sleep(OPERATION_POLL_INTERVAL)

    def _poll(self, href: str) -> str:
        response = self._send(
            "GET", href, headers={"Authorization": f"OAuth {self._token}"}, params=None
        )
        if response.status_code >= 400:
            raise _error_from_response(response)
        payload = response.json()
        return str(payload.get("status", "")) if isinstance(payload, dict) else ""

    # -- disk & metadata --------------------------------------------------

    def disk_info(self) -> dict[str, Any]:
        return self._json("GET", "/")

    def get_meta(
        self,
        path: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        sort: str | None = None,
        fields: str | None = None,
    ) -> dict[str, Any]:
        """Metadata for a file or folder; folders embed their children."""
        params = {"path": path, "limit": limit, "offset": offset, "sort": sort, "fields": fields}
        return self._json("GET", "/resources", params=params)

    def list_all_files(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        media_type: str | None = None,
        fields: str | None = None,
    ) -> list[dict[str, Any]]:
        """One page of the flat, disk-wide file index (files only, no folders)."""
        params = {"limit": limit, "offset": offset, "media_type": media_type, "fields": fields}
        payload = self._json("GET", "/resources/files", params=params)
        items = payload.get("items")
        return items if isinstance(items, list) else []

    # -- structure --------------------------------------------------------

    def mkdir(self, path: str) -> dict[str, Any]:
        return self._json("PUT", "/resources", params={"path": path})

    def copy(self, source: str, destination: str, *, overwrite: bool = False) -> dict[str, Any]:
        return self._transfer("copy", source, destination, overwrite)

    def move(self, source: str, destination: str, *, overwrite: bool = False) -> dict[str, Any]:
        return self._transfer("move", source, destination, overwrite)

    def _transfer(
        self, verb: str, source: str, destination: str, overwrite: bool
    ) -> dict[str, Any]:
        params = {"from": source, "path": destination, "overwrite": str(bool(overwrite)).lower()}
        return self._run("POST", f"/resources/{verb}", params=params)

    def delete(self, path: str, *, permanently: bool = False) -> dict[str, Any]:
        params = {"path": path, "permanently": str(bool(permanently)).lower()}
        return self._run("DELETE", "/resources", params=params)

    # -- transfers --------------------------------------------------------

    def upload_link(self, path: str, *, overwrite: bool = False) -> dict[str, Any]:
        params = {"path": path, "overwrite": str(bool(overwrite)).lower()}
        return self._json("GET", "/resources/upload", params=params)

    def upload(self, path: str, data: bytes | BinaryIO, *, overwrite: bool = False) -> None:
        """Ask for a single-use upload URL, PUT the bytes, then wait for the commit."""
        link = self.upload_link(path, overwrite=overwrite)
        response = self._send("PUT", link["href"], content=data)
        if response.status_code >= 400:
            raise _error_from_response(response)
        operation_id = link.get("operation_id")
        if operation_id:
            self.wait_for_operation(f"{self.base_url}/operations/{operation_id}")

    def upload_from_url(self, path: str, url: str) -> dict[str, Any]:
        """Have Yandex fetch a public URL straight into the disk (never proxied by us)."""
        return self._run("POST", "/resources/upload", params={"path": path, "url": url})

    def download_link(self, path: str) -> str:
        payload = self._json("GET", "/resources/download", params={"path": path})
        href = payload.get("href")
        if not href:
            raise YandexDiskError("Yandex Disk did not return a download link.")
        return str(href)

    def download_bytes(self, path: str, *, max_bytes: int) -> bytes:
        """Download a file, refusing anything past ``max_bytes`` without buffering it."""
        chunks: list[bytes] = []
        total = 0
        with self._client.stream("GET", self.download_link(path)) as response:
            if response.status_code >= 400:
                response.read()
                raise _error_from_response(response)
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise DownloadTooLarge(
                        f"File is larger than the {max_bytes} byte limit for this tool."
                    )
                chunks.append(chunk)
        return b"".join(chunks)

    def download_to_file(self, path: str, destination: str, *, max_bytes: int) -> int:
        """Stream a file to a local path. Returns the number of bytes written."""
        total = 0
        with self._client.stream("GET", self.download_link(path)) as response:
            if response.status_code >= 400:
                response.read()
                raise _error_from_response(response)
            with open(destination, "wb") as handle:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadTooLarge(
                            f"File is larger than the {max_bytes} byte limit for this tool."
                        )
                    handle.write(chunk)
        return total

    # -- sharing ----------------------------------------------------------

    def publish(self, path: str) -> dict[str, Any]:
        self._json("PUT", "/resources/publish", params={"path": path})
        return self.get_meta(path, fields="path,name,type,public_url,public_key")

    def unpublish(self, path: str) -> dict[str, Any]:
        self._json("PUT", "/resources/unpublish", params={"path": path})
        return self.get_meta(path, fields="path,name,type,public_url")

    # -- bin --------------------------------------------------------------

    def trash_list(
        self, *, path: str = "trash:/", limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        params = {"path": path, "limit": limit, "offset": offset}
        return self._json("GET", "/trash/resources", params=params)

    def get_trash_meta(self, path: str) -> dict[str, Any]:
        """Metadata for one item in the bin — notably its ``origin_path``."""
        params = {"path": path, "limit": 0, "fields": "path,name,type,origin_path,deleted"}
        return self._json("GET", "/trash/resources", params=params)

    def trash_restore(
        self, path: str, *, name: str | None = None, overwrite: bool = False
    ) -> dict[str, Any]:
        params = {"path": path, "name": name, "overwrite": str(bool(overwrite)).lower()}
        return self._run("PUT", "/trash/resources/restore", params=params)

    def trash_delete(self, path: str | None = None) -> dict[str, Any]:
        return self._run("DELETE", "/trash/resources", params={"path": path})

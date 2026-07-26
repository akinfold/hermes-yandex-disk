"""The search-capability probe: definitive answers cache, inconclusive ones don't."""

from __future__ import annotations

import httpx
import pytest

from hermes_yandex_disk import capabilities, config
from hermes_yandex_disk.client import YandexDiskClient

from .conftest import API, FakeDisk


@pytest.fixture(autouse=True)
def _clear_probe_cache() -> None:
    capabilities._reset_cache()


def _factory(disk: FakeDisk, token: str = "t"):
    def make() -> YandexDiskClient:
        # max_retries=0 so an inconclusive 5xx probe is not silently retried away.
        return YandexDiskClient(
            token,
            base_url=API,
            client=httpx.Client(transport=httpx.MockTransport(disk.handler)),
            max_retries=0,
        )

    return make


def test_a_permitted_token_reports_available(disk: FakeDisk) -> None:
    disk.search_allowed = True
    assert capabilities.search_available("tok", _factory(disk)) is True
    # The probe is a single, minimal call.
    assert sum("/resources/search" in str(r.url) for r in disk.requests) == 1


def test_a_forbidden_token_reports_unavailable(disk: FakeDisk) -> None:
    disk.search_allowed = False
    assert capabilities.search_available("tok", _factory(disk)) is False


def test_a_401_also_reads_as_unavailable(disk: FakeDisk) -> None:
    disk.fail_next = httpx.Response(401, json={"description": "Unauthorized"})
    assert capabilities.search_available("tok", _factory(disk)) is False


def test_definitive_answers_are_cached(disk: FakeDisk) -> None:
    assert capabilities.search_available("tok", _factory(disk)) is True
    before = len(disk.requests)
    assert capabilities.search_available("tok", _factory(disk)) is True
    assert disk.requests[before:] == []  # served from cache, no second probe


def test_different_tokens_probe_independently(disk: FakeDisk) -> None:
    assert capabilities.search_available("tok-a", _factory(disk)) is True
    n = len(disk.requests)
    capabilities.search_available("tok-b", _factory(disk))
    assert len(disk.requests) > n  # a new token re-probes


def test_an_inconclusive_probe_is_not_cached(disk: FakeDisk) -> None:
    disk.fail_next = httpx.Response(503, json={"description": "busy"})
    assert capabilities.search_available("tok", _factory(disk), use_cache=True) is False
    # A 5xx is not a verdict, so the next load tries again — and now succeeds.
    assert capabilities.search_available("tok", _factory(disk)) is True


def test_a_transport_failure_reads_as_unavailable() -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    def make() -> YandexDiskClient:
        return YandexDiskClient(
            "t",
            base_url=API,
            client=httpx.Client(transport=httpx.MockTransport(boom)),
            max_retries=0,
        )

    assert capabilities.search_available("tok", make) is False


def test_config_search_supported_is_false_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(config.TOKEN_ENV, raising=False)
    monkeypatch.delenv(config.TOKEN_ENV_ALIAS, raising=False)
    assert config.search_supported() is False


def test_config_search_supported_probes_with_the_configured_token(
    monkeypatch: pytest.MonkeyPatch, disk: FakeDisk
) -> None:
    monkeypatch.setenv(config.TOKEN_ENV, "live")
    monkeypatch.setattr(config, "build_client", _factory(disk, "live"))
    disk.search_allowed = True
    assert config.search_supported() is True
    disk.search_allowed = False
    # Cached from the first probe, so the verdict is stable within a process.
    assert config.search_supported() is True

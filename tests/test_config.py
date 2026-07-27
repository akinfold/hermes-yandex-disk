"""Credentials, limits and the YANDEX_DISK_ACTIONS allow-list."""

from __future__ import annotations

import pytest

from hermes_yandex_disk import config


def test_credentials_come_from_either_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(config.TOKEN_ENV)
    assert not config.credentials_present()
    monkeypatch.setenv(config.TOKEN_ENV_ALIAS, "alias-token")
    assert config.credentials_present()
    assert config.token() == "alias-token"
    monkeypatch.setenv(config.TOKEN_ENV, "primary-token")
    assert config.token() == "primary-token"


def test_build_client_without_a_token_explains_where_to_get_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(config.TOKEN_ENV)
    with pytest.raises(config.ConfigError, match=r"yandex\.ru/dev/disk"):
        config.build_client()


def test_build_client_honours_the_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.BASE_URL_ENV, "https://example.test/v1/disk/")
    with config.build_client() as client:
        assert client.base_url == "https://example.test/v1/disk"


def test_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    with config.build_client() as client:
        assert client.base_url == "https://cloud-api.yandex.net/v1/disk"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", ""), ("/", ""), ("/Hermes", "/Hermes"), ("Hermes/sub/", "/Hermes/sub")],
)
def test_root(monkeypatch: pytest.MonkeyPatch, raw: str, expected: str) -> None:
    monkeypatch.setenv(config.ROOT_ENV, raw)
    assert config.root() == expected


@pytest.mark.parametrize("raw", ["not-a-number", "0", "-5", ""])
def test_bad_byte_limits_fall_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(config.MAX_READ_BYTES_ENV, raw)
    assert config.max_read_bytes() == config.DEFAULT_MAX_READ_BYTES


def test_byte_limits_are_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.MAX_READ_BYTES_ENV, "2048")
    monkeypatch.setenv(config.MAX_DOWNLOAD_BYTES_ENV, "4096")
    assert config.max_read_bytes() == 2048
    assert config.max_download_bytes() == 4096


@pytest.mark.parametrize(("raw", "expected"), [("", 30.0), ("bad", 30.0), ("-1", 30.0), ("5", 5.0)])
def test_timeout(monkeypatch: pytest.MonkeyPatch, raw: str, expected: float) -> None:
    monkeypatch.setenv(config.TIMEOUT_ENV, raw)
    assert config.timeout() == expected


def test_unset_actions_means_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ACTIONS_ENV, "   ")
    assert config.allowed_actions() == frozenset(config.ACTIONS)


def test_groups_expand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ACTIONS_ENV, "read")
    assert config.allowed_actions() == config.ACTION_GROUPS["read"]
    assert "delete" not in config.allowed_actions()


def test_groups_combine_with_individual_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ACTIONS_ENV, "read, write , publish")
    allowed = config.allowed_actions()
    assert allowed == config.ACTION_GROUPS["read"] | config.ACTION_GROUPS["write"] | {"publish"}


def test_full_tool_names_from_the_readme_table_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ACTIONS_ENV, "yadisk_list,yadisk-read-file")
    assert config.allowed_actions() == {"list", "read_file"}


def test_a_typo_can_only_withhold_a_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ACTIONS_ENV, "raed,delet,rm -rf")
    assert config.allowed_actions() == frozenset()


def test_every_action_belongs_to_exactly_one_group_beside_all() -> None:
    groups = {k: v for k, v in config.ACTION_GROUPS.items() if k != "all"}
    covered: set[str] = set()
    for members in groups.values():
        assert not covered & members, "an action is in two groups"
        covered |= members
    assert covered == set(config.ACTIONS)

"""Registration, the schemas' wire contract, version sync, and the load path.

No fake disk here: these tests exercise the plugin as Hermes sees it, including
the code path where no credentials are configured at all.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import hermes_yandex_disk
from hermes_yandex_disk import config, schemas, tools

from .conftest import payload

PACKAGE_DIR = Path(hermes_yandex_disk.__file__).parent


class FakeCtx:
    """Stand-in for Hermes' PluginContext, recording what a plugin registers."""

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)

    @property
    def names(self) -> list[str]:
        return [t["name"] for t in self.tools]


def _register(monkeypatch: pytest.MonkeyPatch, *, search: bool = False) -> FakeCtx:
    """Register with the search capability stubbed, so no probe hits the network."""
    monkeypatch.setattr(config, "search_supported", lambda: search)
    ctx = FakeCtx()
    hermes_yandex_disk.register(ctx)
    return ctx


def test_every_action_registers_when_search_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _register(monkeypatch, search=True)
    assert len(ctx.names) == len(config.ACTIONS)
    assert ctx.names[0] == "yadisk_disk_info"
    assert "yadisk_search" in ctx.names


def test_search_is_hidden_when_the_token_cannot_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _register(monkeypatch, search=False)
    assert "yadisk_search" not in ctx.names
    assert len(ctx.names) == len(config.ACTIONS) - 1


def test_search_needs_both_the_action_and_the_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ACTIONS_ENV, "read")
    assert "yadisk_search" in _register(monkeypatch, search=True).names
    assert "yadisk_search" not in _register(monkeypatch, search=False).names
    # And the capability alone does not smuggle it past the allow-list.
    monkeypatch.setenv(config.ACTIONS_ENV, "write")
    assert "yadisk_search" not in _register(monkeypatch, search=True).names


def test_the_manifest_lists_every_tool_except_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (PACKAGE_DIR / "plugin.yaml").read_text(encoding="utf-8")
    declared = [
        line.split("- ", 1)[1].strip()
        for line in manifest.splitlines()
        if line.startswith("  - yadisk_")
    ]
    # Search is deliberately unmanifested: it is discovered only when the token
    # supports it, so with no capability the registered set equals the manifest.
    assert declared == _register(monkeypatch, search=False).names
    assert "yadisk_search" not in declared
    assert "yadisk_search" in _register(monkeypatch, search=True).names


def test_read_only_deployments_never_see_a_mutating_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ACTIONS_ENV, "read")
    names = _register(monkeypatch, search=True).names
    assert names == [
        "yadisk_disk_info",
        "yadisk_list",
        "yadisk_search",
        "yadisk_read_file",
        "yadisk_trash_list",
    ]


def test_a_value_naming_nothing_valid_registers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ACTIONS_ENV, "nonsense")
    assert _register(monkeypatch, search=True).names == []


def test_registration_wires_the_credential_check(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _register(monkeypatch).tools[0]
    assert entry["toolset"] == tools.TOOLSET
    assert entry["requires_env"] == [config.TOKEN_ENV]
    assert entry["check_fn"]()
    monkeypatch.delenv(config.TOKEN_ENV)
    assert not entry["check_fn"]()


def test_registered_handlers_are_the_ones_the_schema_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for entry in _register(monkeypatch, search=True).tools:
        assert entry["schema"]["name"] == entry["name"]
        assert callable(entry["handler"])
        assert entry["description"] and entry["emoji"]


# -- the schemas ----------------------------------------------------------

_SCHEMAS = [
    value
    for name, value in vars(schemas).items()
    if name.isupper() and isinstance(value, dict) and "parameters" in value
]


@pytest.mark.parametrize("schema", _SCHEMAS, ids=lambda s: s["name"])
def test_schema_shape(schema: dict[str, Any]) -> None:
    assert schema["name"].startswith("yadisk_")
    assert len(schema["description"]) > 40
    params = schema["parameters"]
    assert params["type"] == "object"
    assert set(params["required"]) <= set(params["properties"])
    for prop in params["properties"].values():
        # Union types are legal JSON Schema but strict validators reject them.
        assert isinstance(prop["type"], str)
        assert prop["description"]


@pytest.mark.parametrize("schema", _SCHEMAS, ids=lambda s: s["name"])
def test_schema_is_json_serialisable(schema: dict[str, Any]) -> None:
    assert json.loads(json.dumps(schema)) == schema


# -- the never-raise contract, with nothing configured --------------------


@pytest.mark.parametrize(
    "handler",
    [handler for _action, _schema, handler, _desc, _emoji in hermes_yandex_disk._TOOLS],
    ids=[schema["name"] for _action, schema, _handler, _desc, _emoji in hermes_yandex_disk._TOOLS],
)
def test_no_handler_raises_when_the_token_is_missing(
    monkeypatch: pytest.MonkeyPatch, handler: Any
) -> None:
    monkeypatch.delenv(config.TOKEN_ENV, raising=False)
    monkeypatch.delenv(config.TOKEN_ENV_ALIAS, raising=False)
    result = payload(handler({}))
    assert config.TOKEN_ENV in result["error"]


# -- version sync ---------------------------------------------------------


def _pyproject_version() -> str:
    import tomllib

    data = tomllib.loads((PACKAGE_DIR.parent / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _manifest_version() -> str:
    for line in (PACKAGE_DIR / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise AssertionError("plugin.yaml has no version")


def test_all_three_version_files_agree() -> None:
    assert hermes_yandex_disk.__version__ == _pyproject_version() == _manifest_version()


# -- the Hermes directory-plugin load path --------------------------------


def test_loads_the_way_hermes_loads_a_directory_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate Hermes' loader: relative imports and register() must both work."""
    namespace = "hermes_plugins"
    sys.modules.setdefault(namespace, types.ModuleType(namespace)).__path__ = []  # type: ignore[attr-defined]
    module_name = f"{namespace}.yandex_disk"
    spec = importlib.util.spec_from_file_location(
        module_name,
        PACKAGE_DIR / "__init__.py",
        submodule_search_locations=[str(PACKAGE_DIR)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(PACKAGE_DIR)]  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        # The freshly loaded package has its own config submodule; stub the
        # capability there so the load path does not probe the network.
        monkeypatch.setattr(module.config, "search_supported", lambda: True)
        ctx = FakeCtx()
        module.register(ctx)
        assert len(ctx.names) == len(config.ACTIONS)
    finally:
        for name in [k for k in sys.modules if k.startswith(module_name)]:
            del sys.modules[name]

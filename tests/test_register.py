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


def _register() -> FakeCtx:
    ctx = FakeCtx()
    hermes_yandex_disk.register(ctx)
    return ctx


def test_every_action_is_registered_by_default() -> None:
    ctx = _register()
    assert len(ctx.names) == len(config.ACTIONS)
    assert ctx.names[0] == "yadisk_disk_info"


def test_the_manifest_lists_exactly_the_registered_tools() -> None:
    manifest = (PACKAGE_DIR / "plugin.yaml").read_text(encoding="utf-8")
    declared = [
        line.split("- ", 1)[1].strip()
        for line in manifest.splitlines()
        if line.startswith("  - yadisk_")
    ]
    assert declared == _register().names


def test_read_only_deployments_never_see_a_mutating_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ACTIONS_ENV, "read")
    names = _register().names
    assert names == [
        "yadisk_disk_info",
        "yadisk_list",
        "yadisk_read_file",
        "yadisk_trash_list",
    ]


def test_a_value_naming_nothing_valid_registers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ACTIONS_ENV, "nonsense")
    assert _register().names == []


def test_registration_wires_the_credential_check(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _register().tools[0]
    assert entry["toolset"] == tools.TOOLSET
    assert entry["requires_env"] == [config.TOKEN_ENV]
    assert entry["check_fn"]()
    monkeypatch.delenv(config.TOKEN_ENV)
    assert not entry["check_fn"]()


def test_registered_handlers_are_the_ones_the_schema_names() -> None:
    for entry in _register().tools:
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


@pytest.mark.parametrize("entry", _register().tools, ids=lambda e: e["name"])
def test_no_handler_raises_when_the_token_is_missing(
    monkeypatch: pytest.MonkeyPatch, entry: dict[str, Any]
) -> None:
    monkeypatch.delenv(config.TOKEN_ENV, raising=False)
    monkeypatch.delenv(config.TOKEN_ENV_ALIAS, raising=False)
    result = payload(entry["handler"]({}))
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


def test_loads_the_way_hermes_loads_a_directory_plugin() -> None:
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
        ctx = FakeCtx()
        module.register(ctx)
        assert len(ctx.names) == len(config.ACTIONS)
    finally:
        for name in [k for k in sys.modules if k.startswith(module_name)]:
            del sys.modules[name]

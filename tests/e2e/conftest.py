"""Live end-to-end fixtures.

Credentials come from the environment first (that is how CI supplies them) and
otherwise from ``~/.yandex-disk-*`` files, so a local run needs nothing exported.

Every test works inside one throwaway folder named after the run and deletes it
permanently in teardown, so a failed assertion still leaves the account clean.
Use a disposable Yandex account, never a personal one.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from hermes_yandex_disk import tools
from hermes_yandex_disk.config import ACTIONS_ENV, ROOT_ENV, TOKEN_ENV, token

#: env var -> file the value falls back to when the variable is unset.
CREDENTIAL_FILES = {
    TOKEN_ENV: "~/.yandex-disk-oauth",
    "YANDEX_DISK_E2E_LOGIN": "~/.yandex-disk-login",
}

E2E_PREFIX = "hermes-e2e"


def pytest_configure(config: pytest.Config) -> None:
    """Fill unset credentials from the local dotfiles before collection."""
    for variable, location in CREDENTIAL_FILES.items():
        if os.environ.get(variable):
            continue
        path = Path(location).expanduser()
        if path.is_file():
            os.environ[variable] = path.read_text(encoding="utf-8").strip()


@pytest.fixture(scope="session", autouse=True)
def require_credentials() -> None:
    if not token():
        pytest.skip(f"{TOKEN_ENV} is not set and ~/.yandex-disk-oauth is absent")


@pytest.fixture
def no_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tools address the whole disk unless a test opts into a sandbox."""
    monkeypatch.delenv(ROOT_ENV, raising=False)
    monkeypatch.delenv(ACTIONS_ENV, raising=False)


@pytest.fixture
def workspace(no_sandbox: None) -> Iterator[str]:
    """A folder unique to this run, destroyed afterwards whatever the outcome."""
    path = f"/{E2E_PREFIX}-{uuid.uuid4().hex[:8]}"
    tools.handle_mkdir({"path": path})
    try:
        yield path
    finally:
        # A test may have left a sandbox or an allow-list in place; the cleanup
        # addresses the whole disk regardless, or the folder would survive the run.
        saved = {name: os.environ.pop(name, None) for name in (ROOT_ENV, ACTIONS_ENV)}
        try:
            result = json.loads(tools.handle_delete({"path": path, "permanently": True}))
            assert "error" not in result, f"e2e workspace {path} left behind: {result}"
        finally:
            os.environ.update({k: v for k, v in saved.items() if v is not None})

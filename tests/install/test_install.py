"""Install the plugin into a real Hermes, every way README.md says to.

Nothing here is simulated. Each test lays out a fresh home directory the way
the official Hermes installer does — the Hermes checkout and its virtualenv
under ``~/.hermes/hermes-agent``, Hermes' own ``uv`` at ``~/.hermes/bin/uv``,
the ``hermes`` launcher in ``~/.local/bin`` — and runs the commands README.md
gives, as written, with ``HOME`` pointing there. Success is whatever Hermes
reports afterwards: ``hermes plugins list``, and the plugins and tools its own
plugin manager hands the agent (see ``probe.py``).

Where the tests depart from the README text, and why:

* the Git install adds ``--ref`` with the commit under test, because the README
  command installs whatever the default branch holds at the time;
* the PyPI install names the wheel about to be published instead of the
  project, so it cannot pick up the release already on PyPI;
* the drop-in archive is the one about to be attached to the release, and its
  ``<version>`` placeholder is filled in.

Deselected by default. The ``Install check`` workflow runs it before every
release, against the latest Hermes release and against Hermes ``main``, which
is what the installer checks out. To run it locally, build the artifacts the
way ``release-build.yml`` does, then point it at a Hermes checkout that has its
virtualenv in ``venv/``, at a ``uv`` binary, and at a commit GitHub has::

    HERMES_CHECKOUT=~/.hermes/hermes-agent UV="$(command -v uv)" \\
    INSTALL_DIST=dist INSTALL_REF="$(git rev-parse HEAD)" \\
    python -m pytest -m install
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "version"
]

PLUGIN = "yandex-disk"
TOOLSET = "yandex_disk"
PROJECT = "hermes-yandex-disk"
REQUIRED_ENV = ("YANDEX_DISK_OAUTH_TOKEN",)

#: What an install gives the agent before YANDEX_DISK_ACTIONS says otherwise:
#: every tool in the manifest. ``yadisk_search`` is not among them: the plugin
#: offers it only after a live probe shows the token may search, and a token
#: that is a placeholder may not. That probe is the one request these tests
#: make to Yandex — loading the plugin sends it, with the placeholder token, and
#: a refusal and an unreachable API both leave search hidden.
DEFAULT_TOOLS = {
    "yadisk_disk_info",
    "yadisk_list",
    "yadisk_read_file",
    "yadisk_trash_list",
    "yadisk_download",
    "yadisk_mkdir",
    "yadisk_write_file",
    "yadisk_upload",
    "yadisk_copy",
    "yadisk_move",
    "yadisk_trash_restore",
    "yadisk_publish",
    "yadisk_delete",
    "yadisk_trash_empty",
}

# The commands README.md gives. test_readme_gives_the_commands_under_test keeps
# the two in step, so the tests below cannot drift into checking something the
# README does not say.
GIT_INSTALL = "hermes plugins install akinfold/hermes-yandex-disk/hermes_yandex_disk --enable"
PYPI_INSTALL = (
    "~/.hermes/bin/uv pip install --python ~/.hermes/hermes-agent/venv/bin/python"
    " hermes-yandex-disk"
)
DROPIN_INSTALL = "unzip hermes-yandex-disk-plugin-<version>.zip -d ~/.hermes/plugins/"
ENABLE = "hermes plugins enable yandex-disk"
ADD_CREDENTIALS = "echo 'YANDEX_DISK_OAUTH_TOKEN=y0_your_token_here' >> ~/.hermes/.env"

#: Not a README command: the mistake README.md warns about, pointing Hermes at
#: the repository root instead of the plugin directory.
ROOT_INSTALL = GIT_INSTALL.replace("/hermes_yandex_disk ", " ")

#: Answers typed at the credential prompt of the Git install. No test calls a
#: tool; see DEFAULT_TOOLS for the one request loading the plugin makes.
PROMPT_ANSWERS = {"YANDEX_DISK_OAUTH_TOKEN": "install-check-not-a-token"}

install = pytest.mark.install

# Variables that would let the developer's own setup leak into the sandbox.
_LEAKY_ENV = re.compile(r"^(YANDEX_|HERMES_|VIRTUAL_ENV$|CONDA_|PYTHONPATH$|PYTHONHOME$)")


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.fail(f"{name} is not set; see the docstring of {Path(__file__).name}")
    return value


def _one(pattern: str) -> Path:
    found = sorted(Path(_required("INSTALL_DIST")).resolve().glob(pattern))
    assert len(found) == 1, f"expected exactly one {pattern} in INSTALL_DIST, found {found}"
    return found[0]


@dataclass
class Home:
    """A home directory laid out like the official Hermes installer's."""

    path: Path
    env: dict[str, str]

    @property
    def hermes_home(self) -> Path:
        return self.path / ".hermes"

    def run(self, command: str, *, answers: str = "", check: bool = True) -> str:
        """Run a shell command as the user would; fail on a non-zero exit if *check*."""
        result = subprocess.run(
            ["bash", "-c", command],
            input=answers,
            env=self.env,
            cwd=self.path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=600,
            check=False,
        )
        if check:
            assert result.returncode == 0, (
                f"`{command}` exited {result.returncode}:\n{result.stdout}"
            )
        return result.stdout

    def listed(self) -> list[dict]:
        """Rows for this plugin in ``hermes plugins list``."""
        out = self.run("hermes plugins list --json --no-bundled")
        rows = json.loads(out[out.index("[") :])
        return [row for row in rows if row["name"] == PLUGIN]

    def probe(self) -> dict:
        """What Hermes' own plugin manager loaded; see probe.py."""
        python = self.hermes_home / "hermes-agent" / "venv" / "bin" / "python"
        out = self.run(f"'{python}' '{Path(__file__).with_name('probe.py')}'")
        line = next(line for line in reversed(out.splitlines()) if line.startswith("PROBE "))
        report = json.loads(line.removeprefix("PROBE "))
        assert "probe_error" not in report, (
            "probe.py could not read Hermes' state — has Hermes changed its internals?\n"
            + report["probe_error"]
        )
        return report

    def dotenv(self) -> dict[str, str]:
        path = self.hermes_home / ".env"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        return dict(line.split("=", 1) for line in lines if "=" in line and line[0] != "#")


@pytest.fixture
def home(tmp_path: Path) -> Home:
    checkout = Path(_required("HERMES_CHECKOUT")).expanduser().resolve()
    uv = Path(_required("UV")).expanduser().resolve()
    root = tmp_path / "home"
    (root / ".hermes" / "bin").mkdir(parents=True)
    (root / ".hermes" / "bin" / "uv").symlink_to(uv)
    (root / ".hermes" / "hermes-agent").symlink_to(checkout)
    (root / ".local" / "bin").mkdir(parents=True)
    (root / ".local" / "bin" / "hermes").symlink_to(checkout / "venv" / "bin" / "hermes")

    env = {key: value for key, value in os.environ.items() if not _LEAKY_ENV.match(key)}
    env.setdefault("UV_CACHE_DIR", str(Path.home() / ".cache" / "uv"))
    env.update(
        HOME=str(root),
        HERMES_HOME=str(root / ".hermes"),
        PATH=os.pathsep.join([str(root / ".local" / "bin"), env.get("PATH", "")]),
        COLUMNS="500",
        NO_COLOR="1",
    )
    home = Home(root, env)
    assert home.listed() == [], f"{PLUGIN} is already visible to this Hermes before the install"
    return home


def _flat(text: str) -> str:
    return " ".join(text.split())


def _assert_loaded(home: Home, *, source: str) -> None:
    """Hermes lists the plugin as enabled, loads it, and gives the agent its tools."""
    assert [row["status"] for row in home.listed()] == ["enabled"], home.listed()

    report = home.probe()
    loaded = [plugin for plugin in report["plugins"] if plugin["name"] == PLUGIN]
    assert len(loaded) == 1, report["plugins"]
    plugin = loaded[0]
    assert plugin["error"] is None, plugin["error"]
    assert plugin["enabled"], plugin
    assert plugin["source"] == source, plugin
    assert plugin["version"] == VERSION, plugin
    assert plugin["tools"] == len(DEFAULT_TOOLS), plugin

    mine = {name: toolset for name, toolset in report["tools"].items() if toolset == TOOLSET}
    assert set(mine) == DEFAULT_TOOLS, sorted(report["tools"])


def test_readme_gives_the_commands_under_test() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for command in (GIT_INSTALL, PYPI_INSTALL, DROPIN_INSTALL, ENABLE, ADD_CREDENTIALS):
        assert command in readme, f"README.md no longer says:\n{command}"
    assert f"~/.hermes/plugins/{PLUGIN}/plugin.yaml" in readme


@install
def test_git_install_asks_for_credentials_and_loads(home: Home) -> None:
    answers = "".join(PROMPT_ANSWERS[name] + "\n" for name in REQUIRED_ENV)
    out = _flat(home.run(f"{GIT_INSTALL} --ref {_required('INSTALL_REF')}", answers=answers))

    assert "may not be a valid Hermes plugin" not in out, out
    for name in REQUIRED_ENV:
        assert f"{name}:" in out, f"the install never asked for {name}:\n{out}"
    assert home.dotenv() == PROMPT_ANSWERS
    _assert_loaded(home, source="user")


@install
def test_pypi_install_loads(home: Home) -> None:
    wheel = _one("*.whl")
    try:
        home.run(PYPI_INSTALL.removesuffix(PROJECT) + str(wheel))
        home.run(ENABLE)
        home.run(ADD_CREDENTIALS)
        _assert_loaded(home, source="entrypoint")
    finally:
        # One virtualenv serves every test in a local run; leave it as found.
        home.run(
            "~/.hermes/bin/uv pip uninstall --python ~/.hermes/hermes-agent/venv/bin/python "
            + PROJECT,
            check=False,
        )


@install
def test_dropin_archive_loads(home: Home) -> None:
    archive = _one(f"{PROJECT}-plugin-*.zip")
    assert archive.name == f"{PROJECT}-plugin-{VERSION}.zip", archive.name
    shutil.copy(archive, home.path / archive.name)

    home.run(DROPIN_INSTALL.replace("<version>", VERSION))
    assert (home.hermes_home / "plugins" / PLUGIN / "plugin.yaml").is_file()
    home.run(ENABLE)
    home.run(ADD_CREDENTIALS)
    _assert_loaded(home, source="user")


@install
def test_repository_root_install_shows_the_documented_symptom(home: Home) -> None:
    """README.md: it looks like success, asks for nothing, and lists as not enabled."""
    out = _flat(home.run(f"{ROOT_INSTALL} --ref {_required('INSTALL_REF')}"))

    assert "may not be a valid Hermes plugin" in out, out
    for name in REQUIRED_ENV:
        assert name not in out, out
    assert [row["status"] for row in home.listed()] == ["not enabled"], home.listed()

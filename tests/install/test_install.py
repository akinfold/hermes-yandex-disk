"""Install the plugin into a real Hermes, every way README.md says to.

Nothing here is simulated. The tests run against a Hermes that its official
installer set up in this account — the ``setup-hermes`` action does that in CI —
and run the commands README.md gives, as written. Success is whatever Hermes
reports afterwards: ``hermes plugins list``, and the plugins and tools its own
plugin manager hands the agent (see ``probe.py``, run in Hermes' own Python,
which ``hermes_env.py`` finds).

Where the tests depart from the README text, and why:

* the Git install adds ``--ref`` with the commit under test, because the README
  command installs whatever the default branch holds at the time;
* the PyPI install names the wheel about to be published instead of the
  project, so it cannot pick up the release already on PyPI;
* the drop-in archive is the one about to be attached to the release, and its
  ``<version>`` placeholder is filled in;
* before an upgrade, the installed copy's manifest is set to version 0.0.0, so
  the version check afterwards proves the upgrade really replaced the files.

The PyPI route applies only to a Hermes in the older layout (its virtualenv in
``~/.hermes/hermes-agent/venv``), the only one README.md gives a PyPI command
for. On Hermes ``main``, which pm builds, it is skipped. On the latest release it
must run: once a release ships the pm layout, the test fails there rather than
skipping, because README.md then needs changing.

Each test changes the Hermes it runs against, then puts back what it changed:
the plugin directories, ``config.yaml``, ``.env``, and a package installed from
PyPI. They still install into a real ``~/.hermes``, so they refuse to run unless
``INSTALL_CHECK_DISPOSABLE_HOME=1`` says that Hermes is a throwaway one.

Deselected by default. The ``Install check`` workflow runs them before every
release, against the latest Hermes release and against Hermes ``main``. To run
them yourself, do it in a container or VM with Hermes installed by its
installer, after building the artifacts the way ``release-build.yml`` does::

    INSTALL_CHECK_DISPOSABLE_HOME=1 INSTALL_DIST=dist \\
    INSTALL_REF="$(git rev-parse HEAD)" python -m pytest -m install
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

from .hermes_env import hermes_python, is_pm_install

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "version"
]

PLUGIN = "yandex-disk"
TOOLSET = "yandex_disk"
PROJECT = "hermes-yandex-disk"
REQUIRED_ENV = ("YANDEX_DISK_OAUTH_TOKEN",)
#: Every variable the plugin reads its token from: the manifest's, and the alias
#: config.py also accepts.
TOKEN_ENV = (*REQUIRED_ENV, "YANDEX_DISK_API_KEY")

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
GIT_UPDATE = "hermes plugins update yandex-disk"
GIT_UPGRADE = GIT_INSTALL + " --force"
#: For a Hermes in the older layout only; see README.md, B.
PYPI_INSTALL = (
    "~/.hermes/bin/uv pip install --python ~/.hermes/hermes-agent/venv/bin/python"
    " hermes-yandex-disk"
)
DROPIN_INSTALL = "unzip hermes-yandex-disk-plugin-<version>.zip -d ~/.hermes/plugins/"
DROPIN_UPGRADE = DROPIN_INSTALL.replace("unzip ", "unzip -o ")
ENABLE = "hermes plugins enable yandex-disk"
ADD_CREDENTIALS = "echo 'YANDEX_DISK_OAUTH_TOKEN=y0_your_token_here' >> ~/.hermes/.env"

#: Not a README command: the mistake README.md warns about, pointing Hermes at
#: the repository root instead of the plugin directory.
ROOT_INSTALL = GIT_INSTALL.replace("/hermes_yandex_disk ", " ")

#: Answers typed at the credential prompt of the Git install. No test calls a
#: tool; see DEFAULT_TOOLS for the one request loading the plugin makes.
PROMPT_ANSWERS = {"YANDEX_DISK_OAUTH_TOKEN": "install-check-not-a-token"}

install = pytest.mark.install

# Variables that would let the developer's own setup leak into the commands.
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
    """The account the official Hermes installer set Hermes up in."""

    path: Path
    env: dict[str, str]

    @property
    def hermes_home(self) -> Path:
        return self.path / ".hermes"

    def run(
        self, command: str, *, answers: str = "", check: bool = True, cwd: Path | None = None
    ) -> str:
        """Run a shell command as the user would; fail on a non-zero exit if *check*."""
        result = subprocess.run(
            ["bash", "-c", command],
            input=answers,
            env=self.env,
            cwd=cwd or self.path,
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
        python = hermes_python(self.hermes_home)
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

    def credentials(self) -> dict[str, str]:
        """This plugin's variables in ``~/.hermes/.env``; the installer writes its own there too."""
        return {key: value for key, value in self.dotenv().items() if key in TOKEN_ENV}


class _Snapshot:
    """The parts of ``~/.hermes`` an install changes, to put back afterwards."""

    def __init__(self, hermes_home: Path, keep: Path) -> None:
        self.hermes_home = hermes_home
        self.plugins = hermes_home / "plugins"
        self.saved_plugins = keep / "plugins"
        if self.plugins.is_dir():
            shutil.copytree(self.plugins, self.saved_plugins, symlinks=True)
        self.files = {
            name: (hermes_home / name).read_bytes() if (hermes_home / name).exists() else None
            for name in ("config.yaml", ".env")
        }

    def restore(self) -> None:
        if self.plugins.is_dir():
            shutil.rmtree(self.plugins)
        if self.saved_plugins.is_dir():
            shutil.copytree(self.saved_plugins, self.plugins, symlinks=True)
        for name, content in self.files.items():
            path = self.hermes_home / name
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)


@pytest.fixture
def home(tmp_path: Path):
    if os.environ.get("INSTALL_CHECK_DISPOSABLE_HOME") != "1":
        pytest.fail(
            "these tests install into the real ~/.hermes; set INSTALL_CHECK_DISPOSABLE_HOME=1 "
            "only where that Hermes is a throwaway one (see the module docstring)"
        )
    root = Path.home()
    env = {key: value for key, value in os.environ.items() if not _LEAKY_ENV.match(key)}
    env.update(
        PATH=os.pathsep.join([str(root / ".local" / "bin"), env.get("PATH", "")]),
        COLUMNS="500",
        NO_COLOR="1",
    )
    home = Home(root, env)
    assert home.listed() == [], f"{PLUGIN} is already visible to this Hermes before the install"
    assert home.credentials() == {}, "a Yandex Disk token is already in ~/.hermes/.env"
    snapshot = _Snapshot(home.hermes_home, tmp_path)
    try:
        yield home
    finally:
        snapshot.restore()


def _flat(text: str) -> str:
    return " ".join(text.split())


def _make_stale(home: Home) -> None:
    """Mark the installed copy as version 0.0.0, so only a real upgrade passes the version check."""
    manifest = home.hermes_home / "plugins" / PLUGIN / "plugin.yaml"
    text = manifest.read_text(encoding="utf-8")
    stale = re.sub(r"(?m)^version:.*$", "version: 0.0.0", text, count=1)
    assert stale != text, "the installed manifest has no version line"
    manifest.write_text(stale, encoding="utf-8")


def _assert_loaded(home: Home, *, source: str, version: str | None = VERSION) -> None:
    """Hermes lists the plugin as enabled, loads it, and gives the agent its tools."""
    assert [row["status"] for row in home.listed()] == ["enabled"], home.listed()

    report = home.probe()
    loaded = [plugin for plugin in report["plugins"] if plugin["name"] == PLUGIN]
    assert len(loaded) == 1, report["plugins"]
    plugin = loaded[0]
    assert plugin["error"] is None, plugin["error"]
    assert plugin["enabled"], plugin
    assert plugin["source"] == source, plugin
    if version is None:
        return
    assert plugin["version"] == version, plugin
    assert plugin["tools"] == len(DEFAULT_TOOLS), plugin

    mine = {name: toolset for name, toolset in report["tools"].items() if toolset == TOOLSET}
    assert set(mine) == DEFAULT_TOOLS, sorted(report["tools"])


def test_readme_gives_the_commands_under_test() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for command in (
        GIT_INSTALL,
        GIT_UPDATE,
        GIT_UPGRADE,
        PYPI_INSTALL,
        DROPIN_INSTALL,
        DROPIN_UPGRADE,
        ENABLE,
        ADD_CREDENTIALS,
    ):
        assert command in readme, f"README.md no longer says:\n{command}"
    assert f"~/.hermes/plugins/{PLUGIN}/plugin.yaml" in readme
    assert f"remove `~/.hermes/plugins/{PROJECT}`" in readme


@install
def test_git_install_asks_for_credentials_loads_and_upgrades(home: Home) -> None:
    ref = f" --ref {_required('INSTALL_REF')}"
    answers = "".join(PROMPT_ANSWERS[name] + "\n" for name in REQUIRED_ENV)
    out = _flat(home.run(GIT_INSTALL + ref, answers=answers))

    assert "may not be a valid Hermes plugin" not in out, out
    for name in REQUIRED_ENV:
        assert f"{name}:" in out, f"the install never asked for {name}:\n{out}"
    assert home.credentials() == PROMPT_ANSWERS
    _assert_loaded(home, source="user")

    # README.md's upgrade: the same command with --force. The token is already
    # there, so nothing is asked again.
    _make_stale(home)
    out = _flat(home.run(GIT_UPGRADE + ref))
    for name in REQUIRED_ENV:
        assert f"{name}:" not in out, out
    assert home.credentials() == PROMPT_ANSWERS
    _assert_loaded(home, source="user")


@install
def test_git_install_updates_with_hermes_plugins_update(home: Home) -> None:
    """README.md's usual upgrade for A: Hermes' own ``plugins update``.

    It installs from the default branch, not the commit under test, so this
    checks that the command upgrades this plugin's kind of install, not what it
    installs: after it, the manifest marked 0.0.0 must be gone.
    """
    answers = "".join(PROMPT_ANSWERS[name] + "\n" for name in REQUIRED_ENV)
    home.run(GIT_INSTALL, answers=answers)
    _make_stale(home)
    home.run(GIT_UPDATE)
    [plugin] = [plugin for plugin in home.probe()["plugins"] if plugin["name"] == PLUGIN]
    assert plugin["version"] != "0.0.0", f"`{GIT_UPDATE}` replaced nothing: {plugin}"
    _assert_loaded(home, source="user", version=None)


@install
def test_pypi_install_loads(home: Home) -> None:
    if is_pm_install(home.hermes_home):
        if os.environ.get("INSTALL_CHECK_CHANNEL") == "release":
            pytest.fail(
                "the latest Hermes release runs from pm-built environments: README.md's "
                "PyPI command (B) no longer applies to it, so change README.md and this test"
            )
        pytest.skip(
            "this Hermes runs from environments its package manager builds; README.md gives "
            "the PyPI command only for a Hermes in the older layout"
        )
    wheel = _one("*.whl")
    try:
        home.run(PYPI_INSTALL.removesuffix(PROJECT) + str(wheel))
        home.run(ENABLE)
        home.run(ADD_CREDENTIALS)
        _assert_loaded(home, source="entrypoint")
    finally:
        home.run(
            "~/.hermes/bin/uv pip uninstall --python ~/.hermes/hermes-agent/venv/bin/python "
            + PROJECT,
            check=False,
        )


@install
def test_dropin_archive_loads_and_upgrades(home: Home, tmp_path: Path) -> None:
    archive = _one(f"{PROJECT}-plugin-*.zip")
    assert archive.name == f"{PROJECT}-plugin-{VERSION}.zip", archive.name
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    shutil.copy(archive, downloads / archive.name)

    home.run(DROPIN_INSTALL.replace("<version>", VERSION), cwd=downloads)
    assert (home.hermes_home / "plugins" / PLUGIN / "plugin.yaml").is_file()
    home.run(ENABLE)
    home.run(ADD_CREDENTIALS)
    _assert_loaded(home, source="user")

    # README.md's upgrade: unzip the new archive over the old one.
    _make_stale(home)
    home.run(DROPIN_UPGRADE.replace("<version>", VERSION), cwd=downloads)
    _assert_loaded(home, source="user")


@install
def test_repository_root_install_shows_the_documented_symptom(home: Home) -> None:
    """README.md: a warning, no questions, the plugin not enabled, and the directory to remove."""
    out = _flat(home.run(f"{ROOT_INSTALL} --ref {_required('INSTALL_REF')}", check=False))

    assert "may not be a valid Hermes plugin" in out, out
    for name in REQUIRED_ENV:
        assert name not in out, out
    assert [row["status"] for row in home.listed()] == ["not enabled"], home.listed()
    # Hermes names the copy after the repository; README.md says to remove it.
    plugins = home.hermes_home / "plugins"
    assert (plugins / PROJECT).is_dir(), sorted(path.name for path in plugins.iterdir())

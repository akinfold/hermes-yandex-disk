"""The shipped tree must survive Hermes' install-time security scan.

``hermes plugins install`` scans a plugin before it ever runs, and a *critical*
finding is a hard block: ``--force`` does not override a dangerous verdict, and
``hermes plugins update`` disables an already-installed plugin that starts
producing one. Runtime code is where findings keep that severity — docs and the
test tree are demoted — so the guard here reads the package, not the repository.

The pattern below is Hermes' own (``tools/threat_patterns.py``, ``hardcoded_secret``).
It cannot tell a constant that *names* a credential variable from one that *holds*
a credential: ``token = "YANDEX_DISK_OAUTH_TOKEN"`` matches it exactly, and a
sibling plugin was made uninstallable by precisely that line. This package escaped
only because its constants happened to be spelled ``TOKEN_ENV``; the names are now
composed from a prefix, and this test keeps a later edit from writing the block
back in.
"""

from __future__ import annotations

import re
from pathlib import Path

from hermes_yandex_disk import config

PACKAGE = Path(__file__).resolve().parent.parent / "hermes_yandex_disk"

#: Verbatim from Hermes' ``hardcoded_secret`` rule, matched case-insensitively.
HARDCODED_SECRET = re.compile(
    r'(?:api[_-]?key|token|secret|password)\s*[=:]\s*["\'][A-Za-z0-9+/=_-]{20,}',
    re.IGNORECASE,
)


def test_no_runtime_line_looks_like_a_hardcoded_secret() -> None:
    offenders = [
        f"{path.relative_to(PACKAGE.parent)}:{number}: {line.strip()}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if HARDCODED_SECRET.search(line)
    ]
    assert not offenders, (
        "These lines match Hermes' hardcoded_secret rule and would make the plugin "
        "uninstallable (a dangerous verdict that --force cannot override):\n" + "\n".join(offenders)
    )


def test_the_pattern_really_catches_the_shape_it_guards_against() -> None:
    """A self-check: if the regex ever stopped matching, the guard above would be vacuous."""
    assert HARDCODED_SECRET.search('TOKEN = "YANDEX_DISK_OAUTH_TOKEN"')
    assert HARDCODED_SECRET.search("password: 'a-long-enough-value-here'")
    # Under the 20-character floor: not what the rule is after.
    assert not HARDCODED_SECRET.search('API_KEY = "short"')


def test_the_environment_variable_names_are_exact() -> None:
    """The names are composed from a prefix (see config), so pin what they spell."""
    assert config.TOKEN_ENV == "YANDEX_DISK_OAUTH_TOKEN"
    assert config.TOKEN_ENV_ALIAS == "YANDEX_DISK_API_KEY"
    assert config.ROOT_ENV == "YANDEX_DISK_ROOT"
    assert config.ACTIONS_ENV == "YANDEX_DISK_ACTIONS"
    assert config.BASE_URL_ENV == "YANDEX_DISK_BASE_URL"
    assert config.TIMEOUT_ENV == "YANDEX_DISK_TIMEOUT"
    assert config.MAX_READ_BYTES_ENV == "YANDEX_DISK_MAX_READ_BYTES"
    assert config.MAX_DOWNLOAD_BYTES_ENV == "YANDEX_DISK_MAX_DOWNLOAD_BYTES"

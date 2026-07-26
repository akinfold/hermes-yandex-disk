# Contributing

Thanks for taking the time. Bug reports, tool ideas and pull requests are all welcome.

**Everything in this repository is written in English** — code, comments, docstrings, docs,
commit messages, issues and pull requests. Test *data* may of course be non-ASCII; handling
Cyrillic file names correctly is part of the job.

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

## Project layout

```
hermes_yandex_disk/
  client.py     # Yandex Disk REST API v1 over httpx — NO Hermes imports
  paths.py      # path normalisation and the YANDEX_DISK_ROOT sandbox — pure logic
  schemas.py    # the tools' JSON schemas, kept apart from the handlers
  tools.py      # handlers: JSON in, JSON string out, never raising
  config.py     # env-driven credentials, limits and the action allow-list
  capabilities.py  # one-time probe of what the configured token may actually do
  _compat.py    # Hermes' get_provider_env when present, env-only shim otherwise
  __init__.py   # register(ctx) — the entry point; RELATIVE imports only
  plugin.yaml   # the manifest Hermes reads
tests/          # unit tests against an in-memory fake of the API (no network)
tests/e2e/      # live tests, marked `e2e`, deselected by default
```

## Ground rules

These are the ones that actually break things:

1. **`client.py` and `paths.py` import nothing from Hermes.** That is what makes them
   testable without the host installed, and what keeps the plugin usable as a library.
2. **A handler never raises.** It returns a JSON string on every path, including failure.
   `tools.tool_handler` provides that; if you add a handler, wrap it the same way and let it
   return a dict.
3. **Relative imports in `__init__.py`** (`from . import tools`). Hermes loads a directory
   plugin as `hermes_plugins.<slug>`, and an absolute import breaks that.
4. **Secrets come from `get_provider_env`**, never from `os.environ` directly, so they also
   resolve from `~/.hermes/.env`. Never log a token value.
5. **No union types in a tool schema.** `{"type": ["string", "object"]}` is valid JSON Schema
   but strict function-calling validators reject it. Advertise one shape; be lenient in the
   handler.
6. **Every path goes through `paths.resolve`.** That is the single place the sandbox is
   enforced; a handler that builds a path itself has a hole in it.
7. **Destructive steps go last, and default to recoverable.** Deletes go to the bin unless the
   caller asks otherwise; a move confirms the copy before removing the source.
8. **New tools need an entry in `config.ACTIONS`, a group in `config.ACTION_GROUPS`, a row in
   the `_TOOLS` table, a line in `plugin.yaml`, and a row in the README table.** A test
   asserts the manifest and the registration agree.
9. **Some tools are capability-gated.** A few Yandex Disk endpoints are granted per
   application, not per token scope, so whether a given token can call them is only knowable
   by trying. `capabilities.py` probes that once at load; `register()` then offers such a tool
   only when the probe succeeds, so a token that cannot use the endpoint never sees the tool.
   A capability-gated tool is intentionally left out of `plugin.yaml` and the README — it is
   discovered at runtime, not advertised. Gate on the capability, never on a hardcoded
   application identity.

## Checks before opening a PR

```bash
ruff check .
ruff format --check .
pytest --cov=hermes_yandex_disk --cov-report=term-missing --cov-fail-under=90
radon cc -s -n C hermes_yandex_disk    # must print nothing
bandit -q -r hermes_yandex_disk tests --skip B101 --severity-level medium
```

If the complexity gate fires, **split the function — do not raise the bar.** In practice the
fix is extracting an argument-marshalling or dispatch helper, which does not change behaviour.

The bandit gate exists because CodeFactor runs the same checks and files an issue for a
finding — but only once the code is already on `main`. Fix the finding rather than adding a
`# nosec`; reach for `# nosec B<id>` (not `# noqa`, which is ruff's) only when the flagged
construct is genuinely unavoidable, and say why on the same line.

## Running the live E2E tests

They hit a real account and really create, publish and delete files, so use a throwaway
Yandex account. Put the token in `~/.yandex-disk-oauth` and run `pytest -m e2e -v`; the suite
skips itself when the file and the environment variable are both absent. Each test works
inside a `hermes-e2e-<id>` folder that teardown removes permanently even when an assertion
fails — if you add a test, keep that property.

## Commits and pull requests

Small, focused commits with imperative subjects ("Add trash_restore tool", not "added stuff").
In the PR, say what changed and why, and tick the checklist in the template.

## Releasing

1. Bump the version in **three** files, which must agree: `pyproject.toml`,
   `hermes_yandex_disk/__init__.py`, `hermes_yandex_disk/plugin.yaml`. A unit test enforces it.
2. `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. `release-publish.yml` builds, creates the GitHub Release, and — if the repository variable
   `PUBLISH_TO_PYPI` is `true` — publishes to PyPI through Trusted Publishing. The `pypi`
   environment has a required reviewer, so the irreversible step waits for a human.
4. Verify what was published, not just what was built: install the version from PyPI into a
   throwaway venv and check `__version__`, the entry point, and that `register()` still works.

## Security

Please report anything security-sensitive privately — open a
[security advisory](https://github.com/akinfold/hermes-yandex-disk/security/advisories/new)
rather than a public issue. Never paste a token into an issue, a test, or a log line.

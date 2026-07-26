# hermes-yandex-disk

[![PyPI version](https://img.shields.io/pypi/v/hermes-yandex-disk.svg)](https://pypi.org/project/hermes-yandex-disk/)
[![CI](https://github.com/akinfold/hermes-yandex-disk/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/akinfold/hermes-yandex-disk/actions/workflows/ci.yml)
[![E2E (live)](https://github.com/akinfold/hermes-yandex-disk/actions/workflows/e2e.yml/badge.svg)](https://github.com/akinfold/hermes-yandex-disk/actions/workflows/e2e.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/akinfold/hermes-yandex-disk/badges/coverage.json&v=1)](https://github.com/akinfold/hermes-yandex-disk/actions/workflows/ci.yml)
[![CodeFactor](https://www.codefactor.io/repository/github/akinfold/hermes-yandex-disk/badge)](https://www.codefactor.io/repository/github/akinfold/hermes-yandex-disk)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Your Yandex Disk, handed to the agent.** It can walk the folders, read a file into the
conversation, write the answer back, and give you a share link — without you opening a
browser.

> "Read me the March invoice in /Documents/Accounting."
> "Save these meeting notes to /Notes/2026/ and give me a link."
> "What's eating my Disk space? Move the old videos into /Archive."
> "Restore that file I deleted this morning."

- 🔒 **`YANDEX_DISK_ROOT` sandboxes the agent to one folder** — every path is checked against
  it, including ones the model invents and ones it copies back from an earlier result.
- 🛟 **Nothing is overwritten or destroyed by accident** — writes, copies and moves refuse an
  existing destination unless you pass `overwrite`, deletes go to the bin by default, and the
  bin can be restored from.
- 🎚 **`YANDEX_DISK_ACTIONS` decides what the agent may do at all** — a tool outside the
  allow-list is never registered, so it cannot be called or talked into being called.
- 📂 **Fourteen tools over one credential** — browse, read, write, upload, download, copy,
  move, share and restore, all through the REST API.
- 🔑 **One OAuth token, nothing proxied** — the plugin talks to `cloud-api.yandex.net`
  directly; no third-party service sees your files or your token.

Tested against Hermes 0.19.x, Python 3.11–3.13.

## Quick start

```bash
# 1. Install the plugin into your Hermes environment
hermes plugins install akinfold/hermes-yandex-disk --enable

# 2. Add your token — issue one at https://yandex.ru/dev/disk/poligon/
#    (scopes: cloud_api:disk.info, cloud_api:disk.read, cloud_api:disk.write)
echo 'YANDEX_DISK_OAUTH_TOKEN=y0_your_token_here' >> ~/.hermes/.env
```

```yaml
# 3. ~/.hermes/config.yaml
plugins:
  enabled: [yandex-disk]
```

Restart Hermes and ask it: **"How much space is left on my Yandex Disk?"**

> Not ready to hand over write access? Add `YANDEX_DISK_ACTIONS=read` to `~/.hermes/.env` and
> the agent gets a read-only Disk. See [Choosing what the agent may do](#choosing-what-the-agent-may-do).

## The tools

| Tool | What it does |
|---|---|
| `yadisk_disk_info` | Quota, used and bin space, account login |
| `yadisk_list` | List a folder, or show one file's metadata |
| `yadisk_read_file` | Read a text file's contents into the conversation |
| `yadisk_trash_list` | What is in the bin, and where each item came from |
| `yadisk_download` | Save a file from the Disk to the local machine |
| `yadisk_mkdir` | Create a folder, parents included |
| `yadisk_write_file` | Create or replace a text file from the agent's own output |
| `yadisk_upload` | Upload a local file, or have Yandex fetch a public URL |
| `yadisk_copy` | Server-side copy — no bytes pass through your machine |
| `yadisk_move` | Move or rename |
| `yadisk_trash_restore` | Put something back where it was deleted from |
| `yadisk_publish` | Create or revoke a public share link |
| `yadisk_delete` | Delete to the bin, or permanently with `permanently=true` |
| `yadisk_trash_empty` | Destroy one bin item, or the whole bin |

Every read result carries the `path` the write tools take as input, so the agent can chain
them without guessing.

## Configuration

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `YANDEX_DISK_OAUTH_TOKEN` | yes | — | OAuth token for the account (`YANDEX_DISK_API_KEY` is also accepted) |
| `YANDEX_DISK_ACTIONS` | no | all tools | Which tools get registered — see below |
| `YANDEX_DISK_ROOT` | no | whole disk | Confine every tool to this folder |
| `YANDEX_DISK_MAX_READ_BYTES` | no | `1048576` | Largest file `yadisk_read_file` will pull into context |
| `YANDEX_DISK_MAX_DOWNLOAD_BYTES` | no | `268435456` | Largest file `yadisk_download` will write locally |
| `YANDEX_DISK_TIMEOUT` | no | `30` | Per-request timeout, in seconds |
| `YANDEX_DISK_BASE_URL` | no | `https://cloud-api.yandex.net/v1/disk` | API base, for testing against a stub |

Put them in `~/.hermes/.env` — Hermes reads it in gateway and subprocess runs too — or export
them in the environment.

### Confining the agent to one folder

```dotenv
YANDEX_DISK_ROOT=/Hermes
```

Every path the agent gives is then interpreted relative to `disk:/Hermes`, and anything that
resolves outside it — an absolute `disk:/Private/...`, a `../` climb, a path pasted from
somewhere else — is refused before a request is made. Results are rewritten to hide the
prefix, so the agent sees a disk whose root *is* that folder. The bin is filtered the same
way: items deleted from outside the sandbox are neither listed nor restorable, and emptying
the whole bin is refused, because the bin is shared with the rest of the account.

### Choosing what the agent may do

```dotenv
YANDEX_DISK_ACTIONS=read,write      # no deleting, no sharing
YANDEX_DISK_ACTIONS=read            # a read-only Disk
YANDEX_DISK_ACTIONS=list,read_file  # exactly two tools
```

| Group | Tools |
|---|---|
| `read` | `disk_info`, `list`, `read_file`, `trash_list` |
| `download` | `download` (writes to the **local** machine) |
| `write` | `mkdir`, `write_file`, `upload`, `copy`, `move`, `trash_restore` |
| `share` | `publish` |
| `delete` | `delete`, `trash_empty` |
| `all` | everything — the default when the variable is unset |

Groups and individual tool names mix freely, with or without the `yadisk_` prefix, so you can
paste straight from the tool table above. A name that matches nothing is dropped: a typo can
only ever withhold a tool, never grant one, and a value naming nothing valid registers nothing
at all. Filtering happens when the plugin loads, so **restart Hermes** after changing it.

## Getting the credential

1. Open <https://yandex.ru/dev/disk/poligon/> signed in as the account you want the agent to
   use, and issue a debug token — the fastest route, and what it gives you is an ordinary
   OAuth token.
2. For a long-lived setup, register an app at <https://oauth.yandex.ru/> with the scopes
   `cloud_api:disk.info`, `cloud_api:disk.read` and `cloud_api:disk.write`, then run the usual
   OAuth flow.

Yandex Disk is free up to 5 GB and the API costs nothing to use. The token is the only
credential needed — see [Why REST and not WebDAV](#why-rest-and-not-webdav) for why there is
no app password to store.

## Installing

**A. From Git (recommended)**

```bash
hermes plugins install akinfold/hermes-yandex-disk --enable
```

**B. From PyPI** — discovered through the `hermes_agent.plugins` entry point:

```bash
pip install hermes-yandex-disk
```

Then add `yandex-disk` to `plugins.enabled` in `~/.hermes/config.yaml`.

**C. Drop-in** — unzip `hermes-yandex-disk-plugin-<version>.zip` from a
[release](https://github.com/akinfold/hermes-yandex-disk/releases) under `~/.hermes/plugins/`
so you get `~/.hermes/plugins/yandex-disk/plugin.yaml`, then enable it the same way.

## Why REST and not WebDAV

Yandex Disk exposes both a REST API and a WebDAV endpoint. This plugin is REST-only, because
WebDAV covers strictly less: no bin, no public links, and no reporting for the deferred
operations that large folder copies and deletes turn into.
rclone's Yandex backend made the same call. The one thing WebDAV adds — an app-password login
— is not worth a second stored credential for capabilities the REST API already has.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'

pytest --cov=hermes_yandex_disk --cov-report=term-missing   # unit tests, no network
ruff check . && ruff format --check .
radon cc -s -n C hermes_yandex_disk                         # must print nothing
```

## Running the live E2E tests

> These create, publish and permanently delete real files. **Use a throwaway Yandex account.**
> Everything is confined to one `hermes-e2e-<id>` folder that is destroyed in teardown.

Locally, put the token in `~/.yandex-disk-oauth` (and optionally the login in
`~/.yandex-disk-login`) and run:

```bash
pytest -m e2e -v
```

The suite skips itself when no credentials are available. On GitHub Actions, run the
**E2E (live)** workflow manually; it reads `YANDEX_DISK_OAUTH_TOKEN` from the
`yandex-disk-e2e` environment.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues privately rather than as
a public issue.

## License

MIT — see [LICENSE](LICENSE).

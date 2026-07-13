# Open Nova v1.0.0 Installation Guide

This guide covers a new installation, the first health checks, and supported
updates. GitHub Releases in
[`Neo-Isshin/open-nova`](https://github.com/Neo-Isshin/open-nova) are the
canonical public install and update authority.

## Requirements

- macOS with `zsh`, `git`, and Python 3.11 or newer;
- network access to GitHub and Python package indexes during installation;
- a local user account that can create files under its home directory;
- an LLM provider endpoint and credential if diary generation will call an
  external provider.

The default Runtime is `~/.open-nova`. Open Nova keeps application releases,
the active virtual environment, Settings, SQLite data, logs, generated assets,
and rollback state below that Runtime. The installer records the active Runtime
in `~/.config/open-nova/location.json`.

## Immutable one-line install

Run the versioned v1.0.0 bootstrap:

```bash
zsh -c "$(curl -fsSL 'https://raw.githubusercontent.com/Neo-Isshin/open-nova/v1.0.0/install/bootstrap.sh')"
```

The URL is pinned to the immutable `v1.0.0` tag. The bootstrap resolves the
latest stable, non-draft, non-prerelease GitHub Release, peels its tag to a full
commit, clones that exact commit into a detached source cache, and invokes the
installer. It never tracks `main`, `HEAD`, or a symbolic remote ref.

The hosted bootstrap is fresh-install-only. It fails before cloning when it
detects an existing Open Nova Runtime or managed service. Use `open-nova update`
for an existing installation.

## Install from a checkout

To inspect the plan without writes:

```bash
zsh install/bootstrap.sh --dry-run
```

To install from the current checkout:

```bash
zsh install/bootstrap.sh
```

The dependency authority is `pyproject.toml`. The ordinary installation
includes Base-Pipeline, Dashboard, and Nova-Task. nova-RAG is optional. Developer
test dependencies are opt-in and are never part of the Runtime payload.

Useful non-interactive controls include:

```bash
zsh install/bootstrap.sh -- --no-wizard
zsh install/bootstrap.sh -- --enable-rag
zsh install/bootstrap.sh -- --no-scheduler
zsh install/bootstrap.sh -- --no-dashboard-server
zsh install/bootstrap.sh -- --runtime /path/to/runtime
zsh install/bootstrap.sh -- --no-shell-path
zsh install/bootstrap.sh -- --shell-path-file /path/to/profile
```

The installer performs a preflight before changing the Runtime. It verifies
paths, Python compatibility, source cleanliness, port policy, collision guards,
and the selected dependency groups. A failed preflight stops the transaction.

## Guided choices

The interactive installer asks only for choices needed by the selected product
profile:

- interface language (`zh-CN` or `en-US`);
- whether to enable nova-RAG;
- local or cloud embedding mode when nova-RAG is enabled;
- LLM provider, endpoint, model, and credential reference;
- managed Dashboard, nova-RAG, and scheduler service choices;
- optional external-tool coverage and Desktop diary shortcut.

Provider credentials are written only to the Runtime-local private secret store
with restrictive permissions. Settings contain references and provider
metadata, not raw secret values. Never place a real credential in the repository
or in a command that will be retained in shell history.

## CLI and shell path

The Runtime command shim is:

```text
~/.open-nova/bin/open-nova
```

The installer also attempts to link it at `~/.local/bin/open-nova` and adds a
managed PATH block to the selected shell profile. Use `--no-shell-path` to skip
that profile edit, or `--shell-path-file /path/to/profile` to choose a file.

Start a new shell, then verify:

```bash
open-nova doctor
open-nova onboard status
open-nova model show
open-nova config show
```

The installer also runs its post-install doctor before declaring success. Any
blocking result keeps the previous active source and venv available for
recovery.

## Dashboard and nova-RAG

The Dashboard normally listens on loopback. Port `3036` is preferred, with
safe fallback ports when it is occupied. The installer prints the selected URL
in its completion summary.

When nova-RAG local mode is enabled, its service normally uses loopback port
`3037`. External Agent integrations must use the read-only API described in
[rag-external-agent-contract.md](rag-external-agent-contract.md).

Open Nova does not expose these services to a public network by default. Keep
the loopback binding unless you have separately configured authenticated,
private network access.

## Stable updates

Preview an update:

```bash
open-nova update --dry-run
```

Apply the latest stable Release:

```bash
open-nova update --apply
```

The default updater accepts only a stable GitHub Release whose tag resolves to
a full commit. No Release, API rate limit or error, draft/prerelease state,
malformed tag, abbreviated SHA, symbolic ref, or non-commit object fails closed.
There is no silent fallback to another host or to `main`.

For a checkout-based operator update, the installer supports explicit upgrade
mode:

```bash
zsh install/install.sh --upgrade --runtime /path/to/runtime --source-root "$PWD"
```

An update creates a new immutable release directory and venv, validates them,
then switches active pointers atomically. Settings, SQLite, logs, generated user
data, service configuration, and rollback metadata remain Runtime-owned and are
preserved. If activation or health verification fails, the transaction restores
the previous active source and venv.

## Backup and recovery

Before a material update, back up important generated diaries, reports, Runtime
Settings, and SQLite data. Do not copy a live SQLite database without accounting
for its WAL/SHM state.

Useful read-only checks are:

```bash
open-nova doctor
open-nova doctor --scheduler
open-nova update --dry-run
```

Do not move or reuse a published version tag. If a published artifact or
checksum is withdrawn, stop distribution and install a newer version after it is
released.

## Further operations

- [Complete local operations runbook](local-operations-runbook.md)
- [CLI product boundary](cli-boundary.md)
- [nova-RAG external Agent contract](rag-external-agent-contract.md)
- [Security policy](../SECURITY.md)

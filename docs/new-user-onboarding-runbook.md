# Actanara Installation Guide

This guide covers a new installation, the first health checks, and supported
updates. GitHub Releases in
[`Neo-Isshin/actanara`](https://github.com/Neo-Isshin/actanara) are the
canonical public install and update authority.

## Requirements

- macOS with `zsh`, `git`, and Python 3.11 or newer;
- network access to GitHub and Python package indexes during installation;
- a local user account that can create files under its home directory;
- an LLM provider endpoint and credential if diary generation will call an
  external provider.

The default Runtime is `~/.actanara`. Actanara keeps application releases,
the active virtual environment, Settings, SQLite data, logs, generated assets,
and rollback state below that Runtime. The installer records the active Runtime
in `~/.config/actanara/location.json`.

## One-line install or refresh

Run the public installer:

```bash
curl -fsSL https://raw.githubusercontent.com/Neo-Isshin/actanara/main/install/bootstrap.sh | zsh
```

GitHub serves the maintained bootstrap from `main`. The launcher resolves the
official `origin/main` to a full commit, checks out that exact commit in a
detached source cache, and invokes the installer.

The hosted bootstrap supports both new and existing Runtimes. It updates a
current installation in place, or asks before rebuilding managed code and
dependencies for an older layout. User Settings and data remain in place.

## Install from a checkout

To inspect the plan without writes:

```bash
zsh install/bootstrap.sh --dry-run
```

To install from the current checkout:

```bash
zsh install/bootstrap.sh
```

`pyproject.toml` is the direct dependency/profile contract, while
`install/runtime-dependencies.lock.json` is the exact Runtime resolution
authority for every supported Python ABI and macOS architecture. Fresh installs
and candidate-venv rebuilds use the same wheel-only, SHA-256-verified lock.
`requirements-release.txt` locks release build tooling only and is not a
Runtime dependency lock. The ordinary installation includes Base-Pipeline,
Dashboard, and Nova-Task. nova-RAG is optional. Developer test dependencies are
opt-in and are never part of the default production profile.

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
~/.actanara/bin/actanara
```

The installer also attempts to link it at `~/.local/bin/actanara` and adds a
managed PATH block to the selected shell profile. Use `--no-shell-path` to skip
that profile edit, or `--shell-path-file /path/to/profile` to choose a file.

Start a new shell, then verify:

```bash
actanara doctor
actanara onboard status
actanara model show
actanara config show
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

Actanara does not expose these services to a public network by default. Keep
the loopback binding unless you have separately configured authenticated,
private network access.

## Stable updates

Preview an update:

```bash
actanara update --dry-run
```

Apply the latest stable Release:

```bash
actanara update --apply
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

The updater computes a dependency fingerprint before stopping services. When
the active immutable dependency marker, Python ABI/platform identity, enabled
profiles, direct contract, locked transitive closure, and live distributions all
match, it creates only a new immutable source generation and reuses the existing
venv generation without invoking pip. When that evidence is missing, untrusted,
or different, it builds a separate candidate venv from the locked wheelhouse and
switches source and venv pointers only after validation. The active venv is never
modified in place. Unsafe or ambiguous profile evidence fails closed before
service changes rather than guessing a dependency selection.

Operational controls are:

```bash
actanara update --dry-run
actanara update --apply --offline --ref <full-commit-sha>
actanara update --apply --offline --source-root /path/to/source
actanara update --apply --source-only
actanara update --apply --force-rebuild
```

`--source-only` fails closed unless reuse is proven. `--force-rebuild` always
creates a candidate venv. Offline source selection requires a local
`--source-root` or a full `--ref` already present in the installer source cache;
offline mode does not resolve `latest`. Offline rebuilds additionally require a
complete trusted cache at `<runtime>/app/dependency-cache/v1` and fail before
service stop when it is missing or altered. Settings, SQLite, logs, generated user data, service
configuration, and rollback metadata remain Runtime-owned and are preserved.
If activation or health verification fails, the transaction restores the
previous active source, venv, and service state. Product version reporting comes
from the active source manifest; stale `actanara-*.dist-info` in a reused venv
is not authoritative and is not refreshed in place.

## Backup and recovery

Before a material update, back up important generated diaries, reports, Runtime
Settings, and SQLite data. Do not copy a live SQLite database without accounting
for its WAL/SHM state.

Useful read-only checks are:

```bash
actanara doctor
actanara doctor --scheduler
actanara update --dry-run
```

Do not move or reuse a published version tag. If a published artifact or
checksum is withdrawn, stop distribution and install a newer version after it is
released.

## Further operations

- [Complete local operations runbook](local-operations-runbook.md)
- [CLI product boundary](cli-boundary.md)
- [nova-RAG external Agent contract](rag-external-agent-contract.md)
- [Security policy](../SECURITY.md)

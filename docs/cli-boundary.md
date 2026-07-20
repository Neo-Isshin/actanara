# Actanara CLI Boundary

Status: current-version product boundary for the installable `actanara`
command. This document separates stable user commands from guarded operator
commands and compatibility/debug surfaces.

## Product Commands

These commands are the supported first-line CLI surface after install:

```bash
actanara
actanara doctor
actanara model show
actanara model list
actanara model set --provider PROVIDER --model MODEL
actanara model set --api-key-env LLM_API_KEY
actanara model key --value-stdin
actanara onboard status
actanara onboard doctor
actanara onboard plan
actanara dashboard restart
actanara config show
actanara config keys
actanara config get general.timezone
actanara config set general.timezone Asia/Hong_Kong
actanara search "deployment issue" --top-k 5 --json
actanara task
actanara pipeline [YYMMDD|YYYY-MM-DD]
actanara rag-update --dry-run
actanara rag-rebuild --dry-run
actanara update --dry-run
```

The default no-argument command prints this product command guide. README and
new-user docs should prefer these commands.

## Guarded Maintenance

These commands are user-visible, but write-capable variants require explicit
confirmation phrases:

- `actanara rag-update`
- `actanara rag-rebuild`
- `actanara foundation rebuild-sqlite-cache`
- `actanara foundation approve-diary-metrics`
- `actanara model key --value-stdin`

Dry-run and read-only modes are safe to document for routine operations. Any
write-capable example must include the confirmation requirement and the expected
rollback or audit artifact when one exists.

## Compatibility And Debug Commands

These command groups remain available for migration, installer, dashboard or
operator debugging, but they are not the primary product surface:

- `actanara settings ...`
- `actanara onboarding ...`
- `actanara foundation ...`
- `actanara secrets ...`
- `actanara rag search-memory ...`

`actanara rag search-memory` is a compatibility alias for the read-only
Dashboard RAG facade. Product docs should prefer `actanara search ...`.

## Scheduler and Service Boundary

macOS and Linux scheduling use one planner:
`data_foundation.scheduler_preview`.

- On macOS, Dashboard system timer controls retain the established launchd
  handoff: managed LaunchAgent plists, `launchctl`, and scheduler settings are
  updated in one transaction.
- On Linux, the same Dashboard controls install, reconcile, and safely remove
  managed systemd user services and timers through `systemctl --user`.
- Installer and Dashboard Linux flows share the render, alignment, backup,
  compensation, and recovery implementation in
  `data_foundation.systemd_user`; neither flow changes linger implicitly or
  removes an unmanaged unit.
- Dashboard and optional RAG services use the platform service manager:
  launchd on macOS and systemd user units on Linux.

The expected current state is one platform-neutral service contract with
separate, guarded launchd and systemd-user backends.

## Out Of Boundary

Do not add current-version CLI commands that:

- edit prompt payloads, diary schemas, RAG evidence schema, or machine contracts;
- let external governance agents modify or append RAG facts;
- bypass the platform service manager to mutate launchd or systemd definitions;
- hide RAG sync failures as successful pipeline completion.

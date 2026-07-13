# Open Nova CLI Boundary

Status: current-version product boundary for the installable `open-nova`
command. This document separates stable user commands from guarded operator
commands and compatibility/debug surfaces.

## Product Commands

These commands are the supported first-line CLI surface after install:

```bash
open-nova
open-nova doctor
open-nova model show
open-nova model list
open-nova model set --provider PROVIDER --model MODEL
open-nova model set --api-key-env LLM_API_KEY
open-nova model key --value-stdin
open-nova onboard status
open-nova onboard doctor
open-nova onboard plan
open-nova config show
open-nova config keys
open-nova config get general.timezone
open-nova config set general.timezone Asia/Hong_Kong
open-nova search "deployment issue" --top-k 5 --json
open-nova task
open-nova pipeline [YYMMDD|YYYY-MM-DD]
open-nova rag-update --dry-run
open-nova rag-rebuild --dry-run
open-nova update --dry-run
```

The default no-argument command prints this product command guide. README and
new-user docs should prefer these commands.

## Guarded Maintenance

These commands are user-visible, but write-capable variants require explicit
confirmation phrases:

- `open-nova rag-update`
- `open-nova rag-rebuild`
- `open-nova foundation rebuild-sqlite-cache`
- `open-nova foundation approve-diary-metrics`
- `open-nova model key --value-stdin`

Dry-run and read-only modes are safe to document for routine operations. Any
write-capable example must include the confirmation requirement and the expected
rollback or audit artifact when one exists.

## Compatibility And Debug Commands

These command groups remain available for migration, installer, dashboard or
operator debugging, but they are not the primary product surface:

- `open-nova settings ...`
- `open-nova onboarding ...`
- `open-nova foundation ...`
- `open-nova secrets ...`
- `open-nova rag search-memory ...`

`open-nova rag search-memory` is a compatibility alias for the read-only
Dashboard RAG facade. Product docs should prefer `open-nova search ...`.

## Scheduler Boundary

macOS scheduling uses one planner: `data_foundation.scheduler_preview`.

- Dashboard system timer controls call the Dashboard scheduler service, which
  writes managed LaunchAgent plists, calls `launchctl`, and updates scheduler
  settings in one operation.
- Installer and CLI onboarding apply use the same planner in explicit phases:
  runtime bootstrap, plist write, then launchd registration. This keeps install
  bootstrap auditable without routing through a running Dashboard process.
- Linux scheduler apply is outside the v1.0.0 product boundary; the managed
  scheduler implementation targets macOS user LaunchAgents.

The expected current state is not "one function owns every call"; it is "one
LaunchAgent contract and one planner, with separate guarded apply surfaces."

## Out Of Boundary

Do not add current-version CLI commands that:

- edit prompt payloads, diary schemas, RAG evidence schema, or machine contracts;
- let external governance agents modify or append RAG facts;
- turn Linux scheduler previews into write-capable apply flows;
- hide RAG sync failures as successful pipeline completion.

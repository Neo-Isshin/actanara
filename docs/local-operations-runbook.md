# Open Nova Local Operations Runbook

[中文](local-operations-runbook.zh-CN.md) · [Back to English README](../README.md)

Status: Public operator guide<br>
Scope: Current local macOS runtime

## 1. Purpose

This Runbook covers the complete path from installation through first-run setup, history generation, daily operation, subsystem maintenance, updates, and troubleshooting. The README provides a five-minute start; use this guide whenever an operation needs prerequisites, expected results, logs, or recovery steps.

## 2. Before Installation

- macOS;
- Python `>= 3.11`; on supported macOS systems, the installer installs a managed standalone Python when no compatible version is available;
- `git` and `curl` available on `PATH`; `python3` is also required if managed Python installation is disabled;
- network access to the installation source and selected LLM or embedding provider;
- enough local storage for Dashboard and optional local embedding dependencies.

The installer detects supported agent-runtime paths and lets you choose which tools Open Nova should cover.

## 3. One-command Installation

```bash
zsh -c "$(curl -fsSL 'https://raw.githubusercontent.com/Neo-Isshin/open-nova/v1.0.0/install/bootstrap.sh')"
```

This versioned entry is fresh-install-only. Before writing cache or Runtime
state, it refuses any existing target/default/`NOVA_HOME`/location-pointer
Runtime or managed Open Nova LaunchAgent. Source acquisition resolves the
latest stable GitHub Release and pins its full commit; it never falls back to
`main` or `origin/HEAD`.

The installer guides you through:

1. interface and pipeline language;
2. external agent-runtime paths;
3. LLM Provider, Endpoint, Model, and API key;
4. optional `nova-RAG` enablement;
5. local or cloud embedding configuration;
6. managed macOS scheduling and the Dashboard service.

Provider keys are stored in the runtime-local secret store at `$NOVA_HOME/state/secrets`. Open Nova creates the directory with mode `0700` and each secret file with mode `0600`; the user does not need to configure Keychain, and Pipeline or LaunchAgent jobs can run unattended without recurring authorization prompts. A cloud embedding key, when configured, uses the same secret store. Runtime settings, databases, logs, and generated assets remain in user-owned local paths. Requests sent to configured LLM or embedding providers follow the selected provider's endpoint and data policy.

An existing `macos-keychain` secret reference is accepted only for compatibility migration. Open Nova copies a readable legacy secret into the runtime-file store and does not automatically delete the old Keychain item. If macOS does not allow the old item to be read, enter that provider key once in Dashboard.

## 4. Installation Summary and Runtime Paths

Keep the terminal summary after installation. It contains the active Dashboard URL, runtime location, and common commands.

| Path | Purpose |
| :--- | :--- |
| `~/.open-nova` | Runtime home |
| `~/.config/open-nova/location.json` | Active runtime pointer |
| `~/.open-nova/artifacts/diary` | Diaries and period summaries |
| `~/.open-nova/state/logs` | Pipeline, service, and installer logs |
| `~/.open-nova/state/secrets` | Runtime-local provider-key store (`0700` directory, `0600` files) |
| `~/.open-nova/bin/open-nova` | Product CLI |
| `~/Library/LaunchAgents/` | Managed macOS user-service plists |

Default Dashboard URL:

```text
http://127.0.0.1:3036/dashboard
```

If `3036` is occupied, the installer selects another port. The installation summary and active runtime settings are authoritative.

## 5. Post-install Verification

```bash
open-nova doctor
open-nova model show
open-nova onboard status
open-nova config show
```

Inspect individual subsystems when needed:

```bash
open-nova doctor --installer
open-nova doctor --pipeline
open-nova doctor --scheduler
open-nova doctor --rag        # Only when nova-RAG is enabled
```

A warning does not always block operation. Resolve errors and explicit readiness failures first. Verify the runtime pointer, LLM provider, Dashboard, scheduler, and optional RAG Server state.

## 6. Configure the LLM Provider

1. Open Dashboard Settings;
2. select the Provider;
3. verify the Endpoint and Model;
4. enter the API key;
5. run the availability test;
6. save only after the test passes.

Then verify:

```bash
open-nova model show
open-nova doctor --pipeline
```

Never place a real key in README files, logs, shell history, committed files, or ordinary settings fields. To rotate a key, create the replacement at the provider first, then update it through Dashboard. Open Nova writes the replacement to the runtime-file secret store; no separate Keychain configuration is required.

## 7. First History Generation

### 7.1 Preview the Plan

Select **Generate Historical Data** in Dashboard, choose a date range, and select **Preview Plan**. Review:

- pending daily records;
- weekly and monthly reports;
- existing daily artifacts that will be skipped, plus weekly and monthly reports that will be regenerated;
- estimated LLM calls;
- `nova-RAG` synchronization tasks;
- the final selected tasks.

Clear tasks you do not want to run. Plan preview does not mutate the runtime.

### 7.2 Queue Generation

After reviewing the plan, select **Queue Generation**. Open Nova runs only selected tasks:

- Base Pipeline generates daily and period artifacts;
- Foundation persists normalized activity, reports, and snapshots;
- Nova-Task updates task candidates and evidence;
- when enabled and ready, `nova-RAG` synchronizes the retrieval index.

Large ranges may take time. Blank dates can produce structured placeholder artifacts without an LLM call.

### 7.3 Monitor, Cancel, and Retry

Use **Background Tasks** and **Messages** to inspect status. A running job can receive a cancellation request, and partial failures can retry only failed items. Keep at most one active history-backfill job per runtime.

After completion, inspect:

- Diary;
- weekly and monthly reports;
- AI Assets;
- Nova-Task;
- `nova-RAG` status and search results.

## 8. Daily Base Pipeline Operation

Run today or a specific date manually:

```bash
open-nova pipeline
open-nova pipeline YYYY-MM-DD
```

The Pipeline reads configured external-tool paths and attributes workspaces from observed evidence. Do not rely on the command's CWD as the only attribution source.

The macOS installer registers managed LaunchAgents by default. Check scheduling with:

```bash
open-nova doctor --scheduler
```

Before moving scheduling to external automation or another cron job, prevent duplicate execution with the managed schedule.

## 9. Dashboard Operations

Use Dashboard to:

- review daily, weekly, and monthly reports;
- inspect live usage and AI assets;
- configure LLM providers, tool paths, and scheduling;
- plan history backfills;
- monitor background tasks and messages;
- review Nova-Task;
- operate `nova-RAG`.

If Dashboard becomes unavailable:

```bash
open-nova dashboard restart
open-nova doctor --installer
```

## 10. Nova-Task Operations

Nova-Task is a Beta subsystem. It derives candidate tasks and evidence from real agent-runtime activity, conversations, and tool results while preserving human review and takeover.

```bash
open-nova task
```

Operating principles:

- retain human review for top-level or high-impact changes;
- allow routine child-task maintenance, but audit incorrect attribution periodically;
- before importing an RFC, PRD, Roadmap, or Audit, confirm that it contains no information that should stay outside the local work graph;
- treat the task board as a record of actual work, not a conventional backlog.

## 11. nova-RAG Operations

Check readiness:

```bash
open-nova doctor --rag
```

Search:

```bash
open-nova search "deployment issue" --top-k 5 --json
```

Preview maintenance first:

```bash
open-nova rag-update --dry-run
open-nova rag-rebuild --dry-run
```

Applying an update or rebuild requires explicit confirmation. External agent runtimes should prefer the read-only Dashboard facade:

```text
GET  /api/rag/external/health
GET  /api/rag/external/stats
GET  /api/rag/external/contract
POST /api/rag/external/search
```

The default Dashboard base URL is `http://127.0.0.1:3036`; the direct RAG Server defaults to `http://127.0.0.1:3037`. Active runtime settings are authoritative.

## 12. Updates and Rollback Preparation

Preview an update:

```bash
open-nova update --dry-run
```

Apply a guarded update:

```bash
open-nova update --apply
```

Select an explicit immutable commit (full 40- or 64-character hexadecimal object ID):

```bash
open-nova update --dry-run --ref <full-commit-sha>
open-nova update --apply --ref <full-commit-sha>
```

Omitting `--ref` resolves the latest non-draft, non-prerelease GitHub
Release, peels its tag to a full commit, and pins that commit. A custom
`--source-url` requires an explicit full commit. `--source-root` uses the
supplied checkout exactly as-is and cannot be combined with `--ref`.

Before updating:

1. let active jobs finish;
2. save the current `open-nova doctor` output;
3. back up runtime settings and important generated assets;
4. record the current runtime and commit;
5. run Dry Run first.

## 13. Logs and Troubleshooting

Inspect these locations first:

```text
~/.open-nova/state/logs/
~/.open-nova/config/settings.json
~/.config/open-nova/location.json
```

Troubleshoot in this order:

1. confirm the runtime pointer;
2. confirm the Dashboard URL and port;
3. test the LLM provider;
4. confirm that `$NOVA_HOME/state/secrets` exists with mode `0700`, its secret files use mode `0600`, and Pipeline or LaunchAgents use the same `NOVA_HOME`;
5. verify external-tool paths;
6. check for a missing or duplicate scheduler;
7. verify the RAG Server and active index;
8. inspect failed, cancelled, or retryable background tasks.

When reporting an issue, retain the command, relevant output, log paths, runtime pointer, and subsystem Doctor output. Never paste a real secret.

## 14. Data, Backups, and Privacy

- Do not commit runtime databases, logs, caches, generated diaries, or keys;
- inspect screenshots for email addresses, usernames, project names, local paths, and work content before publishing;
- external LLM and embedding requests are processed by the configured provider;
- back up important diaries, reports, Nova-Task data, and runtime settings;
- before removing or migrating a runtime, stop managed services and verify that backups are restorable.

## 15. Completion Checklist

- [ ] Dashboard is reachable;
- [ ] LLM provider availability test passes;
- [ ] Pipeline Doctor has no blocking errors;
- [ ] scheduler state matches the intended configuration;
- [ ] first history generation has completed or is observable;
- [ ] Diary, AI Assets, and Nova-Task contain expected data;
- [ ] when `nova-RAG` is enabled, Server, index, and search are ready;
- [ ] log, update, and backup locations are understood.

## 16. Related References

- [New-user installation guide](new-user-onboarding-runbook.md)
- [CLI product boundary](cli-boundary.md)
- [RAG external agent contract](rag-external-agent-contract.md)
- [Back to English README](../README.md)

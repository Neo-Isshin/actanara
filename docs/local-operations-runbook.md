# Open Nova Local Operations Runbook

<p align="center">
  <img src="https://img.shields.io/badge/Language-English%20·%20Current-2563EB?style=for-the-badge" alt="Current language: English">
  <a href="local-operations-runbook.zh-CN.md"><img src="https://img.shields.io/badge/Language-简体中文-C026D3?style=for-the-badge" alt="切换到简体中文 Runbook"></a>
</p>

**English** · [简体中文](local-operations-runbook.zh-CN.md) · [Back to the English README](../README.md)

Status: Public operator guide<br>
Scope: Open Nova · Local macOS runtime

## 1. Purpose

The README explains the product and provides a quick start. This Runbook covers the complete operating path from pre-install checks through first-run setup, historical backfill, daily Pipeline use, Dashboard, Nova-Task, `nova-RAG`, updates, backups, and troubleshooting.

In this guide, an **agent runtime** means an AI tool environment with its own sessions, logs, memory, and execution context, such as Codex, Claude Code, Gemini CLI, OpenClaw, or Hermes.

## 2. Version and Release Boundaries

- GitHub is the only public release and installation source. Private development archives are not part of the public installation path.
- Published tags, Releases, and artifacts remain immutable.
- The fresh-install command selects only GitHub's latest stable Release, whose tag is resolved to an exact full source commit.
- `v1.0.0` has been withdrawn and remains available for audit only. It should not be installed or recommended.

> [!IMPORTANT]
> Each stable Release carries the exact Runtime dependency lock for supported Python ABIs and macOS architectures. The installer never resolves application source from `main`, `HEAD`, or a symbolic branch.

## 3. Pre-install Checks

Confirm the following:

- 🍎 You are using macOS;
- 🛠️ `zsh`, `git`, and `curl` are available on `PATH`;
- 🐍 Python `>=3.11` is required. On supported Apple Silicon and Intel Macs, the installer can download and verify a managed Python when no compatible version is present;
- 🌐 GitHub, the Python package index, and the selected LLM / embedding provider are reachable;
- 💾 Enough local storage is available for the runtime, Dashboard, and optional local-embedding dependencies;
- 🔐 Provider API keys are ready, but have not been written to shell history, a README, or an ordinary configuration file.

Check the base tools:

```bash
command -v zsh
command -v git
command -v curl
python3 --version 2>/dev/null || true
```

The installer detects supported agent-runtime paths and asks the operator which tools to connect during the guided setup.

## 4. Fresh Installation of the Latest Stable Release

Use the public stable-channel one-liner:

```bash
curl -fsSL https://github.com/Neo-Isshin/open-nova/releases/latest/download/install.sh | zsh
```

The command keeps source selection safe while hiding release plumbing from the user:

1. GitHub resolves `install.sh` from the latest stable Release;
2. The launcher rejects draft, prerelease, and explicitly `WITHDRAWN` releases;
3. It peels the selected tag to a full commit;
4. It installs that detached commit and never follows `main`, `HEAD`, or a symbolic branch.

> [!NOTE]
> Every stable Release publishes the same asset name, so this command advances only when a new stable Release is formally published.

> [!WARNING]
> This command is for a fresh installation only. If the bootstrap detects an existing Open Nova runtime, an active runtime pointer, or a managed LaunchAgent, it stops before writing to the installation source cache. For an existing installation, run `open-nova update` or `open-nova update --dry-run` to review the plan, then use `open-nova update --apply` to perform the update.

The installation wizard covers, in order:

1. Interface language and Pipeline language;
2. External agent-runtime paths;
3. LLM Provider, Endpoint, Model, and API Key;
4. Whether to enable `nova-RAG`;
5. Local or cloud embedding configuration;
6. macOS Dashboard and scheduler services.

The public installer locale uses `zh-CN` or `en-US`; the runtime's internal Pipeline profile uses `zh` or `en`. Users normally select a language in the installation wizard and should not manually mix values from these two groups.

## 5. Installer Write Locations

| Path | Purpose |
| :--- | :--- |
| `~/.cache/open-nova/installer` | Installation source cache |
| `~/.open-nova` | Runtime, virtual environment, settings, database, logs, secrets, and generated assets |
| `~/.config/open-nova/location.json` | Active runtime pointer |
| `~/.local/bin/open-nova` | User-facing CLI entry on `PATH` |
| `~/.zprofile` | Marked `PATH` block; disable during installation with `--no-shell-path` |
| `~/Desktop/Open Nova` | Desktop shortcut to the diary directory, created by default |
| `~/Library/LaunchAgents/` | Dashboard, Scheduler, and optional RAG services |

Provider keys are stored in `$NOVA_HOME/state/secrets`. The secrets directory uses mode `0700`, and each secret file uses mode `0600`.

Legacy `macos-keychain` references are used only for compatibility migration: readable secrets are copied to the runtime secret store, but Open Nova does not automatically delete old Keychain items. If a legacy secret cannot be read, enter the provider key again in the Dashboard.

## 6. Installation Summary and Basic Verification

Keep the terminal summary after installation. It contains the actual Dashboard URL, runtime location, and common commands.

Default Dashboard address:

```text
http://127.0.0.1:3036/dashboard
```

If `3036` is occupied, the installer selects another port. Always use the installation summary and current runtime settings as the authority.

Run these read-only commands first:

```bash
open-nova doctor
open-nova model show
open-nova onboard status
open-nova config show
```

Inspect individual subsystems:

```bash
open-nova doctor --installer
open-nova doctor --pipeline
open-nova doctor --scheduler
open-nova doctor --rag
```

A warning does not always block operation. Resolve errors and explicit readiness failures first. Focus on the runtime pointer, Provider, Dashboard, scheduler, and optional RAG Server.

## 7. Configure the LLM Provider

1. Open the Dashboard URL shown in the installation summary;
2. Open LLM Provider settings;
3. Select the Provider and verify the Endpoint and Model;
4. Enter the API Key;
5. Run the availability test;
6. Save only after the test passes.

Then run:

```bash
open-nova model show
open-nova doctor --pipeline
```

Never put a real key in a README, Issue, log, shell history, Git-tracked file, or ordinary settings field. When an external LLM or cloud embedding provider is configured, relevant derived work content is sent according to the selected endpoint and provider data policy.

## 8. First Historical-Data Generation

### 8.1 Preview the Plan

Select **Generate Historical Data** in the Dashboard, choose a date range, and preview the plan first. Review:

- Pending daily records;
- Weekly and monthly reports;
- Existing artifacts that will be skipped or regenerated;
- Estimated LLM calls;
- Nova-Task tasks;
- `nova-RAG` synchronization tasks, when enabled.

Clear tasks you do not want to run. Plan preview does not write to the runtime.

### 8.2 Queue and Monitor

After confirmation, add the selected tasks to the background queue. Large date ranges take longer; dates without activity may produce only structured placeholder artifacts and may not call an LLM.

Use Background Tasks and Messages to inspect status. A running job can receive a cancellation request, and some partially failed backfill jobs can retry only failed items. Keep at most one active historical-backfill job per runtime.

After completion, inspect:

- Diaries and period reports;
- AI Assets;
- Nova-Task board;
- `nova-RAG` status and search results, when enabled.

## 9. Daily Base Pipeline Operation

Run manually:

```bash
open-nova pipeline
open-nova pipeline YYYY-MM-DD
```

Without a date, `open-nova pipeline` processes the **previous calendar day in the configured time zone**; it is not a shortcut for processing today. Use `YYYY-MM-DD` when specifying a date.

The Pipeline writes diaries, reports, and Foundation data. If the target date has already been generated completely, only an explicit `--force` regenerates it from the frozen Foundation input.

Open Nova attributes workspaces using execution evidence from external runtimes. It does not treat the CLI's current directory as the sole attribution source.

Check managed scheduling:

```bash
open-nova doctor --scheduler
```

If an external automation system will invoke the Pipeline, prevent duplicate execution with the installer-managed schedule.

## 10. Daily Dashboard Operations

Use the Dashboard to:

- Review daily, weekly, and monthly diaries;
- Inspect live usage, AI Assets, and workspace attribution;
- Configure Providers, external-tool paths, and scheduling;
- Plan historical backfills;
- Monitor background tasks and messages;
- Review Nova-Task;
- Operate optional `nova-RAG`.

If the service becomes unavailable:

```bash
open-nova dashboard restart
open-nova doctor --installer
```

Do not assume the Dashboard always uses port `3036`. Check the installation summary or `open-nova config show` first.

## 11. Nova-Task Operations

Open the task board in the Dashboard. The CLI `task` command only reads and prints task statistics; it does not open the interface:

```bash
open-nova task
open-nova task --json
```

Nova-Task is a Beta subsystem. It derives a task structure from real runtime activity, conversations, file changes, tool results, and execution evidence.

Operating principles:

- Retain human review for top-level or high-impact tasks;
- Allow the system to maintain routine subtasks, but check status and attribution periodically;
- Before importing an RFC, PRD, Roadmap, or Audit, confirm that its contents are appropriate for the local task graph;
- Treat the task graph as a description of real work, not a traditional hand-written to-do list.

## 12. nova-RAG Operations

Check status:

```bash
open-nova doctor --rag
```

Search local memory:

```bash
open-nova search "deployment issue" --top-k 5
open-nova search "deployment issue" --top-k 5 --json
```

Automation consuming JSON output should check the `available` field. When RAG is unavailable, the command may still return a successful structured status response.

Preview maintenance operations first:

```bash
open-nova rag-update --dry-run
open-nova rag-rebuild --dry-run
```

External agent runtimes should prefer the Dashboard's read-only facade:

```text
GET  /api/rag/external/health
GET  /api/rag/external/stats
GET  /api/rag/external/contract
POST /api/rag/external/search
```

The default Dashboard base URL is `http://127.0.0.1:3036`; the direct RAG Server defaults to `http://127.0.0.1:3037`. The runtime settings determine the actual ports.

The external-runtime contract allows only health checks, statistics, contract reads, and search. It does not permit memory writes, index changes, global-setting changes, or service-lifecycle control.

## 13. Updates

View the update plan:

```bash
open-nova update
```

Run a no-change preview:

```bash
open-nova update --dry-run
```

Apply a protected update:

```bash
open-nova update --apply
```

- With no arguments, the command displays only the plan;
- `--dry-run` runs a bootstrap and installer preview and reports whether the active venv can be reused or a locked candidate rebuild is required; a cold remote source cache can still limit the preview to source acquisition;
- Only `--apply` performs the real update transaction.

The stable-channel installer and updater use the same dependency contract and
exact Runtime lock. Unreleased `main` code is never selected by this channel.

The default apply mode reuses the active venv only when its immutable dependency
marker, environment identity, selected profiles, exact Runtime lock, and live
distributions all match. This path changes the source pointer without running
pip. Otherwise the updater creates and validates a new venv from the persistent
hash-verified wheelhouse before atomically switching pointers. It never installs
into the active venv. A legacy Runtime with no marker takes the rebuild path;
malformed or unsafe profile evidence fails closed before service changes.

```bash
open-nova update --apply --offline --ref <full-commit-sha>        # cached remote commit
open-nova update --apply --offline --source-root /path/to/source  # local checkout
open-nova update --apply --source-only                            # require venv reuse or fail closed
open-nova update --apply --force-rebuild                          # require a new locked candidate venv
```

Offline remote selection requires a full commit already present under the
installer `--cache-root`; offline mode never resolves `latest`. An offline
rebuild also fails before service stop when the trusted cache under
`~/.open-nova/app/dependency-cache/v1` is incomplete or altered.

Select an immutable full commit:

```bash
open-nova update --dry-run --ref <full-commit-sha>
open-nova update --apply --ref <full-commit-sha>
```

Before updating:

1. Confirm that the Pipeline and background tasks have finished;
2. Save the output of `open-nova doctor`;
3. Back up runtime settings and important generated assets;
4. Record the current runtime and commit;
5. Run the plan or Dry Run first.

## 14. Logs and Troubleshooting

Check these locations first:

```text
~/.open-nova/state/logs/
~/.open-nova/config/settings.json
~/.config/open-nova/location.json
```

Troubleshoot in this order:

1. Is the runtime pointer correct?
2. Does the Dashboard URL and port match the installation summary?
3. Does the LLM Provider test pass?
4. Does `$NOVA_HOME/state/secrets` use mode `0700`, with secret files using `0600`?
5. Do the Pipeline and LaunchAgents use the same `NOVA_HOME`?
6. Do external-tool paths exist, and are they enabled?
7. Is the Scheduler duplicated, missing, or failing?
8. Are the RAG Server and active index ready?
9. Are background tasks failed, canceled, or retryable?

When filing an Issue, include commands, necessary output, log paths, the runtime pointer, and relevant Doctor results. Remove secrets, email addresses, usernames, private project names, machine paths, and work content.

## 15. Data, Backups, and Privacy

- Do not commit runtime databases, logs, caches, secrets, generated diaries, or indexes;
- Before publishing screenshots, check for email addresses, usernames, project names, machine paths, token / RAG metrics, and work content;
- Prefer a disposable, isolated runtime with fully synthetic data when publishing screenshots;
- External LLM / embedding requests are processed by the user's configured provider;
- Back up important diaries, reports, Nova-Task data, and runtime settings regularly;
- Before deleting or migrating a runtime, stop managed services and confirm that the backup can be restored.

## 16. Uninstallation Boundary

Open Nova does not currently include a product-level one-command uninstaller. Do not remove only `~/.open-nova`, because this can leave behind:

- macOS LaunchAgents;
- The `~/.local/bin/open-nova` CLI shim;
- The runtime location pointer;
- The marked `PATH` block in `~/.zprofile`;
- The desktop shortcut;
- The installation source cache.

Until a verified uninstall workflow is published, keep the runtime or make a complete backup first, then review every write location in the installation summary individually.

## 17. Completion Checklist

- [ ] The Dashboard is reachable, and its URL matches the installation summary;
- [ ] The LLM Provider test passes;
- [ ] `open-nova doctor` reports no blocking errors;
- [ ] Scheduler status matches expectations;
- [ ] The first historical backfill is complete or observable;
- [ ] Diaries, AI Assets, and Nova-Task contain expected data;
- [ ] When `nova-RAG` is enabled, the Server, active index, and search work correctly;
- [ ] Log, update, backup, and uninstallation boundaries are understood.

## 18. Related References

- [English README](../README.md)
- [Chinese README](../README.zh-CN.md)
- [Chinese Local Operations Runbook](local-operations-runbook.zh-CN.md)
- [New User Onboarding Runbook](new-user-onboarding-runbook.md)
- [CLI Product Boundary](cli-boundary.md)
- [nova-RAG External Agent Runtime Contract](rag-external-agent-contract.md)
- [GitHub Releases](https://github.com/Neo-Isshin/open-nova/releases)

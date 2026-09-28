<p align="center">
  <a href="README.zh-CN.md">简体中文</a> · <strong>English</strong>
</p>

<h1 align="center">
  <img src="docs/assets/banner.png" alt="Actanara" width="680">
</h1>

<p align="center">
  <strong>Your agents do valuable work. Actanara makes sure it does not disappear with the session.</strong>
  <br>
  Consolidate sessions, tasks, and debugging evidence across <strong>Claude Code, Cursor, Codex, Gemini CLI, Antigravity, ZCode, Qwen Code, OpenClaw, OpenCode, Hermes, Copilot CLI, Cline, Continue, and Aider</strong> into persistent local knowledge and a living work graph.
</p>

<p align="center">
  <a href="https://neo-isshin.github.io/actanara/"><img src="https://img.shields.io/badge/Website-GitHub%20Pages-2563EB" alt="Website"></a>
  <a href="https://github.com/Neo-Isshin/actanara/releases/latest"><img src="https://img.shields.io/github/v/release/Neo-Isshin/actanara?display_name=tag&amp;sort=semver" alt="Latest stable Release"></a>
  <a href="https://neo-isshin.github.io/actanara/dashboard-demo/?lang=en#page-home"><img src="https://img.shields.io/badge/Demo-Interactive-7C3AED" alt="Interactive Dashboard Demo"></a>
  <img src="https://img.shields.io/badge/macOS-Supported-000000?logo=apple&amp;logoColor=white" alt="macOS Supported">
  <a href="#linux-support"><img src="https://img.shields.io/badge/Linux-Debian%20x64%20verified-FCC624?logo=linux&amp;logoColor=111827" alt="Linux Support: verified on Debian x86_64"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-16A34A" alt="License: MIT"></a>
  <a href="https://discord.gg/JvJHngZWz"><img src="https://img.shields.io/badge/Discord-Join-5865F2" alt="Discord"></a>
</p>

<p align="center">
  <a href="https://neo-isshin.github.io/actanara/dashboard-demo/?lang=en#page-home"><strong>▶️ Explore the Interactive Dashboard Demo</strong></a> ·
  <a href="#install-actanara"><strong>⚡ Quick Install</strong></a> ·
  <a href="#linux-support"><strong>🐧 Linux Support</strong></a> ·
  <a href="docs/local-operations-runbook.md"><strong>⚙️ Operations Runbook</strong></a>
</p>

<p align="center">
  <a href="https://neo-isshin.github.io/actanara/dashboard-demo/?lang=en#page-home">
    <img src="docs/assets/dashboard/dashboard-home.png" alt="Actanara Asset-First Alpine Observatory Dashboard" width="920">
  </a>
</p>

<p align="center"><sub><b>v1.9.0 Asset-First Alpine Observatory Dashboard · Sample data</b> · Click the image to enter the interactive demo</sub></p>

> **v1.9.0 Highlights**: Expands the Agent Runtime matrix (adding ZCode, Qwen Code, Copilot CLI, Cline, Continue, and Aider), introducing read-only source coverage, dialogue history, and an artifact browser. Actanara remains steadfastly **asset-first**: completed task outcomes, lessons learned, and reusable canonical skills sit prominently at the center. Token metrics stay organized under a dedicated **Usage & activity** view. Raw source logs never masquerade as independently verified tasks, ensuring your knowledge archive remains authentic. [Read full release notes](https://github.com/Neo-Isshin/actanara/releases/tag/v1.9.0).

---

## 💡 Why Actanara?

In modern software development, you likely rotate between multiple AI tools throughout a single day: **Claude Code, Cursor, Codex, Gemini CLI, Antigravity**, and others. While each agent performs substantive engineering, **the instant you close the terminal, that hard-won context vanishes**:

- **🧠 Context Fragmentation**: The elusive protocol bug you just diagnosed in Claude Code has to be re-explained from scratch when you open Codex.
- **⏳ Disappearing Evidence**: Midnight debugging traces, stack traces, and experimental patch diffs evaporate once the terminal scrollback is lost.
- **📋 Standup & Ticket Fatigue**: Agents quietly execute dozens of surgical bugfixes, yet at day's end, you still spend 30 minutes manually reconstructing standup notes, Jira tickets, and changelogs.

**Actanara solves this**: It quietly ingests and normalizes telemetry across all your local Agent Runtimes, distilling raw interactions into an **authentic, user-owned local fact database** for cross-agent recall, living task graphs, and automated work narratives.

| Dimension | Conventional (Session ends → Context lost) | With Actanara |
| :--- | :--- | :--- |
| **Multi-Agent Flow** | Agents operate in silos with zero mutual context | 🧠 **Shared Memory (`nova-RAG` / Memory Search)**: Allows Codex / Cursor to query past decisions and evidence from Claude Code |
| **Tasks & Evidence** | Relies on manually entered Jira tickets and todo lists | 🕸️ **Living Work Graph (`Nova-Task`)**: Auto-extracts hierarchical task trees from chats, diffs, and tool calls |
| **Lessons & Skills** | Bug resolutions vanish with terminal sessions | 📚 **Local AI Asset Repository**: Saves verified task outcomes, troubleshooting lessons, and reusable canonical skills |
| **Progress Reporting** | Painstaking manual review of terminal scrollbacks | 📝 **Automated Work Narratives**: Generates daily, weekly, and monthly diaries summarizing milestones and architectural shifts |
| **Privacy & Security** | Risk of leaking proprietary code to third-party clouds | 🔒 **100% Local-First**: All SQLite databases, Markdown reports, and vector indexes remain strictly on your local disk |

---

## 🧭 System Architecture & Data Flow

```text
 ┌─────────────────────────────────────────────────────────────────┐
 │       Supported 15+ Agent Runtimes (Local Sessions & Logs)      │
 └────────────────────────────────┬────────────────────────────────┘
                                  │ Attribution Parsers
                                  ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │               Foundation Local Fact Layer (SQLite)              │
 └──────────────┬─────────────────┬─────────────────┬──────────────┘
                │                 │                 │
                ▼                 ▼                 ▼
 ┌───────────────────────┐ ┌─────────────┐ ┌───────────────────────┐
 │ Base Pipeline (Diary) │ │ Nova-Task   │ │ Memory Search / RAG │
 └──────────────┬────────┘ └──────┬──────┘ └────────┬──────────────┘
                │                 │                 │
                └─────────────────┼─────────────────┘
                                  ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │            Actanara Alpine Observatory Web Dashboard            │
 └─────────────────────────────────────────────────────────────────┘
```

---

## 🌟 Four Core Pillars

### 1. 📋 Nova-Task: A Living Graph of Real Work

`Nova-Task` is much more than another to-do list. Valuable engineering rarely starts with a pristine ticket; it emerges organically through investigation, trial, rollback, and verification. Nova-Task translates those real-world traces into an auditable, maintainable task structure.

- **Automated Maintenance & Reconciliation**: Detects hierarchy, updates task status, attaches subtasks, and prunes the task tree. High-impact primary nodes retain human review, while routine updates happen under deterministic rules.
- **Decompose PRDs and RFCs**: Import an RFC, PRD, or roadmap, and Actanara can guide an LLM to decompose it into an actionable, verifiable engineering breakdown. See [Nova-Task Work-Graph Reconciliation](docs/nova-task-work-graph-reconciliation.md).

### 2. 🔎 Memory Search: Instant Recall Even Without RAG

`actanara search` defaults to `--mode auto`. It runs instantly out-of-the-box with zero complex model setup:

- **Lexical Fallback (SQLite FTS5)**: When RAG is disabled or unavailable, Actanara automatically queries an incrementally maintained SQLite full-text index with sub-millisecond response times and zero API costs.
- **Dynamic Read-Only Skill**: The installer provisions a single dynamic, read-only Memory Search Skill. External agents read the response metadata to automatically adopt the appropriate lexical or semantic protocol.
- **Native Memory Coexistence**: Automatically indexes memory directories managed by Codex and Claude Code, along with allowlisted instruction files. See [Memory Search and Local Recall](docs/memory-search.md).

### 3. 🤖 nova-RAG: Guarded Cross-Agent Semantic Recall

`nova-RAG` is Actanara's optional semantic retrieval subsystem supporting local CPU-only embedding models or cloud embeddings:

- **Strict Read-Only Boundary**: Grants external agent runtimes **read-only** query access. They can retrieve past engineering context, but **cannot write memory, mutate indexes, alter settings, or manipulate service lifecycles**.
- **Two-Tier Adaptive Retrieval**: Runs a deterministic baseline pass on the server side; only when retrieved evidence is weak or ambiguous does it ask the external agent's LLM to reflect further, preventing hallucinations. See the [nova-RAG External Agent Runtime Contract](docs/rag-external-agent-contract.md).

### 4. 📦 Asset-First Alpine Observatory Dashboard

In v1.8.0+ and v1.9.0, Actanara delivers a focused **Alpine Observatory** interface:

- **Curated Knowledge Assets**: Review completed task outcomes, distilled lessons, reusable skills, and period reports in one cohesive view.
- **Dedicated Usage & Activity**: Cumulative token volumes, message frequencies, model distribution charts, and 30-day activity trends live in a dedicated **Usage & activity** tab, scientific excluding prompt cache duplicates.
- **Interactive Experience**: Explore locally or preview the live interface anytime via the [Interactive Dashboard Demo](https://neo-isshin.github.io/actanara/dashboard-demo/?lang=en#page-home).

---

<a id="install-actanara"></a>
## ⚡ Quick Install

### 1. One-Line Install

Run the official public installer in your macOS or Linux terminal (no `sudo` required):

```bash
curl -fsSL https://github.com/Neo-Isshin/actanara/releases/latest/download/install.sh | sh
```

This is the unified entrypoint for both macOS and Linux. GitHub provides this installer from the latest immutable release, pinning builds to that release's exact source commit before delegating to the appropriate platform adapter. macOS maintains its guided setup and upgrade flow; Linux supports guarded fresh installs, source-only refreshes, lockfile-pinned upgrades, and explicit repairs. Linux update transactions preserve systemd user unit states, failing safely when definitions drift or encounter external units. Not ready to install? Try the [Interactive Dashboard Demo](https://neo-isshin.github.io/actanara/dashboard-demo/?lang=en#page-home) first.

When the Linux public installer encounters an existing managed Runtime: an interactive terminal will display the commit-pinned upgrade plan before prompting to proceed; a non-interactive shell exits with status 2 without mutating the runtime, outputting exact `actanara update --dry-run` and `actanara update --apply` commands containing resolved source URLs, commits, runtime paths, and installer cache.

The stable CLI shim is located at `~/.actanara/bin/actanara`, with an optional symlink at `~/.local/bin/actanara`. On macOS, use `--no-shell-path` or `--shell-path-file /path/to/profile` to manage shell profile integration; on Linux, `--no-shell-path` suppresses the user-bin link without editing shell profiles. For advanced source builds, supply `--source-root PATH` or an explicit `--ref <full-commit-sha>`. Offline workflows must explicitly specify one of these sources.

For installer paths and launchd/systemd registration boundaries, see the [Local Operations Runbook](docs/local-operations-runbook.md).

### 2. Verify and Launch

After installation completes, verify your environment with read-only inspection commands:

```bash
actanara doctor
actanara onboard status
```

Open the Dashboard in your browser (default: `http://127.0.0.1:3036/dashboard`):
1. **Configure Provider**: Under Settings, configure your preferred LLM Provider and API Key (OpenAI, Anthropic, Gemini, or compatible proxy endpoints) and run the connection test.
2. **Review Ingested Assets**: Actanara scans local sessions and automatically populates your daily diaries, Nova-Task work graph, and knowledge assets.

<details>
<summary><strong>🛠️ Essential CLI Cheat Sheet</strong></summary>

```bash
# Search memory across agents (prefers nova-RAG if ready, otherwise uses local lexical index)
actanara search "redis connection timeout fix" --top-k 5

# Force a specific retrieval mode
actanara search "deployment failure" --mode rag --json
actanara search "deployment failure" --mode local --json

# Inspect or rebuild the local SQLite full-text index
actanara memory status
actanara memory sync
actanara memory rebuild

# Manually trigger daily pipeline generation
actanara pipeline
actanara pipeline 2026-07-12

# Inspect updates or apply upgrades
actanara update --dry-run
actanara update --apply
```

The updater reuses virtual environments when dependencies match or rebuilds from hash-pinned locks; see the Runbook for details on `--source-only/--force-rebuild/--offline`, source fetching, and commit pinning. Actanara does not provide an unvetted one-click uninstaller; do not delete `~/.actanara` directly without following the documented uninstallation boundaries in the Runbook.

When explicitly providing `--source-url` or `--ref` on Linux, the adjacent bootstrap file serves purely as an execution entrypoint and is never mistaken for chosen source. The installer cache validates normalized Git `origin` remotes and fetched/cached commit hashes in both online and offline modes prior to execution.

Standard Linux updates require managed systemd unit definitions to match upstream configurations, preserving each unit's active/enabled state. If trusted runtime configuration drifts, execute the explicitly confirmed repair workflow described in the Runbook; repairs never claim or delete user-owned units.

</details>

---

<a id="linux-support"></a>
## 🐧 Linux Support

Actanara provides a first-class, non-root Linux implementation built natively for `systemd --user` Debian distributions—not an emulation layer. Our release verification runs against Debian 13 x86_64, CPython 3.13, and systemd 257. Lockfiles support both x86_64 and arm64 targets, with functional release verification executed on x86_64.

| Capability | Linux Production Behavior |
| :--- | :--- |
| **Install & Upgrades** | The public Release `install.sh` supports verified fresh installations, commit/source refreshes, lock-pinned dependency updates, and atomic repairs. New generations are staged before atomic promotion; failed updates roll back instantly. |
| **Service Boundaries** | Run setup as a standard login user; **never execute with `sudo`**. Dashboard, optional `nova-RAG`, and scheduled background jobs run under `systemctl --user`. |
| **RAG Profile** | Fresh installations support an audited CPU-only local embedding profile (powered by `intfloat/multilingual-e5-small`). Cloud RAG conservatively declines when credentials are missing. |
| **Headless Servers** | Dashboard and RAG bind strictly to loopback (`127.0.0.1:3036` and `3037`). Headless servers can be accessed securely via SSH local port forwarding: |

```bash
# Port forward to a remote headless server
ssh -N -L 3036:127.0.0.1:3036 user@linux-host
# Open http://127.0.0.1:3036/dashboard in your local browser
```

---

## 🔐 Privacy & Security Commitments

- **100% Local-First**: Runtime state, SQLite databases, generated Markdown diaries, and vector embeddings reside exclusively on your local filesystem.
- **Hardened Secret Permissions**: Provider API keys reside in `$ACTANARA_HOME/state/secrets`, guarded by POSIX mode `0700` for directories and `0600` for secret files.
- **Non-Invasive Architecture**: Actanara reads telemetry already written to disk. **It never modifies external runtime history, nor does it hijack third-party agent processes.**
- **Loopback-Only External Boundary**: The generic external recall facade at `/api/memory/external/*` is restricted strictly to loopback connections, adhering to strict allowlisted file access.

---

## 📐 Development, Testing, and Reproducible Releases

<details>
<summary><strong>Expand development and test commands</strong></summary>

Create a local editable development environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dashboard,rag-local]"
```

Run the release suite with isolated virtual environment, `HOME`, `ACTANARA_HOME`, and a fixed business clock:

```bash
python tests/run_isolated_release_suite.py
```

Run deterministic frontend and Release Page tests:

```bash
npm ci
node --check src/dashboard/app/static/js/app.js
npm run test:dashboard-live-context
npm run test:release-page
```

Reproduce release artifacts for the current checkout:

```bash
python -B -m pip install -r requirements-release.txt
PROJECT_VERSION="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)" \
python -B -m tools.release.build_release \
  --source-root . \
  --output-dir ../actanara-release-artifacts \
  --expected-commit "$(git rev-parse HEAD)" \
  --expected-version "$PROJECT_VERSION"
```

The release builder accepts only a clean, committed Git worktree and writes output outside the repository. Artifacts include source and runtime-payload manifests, a normalized runtime archive, wheel, sdist, provenance, and `SHA256SUMS`.

</details>

---

<a id="documentation"></a>
## 📚 Documentation

### User & Daily Operations
- ⚙️ [Local Operations Runbook](docs/local-operations-runbook.md) — Operational parameters, systemd/LaunchAgent administration, and troubleshooting
- 📖 [New User Onboarding Runbook](docs/new-user-onboarding-runbook.md) — Step-by-step LLM configuration and historical backfill guide
- 🧭 [CLI Product Boundary](docs/cli-boundary.md) — Command interfaces and system boundary definitions
- 🔎 [Memory Search and Local Recall](docs/memory-search.md) — Hybrid lexical and semantic retrieval design

### Integration & Product Architecture
- 🤖 [nova-RAG External Agent Runtime Contract](docs/rag-external-agent-contract.md) — API schema and read-only protocol specification
- 🧩 [Nova-Task Work-Graph Reconciliation](docs/nova-task-work-graph-reconciliation.md) — Automated task synthesis and reconciliation principles

### Quality Assurance & Project History
- ✅ [Release Assurance Archive](docs/v1-release-assurance.md) · 🧹 [Production Cleanup Inventory](docs/production-clean-inventory.md)
- 🧾 [Changelog](CHANGELOG.md) · 🔐 [Security Policy](SECURITY.md) · 🕰️ [Public Project History](HISTORY.md)

---

## ⚖️ License & Acknowledgements

Copyright © 2026 Neo-Isshin.

Actanara is licensed under the [MIT License](LICENSE), with SPDX identifier `MIT`.

- Deep gratitude to all leading AI coding tools (Claude Code, Cursor, Codex, Gemini CLI, Antigravity, OpenClaw, etc.) whose local telemetry standards make unified asset consolidation possible.
- Thanks to the [getdesign.md](https://getdesign.md) community for visual inspiration across the Dashboard experience.

<hr>

<a id="give-star"></a>
<div align="center">

<h2>⭐ Give me a Star</h2>

<p>
If Actanara helps you turn fragmented AI interactions into permanent, searchable engineering assets,<br>
please consider starring our repository on GitHub!
</p>

<a href="https://github.com/Neo-Isshin/actanara">
  <img src="https://img.shields.io/github/stars/Neo-Isshin/actanara?style=for-the-badge&amp;logo=github&amp;label=Give%20me%20a%20Star&amp;color=F5B942" alt="Give Actanara a Star">
</a>

</div>

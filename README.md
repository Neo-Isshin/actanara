<h1 align="center">
  <img src="docs/assets/banner.png" alt="Open Nova" width="650">
</h1>

<p align="center">
  <strong>Share memory across agent runtimes and turn siloed activity into searchable, reusable local AI assets.</strong>
  <br>
  Highly Automated AI Asset Operations · Cross-Runtime Memory Sharing · Deep LLM Involvement
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Language-English%20·%20Current-2563EB?style=for-the-badge" alt="Current language: English">
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Language-简体中文-C026D3?style=for-the-badge" alt="切换到简体中文 README"></a>
</p>

<p align="center">
  <a href="https://neo-isshin.github.io/open-nova/"><img src="https://img.shields.io/badge/Website-GitHub%20Pages-2563EB" alt="Website"></a>
  <a href="https://github.com/Neo-Isshin/open-nova/releases/tag/v1.0.1"><img src="https://img.shields.io/badge/Release-v1.0.1-0EA5E9" alt="Release v1.0.1"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/Install-pinned%20v1.0.1-0284C7" alt="Pinned v1.0.1 install command"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPL--3.0--or--later-16A34A" alt="License"></a>
  <a href="https://discord.gg/JvJHngZWz"><img src="https://img.shields.io/badge/Discord-Join-5865F2" alt="Discord"></a>
</p>

<p align="center">
  <a href="https://neo-isshin.github.io/open-nova/">Release Website</a> ·
  <a href="https://neo-isshin.github.io/open-nova/dashboard-demo/"><strong>Real Dashboard Static Demo</strong></a> ·
  <a href="docs/local-operations-runbook.md"><strong>Local Operations Runbook</strong></a> ·
  <a href="docs/rag-external-agent-contract.md">nova-RAG External Contract</a>
</p>

Open Nova is a highly automated, structured, local-first AI asset operations system. It organizes sessions, tasks, usage, and work traces from supported agent runtimes, turning fragmented activity into unified local data, diaries, task evidence, and searchable memory.

Open Nova is also a system with **deep LLM involvement**: LLMs participate in summarization, task extraction, learning-asset generation, and knowledge organization, while deterministic components control data collection, parsing, attribution, scheduling, persistence, and security boundaries.

> In this README, an **agent runtime** means an AI tool environment with its own sessions, logs, memory, and execution context, such as Codex, Claude Code, Gemini CLI, OpenClaw, or Hermes.

<a id="why-open-nova"></a>
## 🌟 Why Open Nova

A single user may switch among several agent runtimes within the same week—or even within the same project. Each tool has its own logs, sessions, skills, memory, token usage, and task history. These records often capture real work, yet remain isolated from one another.

Open Nova is designed to break down those barriers: it lets `Codex` find work already completed in `Claude Code`, unifies activity from different runtimes into summaries and dashboards, and preserves deliverables, resolved obstacles, and debugging evidence beyond the life of an individual session.

You can use it to:

- 🤖 **Share memory across runtimes:** Convert sessions, tasks, and notes from supported runtimes into structured evidence. With `nova-RAG` enabled, external runtimes can retrieve that memory through a read-only contract.
- 📓 **Automatically recognize and persist tasks** (Beta): Extract candidate tasks, evidence, and status from real activity and tool results, then organize and review them in `Nova-Task`.
- 🌍 **Automatically generate daily, weekly, and monthly summaries:** Review progress, AI asset growth, and token usage in the Dashboard.
- 📖 **Improve alongside your agents:** Accumulate reusable learning assets from challenges, solutions, and practical recommendations.
- 🚉 **Manage supported runtimes in one place:** Review activity, usage, and runtime status, and inspect or edit supported runtimes' `SKILL.md` files.

## 📚 Contents

- [Key Advantages](#core-advantages) · [How It Works and System Components](#how-it-works) · [Support](#support)
- [Quick Start](#quick-start) · [Dashboard, Screenshots, and Interactive Demo](#dashboard) · [Nova-Task](#nova-task)
- [nova-RAG](#nova-rag) · [Privacy and Security](#privacy-security) · [Development and Testing](#development)
- [Documentation](#documentation) · [License](#license) · [Give me a Star](#give-star)

<a id="core-advantages"></a>
## 💫 Key Advantages

- **Parser-first processing:** Source-specific parsers normalize sessions, tasks, usage, scheduled activity, and workspace signals before data enters summarization, task-evidence, or RAG workflows. Raw, unprocessed logs are not handed directly to an LLM.
- **Reliable workspace attribution:** Project context is not inferred solely from the current shell directory. Scheduled jobs, background scripts, and out-of-directory runtime activity can still be associated with the correct workspace through execution evidence.
- **Task evidence grounded in real work:** `Nova-Task` evaluates tool results and delivery evidence as well as conversations when determining task status, keeping the board closer to the engineering work that actually occurred.
- **Local-first with explicit boundaries:** Open Nova reads configured tool locations and writes to its own runtime home. It does not rewrite external-runtime history or take over runtime execution.
- **Model-cost efficient:** Structured prompts, explicit schemas, and controlled orchestration let lightweight or cost-efficient models produce useful output without locking the system to one provider.
- **User-controlled integrations:** Tool skills, external-runtime definitions, and critical settings remain visible, editable, and auditable instead of implicitly taking over the global toolchain.
- **Protected Agentic RAG lifecycle:** `nova-RAG` manages retrieval quality through evaluation queries, candidate promotion, recall calibration, and safe rollback, while exposing only a restricted read-only contract to external runtimes.

<a id="how-it-works"></a>
## 🧭 How It Works

```text
Supported Agent Runtimes
        ↓
Parsing, Attribution, and Normalization
        ↓
Foundation Local Fact Layer
        ↓
Base Pipeline · Nova-Task · Dashboard
        ↓
nova-RAG (optional) → Read-only Retrieval for External Runtimes
```

| System | Core responsibility |
| :--- | :--- |
| **`Foundation`** | Normalizes AI activity, workspace attribution, snapshots, reports, task evidence, and repair records into a local fact layer. |
| **`Base Pipeline`** | Generates narrative diaries, technical progress, learning records, and task summaries from runtime activity. |
| **`Dashboard`** | Presents diaries, AI assets, token usage, settings, Foundation operations, background tasks, and task boards in one place. |
| **`Nova-Task`** | Maintains a reviewable task graph based on evidence from real work. |
| **`nova-RAG`** | Optional local- or cloud-embedding retrieval subsystem with a protected index lifecycle and external read-only retrieval. |
| **Attribution Parsers** | Identify runtimes, sessions, workspaces, scheduled jobs, usage events, and execution evidence, including work launched outside project directories. |
| **Installer** | Handles dependency checks, runtime initialization, macOS LaunchAgents, Doctor diagnostics, and protected update transactions. |

Together, the local fact layer, Pipeline, task system, Dashboard, and optional retrieval subsystem deliver these capabilities.

<a id="support"></a>
## 💻 Support and Prerequisites

The hosted Open Nova v1.0.x installation path is designed first for local macOS user environments:

- 🍎 **macOS is the first-class target:** Guided installation, Dashboard services, and managed scheduling use user-level `LaunchAgent` services by default.
- 🛠️ **Base tools:** Verify that `zsh`, `git`, and `curl` are available before installation. `sudo` is not required.
- 🐍 **Python:** Python `>=3.11` is required. On supported Apple Silicon and Intel Macs, the installer downloads and verifies a managed Python when no compatible version is available.
- 🌐 **Network and storage:** Installation needs access to GitHub, the Python package index, and your selected model services. The first local `nova-RAG` run may download `torch`, `sentence-transformers`, and model weights.
- 🐧 **Linux and Windows:** They are not first-class targets for the v1.0.x one-liner or managed services. Advanced users can run some components manually from source.

### Currently Supported Agent Runtimes

| Runtime | v1.0.x status |
| :--- | :--- |
| 🦞 **OpenClaw** | Supported external-tool path family |
| ✳️ **Claude Code** | Supported external-tool path family |
| 🤖 **Codex** | Supported external-tool path family |
| ✨ **Gemini CLI** | Supported external-tool path family |
| ⚕️ **Hermes** | Supported external-tool path family |

The available data depends on whether compatible logs, sessions, or usage records exist locally and whether their paths are enabled in Settings. Additional runtimes and broader cross-platform support belong to future releases and should not be inferred as v1.0.x capabilities.

<a id="quick-start"></a>
## 🎥 Quick Start

> [!TIP]
> **Deploy with one command, then let prosperity follow.**

### 1. Install v1.0.1

The following one-liner pins both the `v1.0.1` bootstrap and the exact source commit used for installation. It does not track `main` or a future `latest` Release:

```bash
bootstrap="$(curl -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 --max-time 30 'https://raw.githubusercontent.com/Neo-Isshin/open-nova/v1.0.1/install/bootstrap.sh')" && [ -n "$bootstrap" ] && NOVA_INSTALL_SOURCE_URL='https://github.com/Neo-Isshin/open-nova.git' NOVA_INSTALL_REF='82bbdbd83e35724441c7005dfc0b555d413fcf93' zsh -c "$bootstrap"
```

> [!NOTE]
> This README deliberately uses a strictly pinned v1.0.1 installation. Supplying the explicit commit bypasses the bootstrap's future dynamic checks for the `latest` Release and its `WITHDRAWN` marker, thereby keeping the Open Nova source fixed instead of allowing it to drift with `latest`. Third-party dependencies are still resolved at install time according to the release configuration, so this does not promise byte-for-byte reproducibility of the entire dependency environment. This choice does not modify the published v1.0.1 tag or Release.

> [!IMPORTANT]
> This command is for a fresh installation only. If the bootstrap detects an existing Open Nova runtime, an active runtime pointer, or a managed LaunchAgent, it stops safely before writing to the source cache. For an existing installation, run `open-nova update` or `open-nova update --dry-run` to review the plan, then use `open-nova update --apply` to perform the update.

> [!WARNING]
> `v1.0.0` has been withdrawn: its update transaction could leave managed services bound to an old concrete source directory. Its immutable tag and artifacts remain available for audit only. Do not install or recommend it.

#### Installer Write Locations

| Path | Purpose |
| :--- | :--- |
| `~/.cache/open-nova/installer` | Installation source cache |
| `~/.open-nova` | Runtime, virtual environment, settings, database, logs, secrets, and generated assets |
| `~/.config/open-nova/location.json` | Active runtime pointer |
| `~/.local/bin/open-nova` | User-facing CLI entry on `PATH` |
| `~/.zprofile` | Receives a marked `PATH` block by default; disable with `--no-shell-path` |
| `~/Desktop/Open Nova` | Desktop shortcut to the diary directory, created by default |
| `~/Library/LaunchAgents/` | User-level macOS Dashboard, Scheduler, and optional RAG services |

The default shell-profile update can be disabled with `--no-shell-path`, or redirected explicitly with `--shell-path-file /path/to/profile`.

When `nova-RAG` is enabled and external agent runtimes are selected in the wizard, the installer can also register missing read-only retrieval skills. Existing skills are never overwritten implicitly.

### 2. Basic Verification

After installation, run these read-only commands first. They do not initialize a new runtime or change existing settings:

```bash
open-nova doctor
open-nova model show
open-nova onboard status
open-nova config show
```

For targeted diagnostics:

```bash
open-nova doctor --installer
open-nova doctor --pipeline
open-nova doctor --scheduler
open-nova doctor --rag
```

The installation summary displays the actual Dashboard URL. The default is `http://127.0.0.1:3036/dashboard`; if that port is occupied, use the automatically selected address shown in the summary.

### 3. Complete the First Run

1. **Open the Dashboard:** Use the URL in the installation summary and check the background-task and message indicators in the upper-right corner.
2. **Configure the LLM Provider:** Verify the Provider, Endpoint, Model, and API Key. Run the availability test before saving.
3. **Preview the historical-data plan:** Select a date range and review pending diaries, weekly and monthly reports, estimated LLM calls, and optional RAG tasks.
4. **Queue generation:** Clear any tasks you do not need, then add the remaining work to the background queue.
5. **Review results:** Monitor progress in Background Tasks and Messages. When complete, refresh Diary, AI Assets, Nova-Task, and optional `nova-RAG` views.

> [!NOTE]
> Historical-data generation runs in the background. Large date ranges can take longer; days without activity may produce only structured placeholder artifacts and may not call an LLM.

<details>
<summary><strong>Expand the first-run checklist</strong></summary>

- [ ] The Dashboard opens successfully.
- [ ] The LLM Provider test passes and the settings are saved.
- [ ] `open-nova doctor` reports no blocking errors.
- [ ] The history plan and selected tasks match expectations.
- [ ] The first tasks have completed or are observable in the background.
- [ ] Diary, AI Assets, and Nova-Task contain data.
- [ ] When `nova-RAG` is enabled, the Server and active index are ready.

</details>

Open Nova v1.0.1 targets a local macOS runtime. For complete pre-install checks, first-run setup, historical backfill, daily Pipeline, Dashboard / Nova-Task / nova-RAG operations, updates, and troubleshooting, see the [Local Operations Runbook](docs/local-operations-runbook.md).

<a id="dashboard"></a>
## 📊 Dashboard, Screenshots, and Interactive Demo

The Dashboard is Open Nova's primary operating surface. It includes:

- 📅 Daily, weekly, and monthly diaries;
- 📈 Live overview, token usage, and AI asset metrics;
- 🔧 Foundation operations, Daily QA, and data repair;
- ✉️ Background tasks and messages;
- ⚙️ LLM Provider, scheduling, runtime, and external-tool settings;
- 📋 Nova-Task board and evidence review;
- 🔍 Semantic search and retrieval-quality views when RAG is enabled.

### 🖼️ Real Dashboard Screenshots

The images below come from the real Open Nova Dashboard during development and operation. They preserve the project's own design, layout, typography, and components; they are neither redrawn mockups nor marketing illustrations from the release website. **nova-RAG v2** in a screenshot refers to the index and retrieval generation of the RAG subsystem, not an Open Nova v2 product release. The current product version remains `v1.0.1`.

<details>
<summary><strong>Expand the real Dashboard home screenshot</strong></summary>

<p align="center">
  <a href="docs/assets/dashboard/dashboard-home.png">
    <img src="docs/assets/dashboard/dashboard-home.png" alt="Open Nova Dashboard home" width="100%">
  </a>
</p>

<p align="center"><sub>Open Nova Dashboard home; select the image to view it at full size.</sub></p>

</details>

<details>
<summary><strong>Expand the real W27 weekly report screenshot</strong></summary>

<p align="center">
  <a href="docs/assets/dashboard/dashboard-weekly-full.png">
    <img src="docs/assets/dashboard/dashboard-weekly-overview.png" alt="Open Nova Dashboard W27 weekly report overview" width="100%">
  </a>
</p>

<p align="center"><sub>W27 example report; select the image to view the complete long-form report.</sub></p>

</details>

<details>
<summary><strong>Expand the real AI Assets screenshot</strong></summary>

<p align="center">
  <a href="docs/assets/dashboard/dashboard-ai-assets-long.png">
    <img src="docs/assets/dashboard/dashboard-ai-assets-overview.png" alt="Open Nova Dashboard AI Assets overview" width="100%">
  </a>
</p>

<p align="center"><sub>Select the image to view the complete AI Assets page.</sub></p>

</details>

<details>
<summary><strong>Expand the real Nova-Task work graph</strong></summary>

<p align="center">
  <a href="docs/assets/dashboard/dashboard-nova-task.png">
    <img src="docs/assets/dashboard/dashboard-nova-task.png" alt="Real Open Nova Nova-Task work graph" width="100%">
  </a>
</p>

</details>

<details>
<summary><strong>Expand the nova-RAG status and retrieval interface</strong></summary>

<p align="center">
  <a href="docs/assets/dashboard/dashboard-nova-rag.png">
    <img src="docs/assets/dashboard/dashboard-nova-rag.png" alt="Open Nova nova-RAG status and retrieval interface" width="100%">
  </a>
</p>

</details>

### ▶️ Real Static Interactive Demo

The [Dashboard Static Demo](https://neo-isshin.github.io/open-nova/dashboard-demo/) preserves the real Dashboard HTML, CSS, components, layout, and interaction code, replacing only backend APIs with fixed static data. It never connects to or modifies a local Open Nova runtime. The published demo dataset contains only the live overview, AI Assets, Nova-Task board, one W27 weekly report, two ordinary diaries, and one Blank Day diary. To fully demonstrate the weekly-report components, clearly identified display data was added to the W27 metrics based on the fields and approximate scale visible in the existing real screenshot; those values do not represent statistics from a specific real run.

<p align="center">
  ▶ <a href="https://neo-isshin.github.io/open-nova/dashboard-demo/"><strong>Open the Real Dashboard Static Demo</strong></a>
</p>

The version-controlled static snapshot is available at [`docs/dashboard-demo/index.html`](docs/dashboard-demo/index.html). The [release website](https://neo-isshin.github.io/open-nova/) remains the product and installation overview rather than the interactive Dashboard itself.

### Runtime Layout

| Default path | Purpose |
| :--- | :--- |
| `~/.open-nova` | Main runtime home |
| `~/.config/open-nova/location.json` | Active runtime pointer |
| `~/.open-nova/config/settings.json` | Runtime settings |
| `~/.open-nova/data/nova_data.sqlite3` | Foundation SQLite database |
| `~/.open-nova/state/secrets` | LLM and optional cloud-embedding provider keys |
| `~/.open-nova/artifacts/diary` | Diaries and summaries |
| `~/.open-nova/artifacts/reports` | Report output |
| `~/.open-nova/bin/open-nova` | Runtime-local CLI shim |

Runtime databases, diaries, reports, logs, caches, secrets, and local LaunchAgent artifacts must not be committed to the source repository.

### Common Commands

Search local memory through `nova-RAG`:

```bash
open-nova search "deployment issue" --top-k 5
open-nova search "deployment issue" --top-k 5 --json
```

This command uses the Dashboard's read-only external retrieval API. Automation consuming JSON output should check the `available` field; when RAG is unavailable, the command may still return a successful structured status response.

Run the daily Pipeline manually:

```bash
open-nova pipeline
open-nova pipeline 2026-07-12
```

Without a date, the Pipeline processes the previous calendar day in the configured time zone. It writes diaries, reports, and Foundation data. If the target date has already been generated completely, regeneration from the frozen Foundation input requires an explicit `--force`.

Review or apply an update:

```bash
open-nova update
open-nova update --dry-run
open-nova update --apply
```

- `open-nova update` only displays the update plan.
- `--dry-run` runs a no-change bootstrap and installer preview. With a cold cache, it mainly shows the source-acquisition plan and is not a complete candidate-version E2E validation.
- Only `--apply` executes the protected update transaction.

Open Nova v1.0.1 does not yet include a product-level one-command uninstaller. Do not remove only `~/.open-nova`; doing so leaves LaunchAgents, the CLI shim, runtime pointer, shell `PATH` block, desktop shortcut, and installation cache behind.

<a id="nova-task"></a>
## 📋 Nova-Task: A Graph of Real Work

`Nova-Task` is more than another to-do list. It is designed to record engineering work that actually happened, based on conversations, file changes, tool results, and execution evidence.

It is a **graph of real work**: much valuable work does not begin with a formal ticket but grows naturally through discussion, investigation, repair, experimentation, rollback, and verification. `Nova-Task` converts those traces into a reviewable, maintainable task structure.

In automatic-maintenance mode, `Nova-Task` can detect hierarchy, update status, attach subtasks, and refine the task tree. High-impact top-level nodes retain human review, while routine second- and third-level updates can proceed under configured rules. A person can take over at any time.

After an RFC, PRD, Roadmap, or Audit document is imported, Open Nova can also ask an LLM to decompose it into an iterative `Nova-Task` tree for review and maintenance.

<a id="nova-rag"></a>
## 🤖 nova-RAG: Shared Memory with a Read-Only Boundary

`nova-RAG` is Open Nova's optional retrieval subsystem with local or cloud embeddings. It gives external agent runtimes read-only access to a user's work memory while refusing memory writes, index changes, global-setting changes, or service-lifecycle control.

Retrieval quality is managed at two levels:

- **Server-side Agentic:** A deterministic, low-cost, baseline-first adaptive retrieval pass.
- **Skill-side Agentic:** The external runtime's own LLM reflects further only when the server returns weak or ambiguous evidence.

`nova-RAG` also manages recall quality through evaluation queries, candidate promotion, a protected index lifecycle, and safe rollback paths.

<details>
<summary><strong>Expand the external read-only API overview</strong></summary>

Prefer the Dashboard facade (default `http://127.0.0.1:3036`):

```text
GET  /api/rag/external/health
GET  /api/rag/external/stats
GET  /api/rag/external/contract
POST /api/rag/external/search
```

Direct nova-RAG service (default `http://127.0.0.1:3037`):

```text
GET  /health
GET  /stats
POST /search
```

The current runtime settings determine the actual host and ports. `POST /encode` is for internal embedding computation and is not part of the external-runtime contract.

</details>

For the complete security boundary, request schema, and error semantics, see the [nova-RAG External Agent Runtime Contract](docs/rag-external-agent-contract.md).

<a id="privacy-security"></a>
## 🔐 Privacy and Security

- **Local-first:** Runtime state, the Foundation database, generated assets, and indexes remain in user-owned local paths.
- **Secret permissions:** Provider keys live in `$NOVA_HOME/state/secrets`; the directory uses mode `0700`, and secret files use mode `0600`.
- **Keychain migration:** Legacy `macos-keychain` references are used only for compatibility migration. Readable legacy secrets are copied to the runtime secret store; Open Nova does not automatically delete old Keychain items.
- **External-provider boundary:** If an external LLM or embedding provider is configured, relevant derived work content is sent according to the selected endpoint and provider data policy.
- **Input content:** If source logs, diaries, or user-selected material already contain secrets or sensitive information, generated diaries, reports, snapshots, and indexes may faithfully preserve that content.
- **Non-invasive boundary:** Open Nova does not rewrite supported runtimes' historical data or take over their execution. It creates its own runtime, CLI shim, optional skills, and managed services.

<a id="development"></a>
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

Run the release suite with isolated virtual environment, `HOME`, `NOVA_HOME`, and a fixed business clock:

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

`npm run test:dashboard-live` is an explicit opt-in gate against a real Dashboard and may mutate the runtime. Run it only against a seeded, disposable runtime.

Reproduce v1.0.1 release artifacts:

```bash
python -B -m pip install -r requirements-release.txt
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)" \
python -B -m tools.release.build_release \
  --source-root . \
  --output-dir ../open-nova-release-artifacts \
  --expected-commit "$(git rev-parse HEAD)" \
  --expected-version 1.0.1
```

The release builder accepts only a clean, committed Git worktree and writes output outside the repository. Artifacts include public-source and runtime-payload manifests, a normalized runtime archive, wheel, sdist, provenance, and `SHA256SUMS`.

</details>

<a id="documentation"></a>
## 📄 Documentation

### User and Daily Operations

- ⚙️ [English Local Operations Runbook](docs/local-operations-runbook.md)
- 🇨🇳 [Chinese Local Operations Runbook](docs/local-operations-runbook.zh-CN.md)
- 📖 [New User Onboarding Runbook](docs/new-user-onboarding-runbook.md)
- 🧭 [CLI Product Boundary](docs/cli-boundary.md)

### Integration and Product Design

- 🤖 [nova-RAG External Agent Runtime Contract](docs/rag-external-agent-contract.md)
- 🧩 [Nova-Task Work-Graph Reconciliation](docs/nova-task-work-graph-reconciliation.md)

### Release, Security, and Project History

- ✅ [v1.0.1 Release Assurance](docs/v1-release-assurance.md)
- 🧹 [Production Cleanup Inventory](docs/production-clean-inventory.md)
- 🧾 [Changelog](CHANGELOG.md)
- 🔐 [Security Policy](SECURITY.md)
- 🕰️ [Public Project History](HISTORY.md)

<a id="license"></a>
## ⚖️ License

Copyright © 2026 Neo-Isshin.

Open Nova is free software licensed under the [GNU General Public License, version 3 or any later version](LICENSE), with SPDX identifier `GPL-3.0-or-later`.

## 🙏 Acknowledgements

Open Nova exists thanks to outstanding AI coding tools and their open-source communities. Their local activity and token-usage logs make unified visualization, asset consolidation, and cross-runtime memory sharing possible.

Thanks also to the [getdesign.md](https://getdesign.md) community for inspiration on the Dashboard's layout and visual direction.

<hr>

<a id="give-star"></a>
<div align="center">

<h2>⭐ Give me a Star</h2>

<p>
If Open Nova helps you turn fragmented AI work into searchable, reusable local assets,<br>
please give it a Star so more people can discover the project.
</p>

<a href="https://github.com/Neo-Isshin/open-nova">
  <img src="https://img.shields.io/github/stars/Neo-Isshin/open-nova?style=for-the-badge&amp;logo=github&amp;label=Give%20me%20a%20Star&amp;color=F5B942" alt="Give Open Nova a Star">
</a>

</div>

<div align="center">

<img src="docs/assets/banner.png" alt="Open Nova" width="650">

### 跨多 Agent runtime 共享记忆，将相互独立的Agent活动统一整合沉淀为可检索、可复用的本地 AI 资产。

**全自动 AI 资产运维 · 跨 Agent runtime 共享记忆 · LLM深度参与**

[![官网](https://img.shields.io/badge/Website-release%20page-2563EB)](https://neo-isshin.github.io/open-nova/)
[![一行安装](https://img.shields.io/badge/One--liner-GitHub-0EA5E9)](https://raw.githubusercontent.com/Neo-Isshin/open-nova/v1.0.0/install/bootstrap.sh)
[![文档](https://img.shields.io/badge/Docs-release%20page-1D4ED8)](https://neo-isshin.github.io/open-nova/)
[![中文](https://img.shields.io/badge/Lang-中文-2563EB)](README.zh-CN.md)
[![English](https://img.shields.io/badge/Lang-English-64748B)](README.md)
[![Discord](https://img.shields.io/badge/Discord-加入-5865F2)](https://discord.gg/JvJHngZWz)

[官网发布页](https://neo-isshin.github.io/open-nova/) · [完整操作 Runbook](docs/local-operations-runbook.zh-CN.md) · [RAG 外部 Agent runtime 合约](docs/rag-external-agent-contract.md) · [English README](README.md)

</div>

Open Nova 是一个高度自动化、结构化、非侵入的本地 AI 资产运维系统，由 LLM 深度参与，为用户将积累的 AI 资产沉淀为可复用的知识与工具。

当你寻求以下需求满足时，Open Nova 会很适合：

- 🤖 **跨多 Agent runtime 共享记忆**：将任何 Agent runtime 的会话、任务、笔记结构化沉淀为可检索证据，且任何 Agent runtime 均可直接使用其他 Agent runtime 的工作记忆；
- 📓 **自动化任务识别与落盘系统** (Beta)：高度结构化总结每日任务成果，由统一的任务系统进行管理与呈现；
- 🌍 **全自动总结与精美 Dashboard**：自动化呈现每日/周/月工作总结，随时总结与回味你与各 Agent runtime 的共同成长；
- 📖 **与 Agent 共同进步**：自动总结遇到的困难与解决方案，并生成建议，沉淀丰富你的学习资产；
- 🚉 **一站式管理所有 Agent**：随时查看你所有 Agent 的各种状态，无论实时还是 lifetime，尽在掌握。支持实时编辑各个 Agent 的 `SKILL.md`；


## 🌟 为什么是 Open Nova

同一个用户可能在同一周内、甚至同一个项目内使用多个 AI Agent runtime，如 `Codex`、`Claude Code`、`Gemini CLI`、`OpenClaw`、`Hermes`。每个工具都有自己的日志、会话、技能、记忆、Token 用量和任务轨迹。

而 Open Nova 的愿景就是打通这些 Agent runtime 之间的壁垒，比如让 `Codex` 也能随时知道 `Claude` 干了什么，也能够将用户零散的工作自动化总结至一处并以漂亮的报告/报表呈现，甚至能够自动为用户归类与落盘实际发生的工作成果、克服困难时遇到的阻碍 —— 而这些是完全自动化的。


| 能力 | 说明 |
| :--- | :--- |
| **全自动化管线** | 自动采集所有 Agent runtime 原始活动，清洗、解析、分析、结构化所有可能的资产，持久化进入数据基座。 |
| **持久化 AI 资产层** | 将受支持工具、会话、用量事件、任务、报告和生成日记规范化到本地 `Foundation` 系统中。 |
| **工作区自动归属** | 即使工作不是从项目目录启动，也能根据工具证据关联工作区、Agent runtime、定时任务和源码路径。 |
| **智能任务管理系统** | 从所有 Agent runtime 活动中捕捉一切任务细节、证据、状态、候选项和审阅上下文，由 Nova-task 系统统一解析、归类、维护、呈现。 |
| **自研 nova-RAG** | 提供精心优化的 RAG 检索能力，向其他 Agent runtime 暴露只读搜索合约，实现跨 Agent runtime 共享记忆。 |
| **完全的设置中心** | 可自定义整个系统的配置，包括时区、管线运行、LLM 偏好、RAG 参数。 |

## 💫 核心优势

- **解析器优先的整理系统**：各数据源专用解析器会先规范化会话、任务、用量记录、定时任务活动、报告和工作区信号，再进入总结、任务证据或 RAG 流程。
- **非侵入 runtime**：读取配置好的工具位置，写入自己的 runtime 主目录，不接管用户的 Agent runtime、Shell、编辑器或模型网关。
- **覆盖后台自动化**：项目上下文不只依赖当前 Shell 目录；定时任务、后台脚本和跨目录 Agent runtime 运行也能通过工具证据被归因到对应工作区。
- **来自真实工作的任务记忆**：`Nova-Task` 将已观察到的 Agent runtime 活动转化为任务权威源和审阅界面，不仅分析对话内容，也会评估 tool results 等关键工程信息来判断工作完成度，让任务看板反映真实发生的工作，而不是另一个需要手工维护的待办清单。
- **低成本模型友好**：结构化提示词、明确结构约束和优化过的执行器，让参数较小或成本较低的模型也能产出可用结构化结果。更强模型可以提升质量，但系统不绑定单一高价模型路径。
- **工具技能可编辑**：每个工具相关的技能和外部 Agent runtime 集成文件由操作者控制，在 dashboard 直接实现可查看、可调整、可审计。
- **具备 Agentic RAG 能力的 nova-RAG**：`nova-RAG` 是 Open Nova 自研的 RAG 系统，向外部 Agent runtime 提供只读搜索，并通过评估查询、候选提升和回滚管理召回质量，可作为你的专属 RAG系统。
- **完全专属**：所有资产均来自你与各 Agent runtime 的活动记录，打造专属于你的 AI 资产。

## 💻 系统构成

| 子系统 / 模块 | 价值与作用 |
| :--- | :--- |
| **`Foundation`** | 规范化并落盘 AI 活动、工作区归因、快照、报告、任务证据和修复审计记录的本地事实源。 |
| **`Dashboard`** | 在浏览器界面中查看日记、AI 资产指标、Token 用量、设置、`Foundation` 操作、后台任务和任务审阅。 |
| **`base-pipeline`** | 基于用户 AI 工作自动生成叙事记录、技术进展、学习笔记和任务摘要。 |
| **`nova-RAG`** | Open Nova 自研的可选 RAG 系统，具备 Agentic RAG 能力，支持本地或云端 embedding、校准后的召回、外部只读搜索和受保护的索引生命周期。 |
| **`Nova-Task`** | 由 LLM 深度驱动的任务权威源和审阅界面，用于处理真实任务证据。 |
| **`归因解析器`** | 面向工作区、Agent runtime、会话、定时任务、用量记录和工具证据的识别与分类层。 |
| **`安装器`** | 支持 dry-run 规划、依赖分组、runtime 引导、macOS `LaunchAgent` 注册、doctor 检查和升级路径。 |

当前外部工具设置支持 `OpenClaw`、`Claude Code`、`Codex`、`Gemini CLI` 和 `Hermes` 等路径族。这些集成被视为操作者拥有的配置，而不是隐藏式接管全局工具环境。

## 💽 前置要求

Open Nova 当前优先面向本地 macOS 运行环境：

- 🍎 **默认支持目标为 macOS**：引导式安装器和托管调度器默认使用 macOS `LaunchAgent` 服务。
- 🐍 **Python 环境**：需要 Python `>= 3.11`（推荐 Python `3.12`）。
- 📦 **依赖自动安装**：系统会自动检测依赖，缺失依赖会自动安装。运行 `Dashboard` 和 `nova-RAG` 本地 embedding 时会安装额外的 Python 包；首次安装可能会下载 `torch`、`sentence-transformers` 等较大的依赖。
- 🐧 **Linux & Windows 兼容性**：暂不是一行安装（One-liner）的一等支持目标。高级用户可以从本地 Checkout 并手动运行单个组件。下一个大版本会加入对 Windows 与 Linux 的原生支持，以及跨设备 Agent runtime 支持（仅限 Linux/macOS）。
- ⚙️ **PATH 要求**：安装前请确认 `git`、`curl` 和兼容的 `python3` 已在环境变量 `PATH` 中可用。

**Open Nova 当前支持 5 种 Agent runtime：**<br>
🦞 `OpenClaw`、✳️ `Claude Code`、🤖 `Codex`、✨ `Gemini CLI`、⚕️ `Hermes`

更多 Agent runtime 将在下一个大版本得到支持，包括 `Cursor`、`Antigravity`、`OpenCode` 等。

## 🎥 快速开始

> [!TIP]
> **一行部署，然后静等繁荣。**

### 1. 一键安装 (One-liner)

通过托管 bootstrap 脚本安装：

```bash
zsh -c "$(curl -fsSL 'https://raw.githubusercontent.com/Neo-Isshin/open-nova/v1.0.0/install/bootstrap.sh')"
```

托管 bootstrap 只用于全新安装。它会先把最新正式 GitHub Release
解析为完整 commit，再获取源码；没有正式 Release 时将安全阻断。若已存在任何
Open Nova Runtime 或托管 LaunchAgent，请改用
`open-nova update --dry-run`，确认后执行 `open-nova update --apply`。
上面的版本化 `v1.0.0` URL 是本版本的不可变安装入口，不会追踪 `main`。

> [!NOTE]
> 安装器会引导您配置 LLM Provider，并将 Provider Key 写入 Runtime 本地密钥目录 `$NOVA_HOME/state/secrets`（目录权限 `0700`、密钥文件权限 `0600`）。该机制支持无人值守运行，用户无需配置 Keychain，也不会遇到周期性重新授权。`nova-RAG` 为可选子系统；如配置云端 Embedding Key，也使用同一密钥目录。
>
> 已有 `macos-keychain` 引用仅用于兼容迁移。若旧 Keychain item 无法读取，请在 Dashboard 中重新输入一次对应 Provider Key；Open Nova 不会自动删除旧 Keychain item。

### 2. 基础验证

安装完成后，先运行以下只读命令：

```bash
open-nova doctor
open-nova model show
open-nova onboard status
open-nova config show
```

安装摘要会显示实际 Dashboard URL。默认地址为 `http://127.0.0.1:3036/dashboard`；若端口被占用，请使用摘要中自动选择的新地址。

## 🧭 Onboarding：首次使用引导

### 1. 打开 Dashboard

使用安装摘要中的 URL 打开 Dashboard。确认页面可以访问，并检查右上角的后台任务与消息状态。

### 2. 配置并检测 LLM Provider

进入设置页面，确认 Provider、Endpoint、Model 和 API Key。先执行可用性检测，检测通过后再保存设置。叙事日记、周期总结和需要 LLM 的历史任务依赖这套配置。

### 3. 生成第一批历史数据

1. 点击 Dashboard 中的“生成历史数据”，选择希望补全的日期范围。
2. 先点击“计划预览”，检查待生成的日记、周报、月报、预计 LLM 调用量和 `nova-RAG` 同步任务。
3. 取消勾选不需要生成的任务，然后点击“排队生成”。
4. 系统只执行计划中勾选的任务；`nova-RAG` 任务仅在其已启用并可用时执行。

> [!NOTE]
> 历史数据生成在后台运行。较长日期范围可能需要较长时间；空白日期可能只生成结构化占位产物，不一定调用 LLM。

### 4. 查看进度与结果

在“后台任务”和“消息”中查看排队、运行、失败和重试状态。任务完成后，刷新日记、AI 资产、Nova-Task 和 `nova-RAG` 页面查看结果。失败任务可从消息或后台任务入口重试。

### 5. 完成 Onboarding

- [ ] Dashboard 可以正常打开；
- [ ] LLM Provider 检测通过并已保存；
- [ ] `open-nova doctor` 没有阻断性错误；
- [ ] 历史数据计划预览与勾选任务符合预期；
- [ ] 第一批任务已完成或正在后台运行；
- [ ] 日记和 AI 资产页面已有数据；
- [ ] Nova-Task 已出现任务证据；
- [ ] 启用 `nova-RAG` 时，Server 和索引状态正常。

完整的首次运行、日常操作、调度、更新和故障排查流程见 [本地操作 Runbook](docs/local-operations-runbook.zh-CN.md)。

## 📂 Runtime 布局

默认情况下，Open Nova 使用用户本地路径：

| 本地路径 | 用途说明 |
| :--- | :--- |
| **`~/.open-nova`** | Runtime 主目录 (Runtime Home) |
| **`~/.config/open-nova/location.json`** | 当前活动 Runtime 指针 |
| **`~/.open-nova/state/secrets`** | Runtime 本地 LLM 与可选云端 Embedding Provider 密钥 |
| **`~/.open-nova/artifacts/diary`** | 生成的日记与总结输出目录 |
| **`~/.open-nova/bin/open-nova`** | CLI 命令行 Shim 链接 |

安装器还会创建面向用户的 `~/.local/bin/open-nova` Shim。使用 `--no-shell-path` 可保持 Shell profile 不变；使用 `--shell-path-file /path/to/profile` 可明确指定需要更新的 profile 文件。

## 🔧 Base Pipeline

- macOS 安装器默认注册 Open Nova 托管的用户级 LaunchAgent；plist 位于 `~/Library/LaunchAgents/`。
- Pipeline 从已配置的外部工具路径采集活动，并根据实际工具证据完成工作区归属。
- 使用 `open-nova doctor --scheduler` 检查托管调度状态。
- 手动运行指定日期的日常管线：`open-nova pipeline [YYYY-MM-DD]`。
- 如需由外部 Agent runtime 定时调用，应先避免与系统托管调度重复运行；Dashboard 设置页提供相应提示词。

## 📊 控制面板 (Dashboard)

`Dashboard` 是一个本地 FastAPI 应用，包含动态与静态 UI 资源。本地默认访问地址为：

```text
http://127.0.0.1:3036/dashboard
```

> [!NOTE]
> 若默认 `3036` 端口被占用，安装器会自动选择其他可用端口；请以安装完成摘要中显示的 Dashboard URL 为准。

控制面板包括以下核心视窗：
- 📅 **每日和周期日记视图**：查看自动生成的 Narrative、Technical、Learning 记录；
- 📈 **实时数据总览与 AI 资产指标**：可视化的用量数据分析统计；
- 🔧 **Foundation 操作与每日 QA 面板**：快速审计与修复数据；
- ✉️ **后台任务与消息界面**：直观展示系统常驻进程状态；
- ⚙️ **系统设置与偏好面板**：包括 LLM Provider、调度器和运行状态视图；
- 📋 **任务看板与 Nova-Task 审阅**：直观管理和验证实际工作任务证据；
- 🔍 **RAG 检索控制**：在启用 `RAG` 配置后进行全局语义搜索及召回质量管理。

### 🖼️ 界面预览

本地 Dashboard 是 Open Nova 的主要操作界面。隐私安全、完全使用合成数据的交互预览可在
[公开发布页](https://neo-isshin.github.io/open-nova/)访问，仓库内也保留了
[docs/dashboard-demo/index.html](docs/dashboard-demo/index.html)。该 Demo 不会连接本机
Open Nova Runtime。

<p align="center">
  <a href="https://neo-isshin.github.io/open-nova/">
    <img src="docs/assets/open-nova-product.svg" alt="Open Nova 产品概览" width="100%">
  </a>
</p>

## ⚙️ Nova-Task System

得益于领先的业务逻辑设计，nova-task得以与人类在任务管理系统上实现良好的协同。可随时由人类接管，人类也可放心地将任务交给nova-task进行自动化维护。
自动化维护的情况下，nova-task可以自主识别任务节点级别、更新任务状态、挂载新的子任务、优化任务树。——对于重要的一级节点，会交给人类审批后再挂载新的子任务。而对于普通的二级、三级子任务，则完全自主完成。
`nova-Task` 致力于成为用户的“真实工作图谱“，记录实际发生的工程工作、而不是计划中想做的工作。这对AI时代非常有价值，因为很多工作不是从明确ticket开始，而是从对话、排查、修复、试验、回滚、验证中自然生长出来的。
`nova-Task` 也负责“任务状态追踪系统”的职责，包括已有工作看板的增添、状态机更新。用户输入RFC、PRD、roadmap、Audit等工程文档后，nova-task可调用LLM将专业工程文档拆解为符合nova-task标准的任务树，从而进行迭代维护。


## 🤖 nova-RAG 外部 Agent runtime 边界

`nova-RAG` 是 Open Nova 自研的检索系统。它旨在为外部 Agent runtime 提供读取个人工作记忆的只读查询能力，同时**严禁**外部 Agent runtime 获得对记忆、索引、全局设置或服务生命周期的写入/修改权限。召回质量通过 `nova-RAG` 索引生命周期、评估查询、候选提升 (Candidate Promotion) 和回滚路径管理。

`nova-RAG` 通过对**服务端**和**客户端（外部Agent runtime）**同时优化，以实现Agentic RAG级别的召回质量：
- **Server-side Agentic**：确定性、低成本、baseline-first、自适应 pass。
- **Skill-side Agentic**：使用外部 Agent 自身 LLM，只在服务端返回 weak/ambiguous 时反思。

#### 1. 优先使用的 Dashboard 代理接口 (默认基址 `http://127.0.0.1:3036`)
外部 Agent runtime 应当优先调用此只读 Facade 接口：
```text
GET  /api/rag/external/health    # 外部健康检查
GET  /api/rag/external/stats     # 召回与索引统计
GET  /api/rag/external/contract  # 外部接口合约声明
POST /api/rag/external/search    # 语义记忆搜索接口
```

#### 2. 直接调用 nova-RAG 独立服务端接口 (默认基址 `http://127.0.0.1:3037`)
```text
GET  /health                     # 基础健康检查
GET  /stats                      # 索引库状态
POST /search                     # 记忆检索接口
```

> [!IMPORTANT]
> - 实际主机和端口以当前 runtime 设置为准。`POST /encode` 为系统内部 Embedding 计算接口，不属于外部 Agent runtime 合约公开边界。
> - 任何写入记忆、创建 Source 数据源、重建索引、修改配置参数、启动/停止服务等写操作（Mutation）请求，均会被外部命名空间严格拒绝。详见 [docs/rag-external-agent-contract.md](docs/rag-external-agent-contract.md)。

## 📐 开发与测试

若要在本地进行二次开发或调试，请按照以下步骤搭建可编辑环境：

### 1. 搭建本地开发环境
```bash
# 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 升级 pip 并安装可编辑模式依赖
python -m pip install --upgrade pip
python -m pip install -e ".[dashboard,rag-local]"
```

### 2. 运行单元测试
```bash
# 在一次性 venv、HOME、NOVA_HOME 和固定业务时钟中运行发布测试集
python tests/run_isolated_release_suite.py
```

若只需在已准备好的开发环境中运行一项定向测试，可使用
`python -m unittest tests.test_module.TestClass.test_name`。

### 3. 运行前端与端到端测试
若 Node.js 测试工具链可用，可运行静态检查及 Playwright 自动化测试：
```bash
# 按 lockfile 安装 Node 依赖
npm ci

# 检查静态 JavaScript 语法
node --check src/dashboard/app/static/js/app.js

# 运行确定性的 Release Page 与 Dashboard context 测试
npm run test:dashboard-live-context
npm run test:release-page
```

> [!NOTE]
> 真实 Dashboard Gate 是显式 opt-in 且具破坏性的测试。只能针对已播种的一次性
> Runtime 运行 `npm run test:dashboard-live`，并把
> `OPEN_NOVA_DASHBOARD_LIVE_BASE_URL` 指向它的 loopback URL。生成的数据库、
> Runtime 日志、临时文件、证据和本地 secret-store 数据必须保持 untracked，禁止提交。

## 📄 关联文档

- 📖 [新用户安装手册](docs/new-user-onboarding-runbook.md)
- ⚙️ [完整本地操作 Runbook](docs/local-operations-runbook.zh-CN.md)
- 🧭 [CLI 产品边界](docs/cli-boundary.md)
- 🤖 [RAG 外部 Agent runtime 合约](docs/rag-external-agent-contract.md)
- 🧩 [Nova-Task 工作图谱对账](docs/nova-task-work-graph-reconciliation.md)
- 🧹 [发布清理清单](docs/production-clean-inventory.md)
- ✅ [v1.0.0 发布保证摘要](docs/v1-release-assurance.md)
- 🌐 [现代发布页](https://neo-isshin.github.io/open-nova/)
- 🧾 [更新日志](CHANGELOG.md)
- 🔐 [安全策略](SECURITY.md)
- 🕰️ [公开项目历史](HISTORY.md)

## ⚖️ 许可证

Copyright © 2026 Neo-Isshin.

Open Nova 是自由软件，采用 [GNU 通用公共许可证第 3 版或任何后续版本](LICENSE)，SPDX 标识为 `GPL-3.0-or-later`。

## 🙏 致谢

Open Nova 的诞生得益于众多优秀的 AI 编程工具及其开源社区。感谢这些工具把 Token 用量写入本地日志，使统一的可视化和资产归口成为可能。

同时感谢 [getdesign.md](https://getdesign.md)（或相关社区）为 Dashboard 提供的前端设计灵感。

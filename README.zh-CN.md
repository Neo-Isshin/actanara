<h1 align="center">
  <img src="docs/assets/banner.png" alt="Open Nova" width="650">
</h1>

<p align="center">
  <strong>跨多 Agent Runtime 共享记忆，将相互独立的 Agent 活动整合为可检索、可复用的本地 AI 资产。</strong>
  <br>
  高度自动化的 AI 资产运维 · 跨 Runtime 记忆共享 · LLM 深度参与
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Language-简体中文%20·%20当前-C026D3?style=for-the-badge" alt="当前语言：简体中文">
  <a href="README.md"><img src="https://img.shields.io/badge/Language-English-2563EB?style=for-the-badge" alt="Switch to English README"></a>
</p>

<p align="center">
  <a href="https://neo-isshin.github.io/open-nova/"><img src="https://img.shields.io/badge/Website-GitHub%20Pages-2563EB" alt="Website"></a>
  <a href="https://github.com/Neo-Isshin/open-nova/releases/tag/v1.0.1"><img src="https://img.shields.io/badge/Release-v1.0.1-0EA5E9" alt="Release v1.0.1"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/Install-pinned%20v1.0.1-0284C7" alt="Pinned v1.0.1 install command"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPL--3.0--or--later-16A34A" alt="License"></a>
  <a href="https://discord.gg/JvJHngZWz"><img src="https://img.shields.io/badge/Discord-加入-5865F2" alt="Discord"></a>
</p>

<p align="center">
  <a href="https://neo-isshin.github.io/open-nova/">官网发布页</a> ·
  <a href="https://neo-isshin.github.io/open-nova/dashboard-demo/"><strong>真实 Dashboard 静态 Demo</strong></a> ·
  <a href="docs/local-operations-runbook.zh-CN.md"><strong>中文本地操作 Runbook</strong></a> ·
  <a href="docs/rag-external-agent-contract.md">nova-RAG 外部合约（English）</a>
</p>

Open Nova 是一个高度自动化、结构化、本地优先的 AI 资产运维系统。它从受支持的 Agent Runtime 中整理会话、任务、用量和工作轨迹，将零散活动沉淀为统一的本地数据、日记、任务证据和可检索记忆。

Open Nova 也是一个 **LLM 深度参与**的系统：LLM 参与总结、任务提取、学习资产生成和知识组织；数据采集、解析、归因、调度、持久化和安全边界则由确定性组件控制。

> 本文中的 **Agent Runtime** 指拥有独立会话、日志、记忆和执行上下文的 AI 工具环境，例如 Codex、Claude Code、Gemini CLI、OpenClaw 和 Hermes。

<a id="why-open-nova"></a>
## 🌟 为什么需要 Open Nova

同一个用户可能在同一周、甚至同一个项目中轮换使用多个 Agent Runtime。每个工具都有自己的日志、会话、Skill、记忆、Token 用量和任务轨迹；它们往往真实记录了工作，却彼此隔离。

Open Nova 希望打通这些壁垒：让 `Codex` 能查找 `Claude Code` 已经完成的工作，让不同 Runtime 的活动被统一总结和展示，也让任务成果、解决过的困难和调试证据不再随会话消失。

你可以用它：

- 🤖 **跨 Runtime 共享记忆**：将受支持 Runtime 的会话、任务和笔记转换为结构化证据；启用 `nova-RAG` 后，外部 Runtime 可以通过只读合约检索这些记忆。
- 📓 **自动识别和落盘任务**（Beta）：从真实活动和工具结果中提取候选任务、证据和状态，交给 `Nova-Task` 统一组织与审阅。
- 🌍 **自动生成每日、每周和每月总结**：在 Dashboard 中查看工作进展、AI 资产增长与 Token 用量。
- 📖 **与 Agent 共同进步**：从困难、解决方案和实践建议中积累可复用的学习资产。
- 🚉 **集中管理受支持的 Runtime**：查看活动、用量和运行状态，并审阅或编辑受支持 Runtime 的 `SKILL.md`。

## 📚 内容导航

- [核心优势](#core-advantages) · [工作原理与系统构成](#how-it-works) · [支持范围](#support)
- [快速开始](#quick-start) · [Dashboard、截图与交互 Demo](#dashboard) · [Nova-Task](#nova-task)
- [nova-RAG](#nova-rag) · [隐私与安全](#privacy-security) · [开发与测试](#development)
- [文档导航](#documentation) · [许可证](#license) · [Give me a Star](#give-star)

<a id="core-advantages"></a>
## 💫 核心优势

- **解析器优先**：先将不同 Runtime 的会话、任务、用量、定时活动和工作区信号标准化，再进入总结、任务证据或 RAG 流程，而不是将未处理的原始日志直接交给 LLM。
- **可靠的工作区归因**：项目上下文不只依赖当前 Shell 目录；定时任务、后台脚本和跨目录 Runtime 活动也能通过执行证据关联到正确工作区。
- **基于真实工作的任务证据**：`Nova-Task` 不仅分析对话，也结合工具结果和交付证据判断任务状态，让看板更接近实际发生的工程工作。
- **本地优先且边界清晰**：Open Nova 读取已配置的工具位置，把数据写入自己的 Runtime Home，不改写外部 Runtime 的历史或接管其执行。
- **模型成本友好**：结构化提示词、明确 Schema 和可控的编排流程，让轻量或成本友好的模型也能产出可用结果，同时不锁定单一 Provider。
- **集成由用户控制**：工具 Skill、外部 Runtime 定义和关键设置可查看、可编辑、可审计，不以隐式方式接管全局工具链。
- **受保护的 Agentic RAG 生命周期**：`nova-RAG` 通过评估查询、候选提升、召回校准和安全回滚管理检索质量，并且只向外部 Runtime 暴露受限的只读合约。

<a id="how-it-works"></a>
## 🧭 工作原理

```text
受支持的 Agent Runtime
        ↓
解析、归因与标准化
        ↓
Foundation 本地事实层
        ↓
Base Pipeline · Nova-Task · Dashboard
        ↓
nova-RAG（可选）→ 外部 Runtime 只读检索
```

| 系统 | 核心职责 |
| :--- | :--- |
| **`Foundation`** | 将 AI 活动、工作区归因、快照、报告、任务证据和修复记录规范化到本地事实层。 |
| **`Base Pipeline`** | 从 Runtime 活动中生成叙事日记、技术进展、学习记录和任务总结。 |
| **`Dashboard`** | 统一呈现日记、AI 资产、Token 用量、设置、Foundation 操作、后台任务和任务看板。 |
| **`Nova-Task`** | 根据真实工作证据维护可审阅的任务图谱。 |
| **`nova-RAG`** | 可选的本地或云端 Embedding 检索子系统，提供受保护的索引生命周期与外部只读检索。 |
| **归因解析器** | 识别 Runtime、会话、工作区、定时任务、用量事件和执行证据，包括从项目目录外启动的工作。 |
| **安装器** | 处理依赖检测、Runtime 初始化、macOS LaunchAgent、Doctor 检查和受保护的更新事务。 |

这些优势由本地事实层、Pipeline、任务系统、Dashboard 和可选检索子系统共同实现。

<a id="support"></a>
## 💻 支持范围与前置要求

Open Nova v1.0.x 的托管安装优先面向本地 macOS 用户环境：

- 🍎 **macOS 是一等支持目标**：引导式安装、Dashboard 服务和托管调度默认使用用户级 `LaunchAgent`。
- 🛠️ **基础工具**：安装前需确认 `zsh`、`git` 和 `curl` 可用，无需 `sudo`。
- 🐍 **Python**：运行需要 Python `>=3.11`。受支持的 Apple Silicon 与 Intel Mac 如果缺少兼容 Python，安装器会自动下载并校验托管 Python。
- 🌐 **网络和磁盘**：安装期间需要访问 GitHub、Python 包索引及你选择的模型服务。启用本地 `nova-RAG` 时，首次运行可能下载 `torch`、`sentence-transformers` 和模型权重。
- 🐧 **Linux 与 Windows**：不是 v1.0.x 一行安装和托管服务的一等支持目标；高级用户可从源码手动运行部分组件。

### 当前支持的 Agent Runtime

| Runtime | v1.0.x 定位 |
| :--- | :--- |
| 🦞 **OpenClaw** | 受支持的外部工具路径族 |
| ✳️ **Claude Code** | 受支持的外部工具路径族 |
| 🤖 **Codex** | 受支持的外部工具路径族 |
| ✨ **Gemini CLI** | 受支持的外部工具路径族 |
| ⚕️ **Hermes** | 受支持的外部工具路径族 |

实际可采集内容取决于本机是否存在兼容日志、会话或用量数据，以及对应路径是否已在设置中启用。更多 Runtime 与跨平台能力属于后续版本范围，不应从 v1.0.x README 推断为已支持能力。

<a id="quick-start"></a>
## 🎥 快速开始

> [!TIP]
> **一行部署，然后静等繁荣。**

### 1. 安装 v1.0.1

下面的 one-liner 同时固定 `v1.0.1` bootstrap 与实际安装源码提交，不追踪 `main` 或未来的 `latest` Release：

```bash
bootstrap="$(curl -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 --max-time 30 'https://raw.githubusercontent.com/Neo-Isshin/open-nova/v1.0.1/install/bootstrap.sh')" && [ -n "$bootstrap" ] && NOVA_INSTALL_SOURCE_URL='https://github.com/Neo-Isshin/open-nova.git' NOVA_INSTALL_REF='82bbdbd83e35724441c7005dfc0b555d413fcf93' zsh -c "$bootstrap"
```

> [!NOTE]
> 这里采用“严格固定 v1.0.1”：显式 commit 会跳过 bootstrap 对未来 `latest` Release 和 `WITHDRAWN` 标记的动态检查，从而固定 Open Nova 源码内容，不随 `latest` 漂移。第三方依赖仍按发布配置在安装时解析，因此这里不承诺整个依赖环境逐字节可复现。这项取舍不会改动已发布的 v1.0.1 tag 或 Release。

> [!IMPORTANT]
> 这条命令仅用于全新安装。如果 bootstrap 发现已有 Open Nova Runtime、活动 Runtime 指针或托管 LaunchAgent，会在写入源码缓存前安全终止。已有安装应先运行 `open-nova update` 或 `open-nova update --dry-run` 检查计划，再用 `open-nova update --apply` 执行更新。

> [!WARNING]
> `v1.0.0` 已撤回：它的更新事务可能使托管服务继续绑定到旧的具体源码目录。该版本的不可变 tag 和制品仅供审计，请勿安装或推荐。

#### 安装器会写入哪些位置

| 路径 | 用途 |
| :--- | :--- |
| `~/.cache/open-nova/installer` | 安装源码缓存 |
| `~/.open-nova` | Runtime、虚拟环境、设置、数据库、日志、密钥和生成资产 |
| `~/.config/open-nova/location.json` | 当前活动 Runtime 指针 |
| `~/.local/bin/open-nova` | 面向用户 `PATH` 的 CLI 入口 |
| `~/.zprofile` | 默认写入带标记的 `PATH` 配置；可用 `--no-shell-path` 禁用 |
| `~/Desktop/Open Nova` | 默认创建指向日记目录的快捷链接 |
| `~/Library/LaunchAgents/` | macOS 用户级 Dashboard、Scheduler 和可选 RAG 服务 |

默认 Shell profile 写入可用 `--no-shell-path` 禁用，也可用 `--shell-path-file /path/to/profile` 显式指定目标文件。

启用 `nova-RAG` 并在向导中选择外部 Agent Runtime 时，安装器还可以注册缺失的只读检索 Skill；已有 Skill 不会被隐式覆盖。

### 2. 基础验证

安装完成后，先运行以下只读命令。它们不会初始化新 Runtime，也不会修改现有设置：

```bash
open-nova doctor
open-nova model show
open-nova onboard status
open-nova config show
```

如需定向诊断：

```bash
open-nova doctor --installer
open-nova doctor --pipeline
open-nova doctor --scheduler
open-nova doctor --rag
```

安装摘要会显示实际 Dashboard URL。默认为 `http://127.0.0.1:3036/dashboard`；如果端口已被占用，请使用摘要中自动选择的新地址。

### 3. 完成首次运行

1. **打开 Dashboard**：使用安装摘要中的 URL，检查右上角的后台任务与消息状态。
2. **配置 LLM Provider**：确认 Provider、Endpoint、Model 和 API Key，先执行可用性测试，再保存设置。
3. **预览历史数据计划**：选择日期范围，检查待生成日记、周报、月报、预计 LLM 调用和可选 RAG 任务。
4. **排队执行**：取消不需要的任务，再将其余项目加入后台队列。
5. **查看结果**：在“后台任务”和“消息”中查看进度，完成后刷新日记、AI 资产、Nova-Task 和可选 `nova-RAG`。

> [!NOTE]
> 历史数据生成在后台运行。较长日期范围可能需要更长时间；没有活动的日期可能只生成结构化占位产物，不一定调用 LLM。

<details>
<summary><strong>首次运行检查清单</strong></summary>

- [ ] Dashboard 可以正常打开。
- [ ] LLM Provider 检测通过并已保存。
- [ ] `open-nova doctor` 没有阻断性错误。
- [ ] 历史数据计划与勾选任务符合预期。
- [ ] 首批任务已完成或可以在后台观察。
- [ ] 日记、AI 资产与 Nova-Task 已有内容。
- [ ] 启用 `nova-RAG` 时，Server 和活动索引已就绪。

</details>

当前 v1.0.1 面向 macOS 本地 Runtime；完整的安装前检查、首次配置、历史回填、日常 Pipeline、Dashboard / Nova-Task / nova-RAG 运维、更新与故障排查，请参阅<a href="docs/local-operations-runbook.zh-CN.md">中文本地操作 Runbook</a>。

<a id="dashboard"></a>
## 📊 Dashboard、截图与交互 Demo

Dashboard 是 Open Nova 的主要操作界面，包括：

- 📅 每日、每周和每月日记；
- 📈 实时概览、Token 用量与 AI 资产指标；
- 🔧 Foundation 操作、每日 QA 与数据修复；
- ✉️ 后台任务和消息；
- ⚙️ LLM Provider、调度、Runtime 与外部工具设置；
- 📋 Nova-Task 任务看板与证据审阅；
- 🔍 启用 RAG 后的语义检索与召回质量视图。

### 🖼️ 真实 Dashboard 截图

以下图片来自 Open Nova 的真实 Dashboard 开发与运行界面，沿用项目本身的设计、布局、排版和组件，不是重新绘制的演示页，也不是官网发布页的营销示意图。截图中的 **nova-RAG v2** 指 RAG 子系统的索引与检索代际，不是 Open Nova v2 产品版本；当前产品版本仍为 `v1.0.1`。

<details>
<summary><strong>展开 Dashboard 首页真实截图</strong></summary>

<p align="center">
  <a href="docs/assets/dashboard/dashboard-home.png">
    <img src="docs/assets/dashboard/dashboard-home.png" alt="Open Nova Dashboard 首页" width="100%">
  </a>
</p>

<p align="center"><sub>Open Nova Dashboard 首页；点击图片查看原图。</sub></p>

</details>

<details>
<summary><strong>展开 W27 周报真实截图</strong></summary>

<p align="center">
  <a href="docs/assets/dashboard/dashboard-weekly-full.png">
    <img src="docs/assets/dashboard/dashboard-weekly-overview.png" alt="Open Nova Dashboard W27 周报概览" width="100%">
  </a>
</p>

<p align="center"><sub>W27 示例报告；点击图片查看完整周报长图。</sub></p>

</details>


<details>
<summary><strong>展开 AI 资产真实截图</strong></summary>

<p align="center">
  <a href="docs/assets/dashboard/dashboard-ai-assets-long.png">
    <img src="docs/assets/dashboard/dashboard-ai-assets-overview.png" alt="Open Nova Dashboard AI 资产概览" width="100%">
  </a>
</p>

<p align="center"><sub>点击图片查看 AI 资产完整长图。</sub></p>

</details>

<details>
<summary><strong>展开 Nova-Task 真实任务图谱</strong></summary>

<p align="center">
  <a href="docs/assets/dashboard/dashboard-nova-task.png">
    <img src="docs/assets/dashboard/dashboard-nova-task.png" alt="Open Nova Nova-Task 真实任务图谱" width="100%">
  </a>
</p>

</details>

<details>
<summary><strong>展开 nova-RAG 状态与检索界面</strong></summary>

<p align="center">
  <a href="docs/assets/dashboard/dashboard-nova-rag.png">
    <img src="docs/assets/dashboard/dashboard-nova-rag.png" alt="Open Nova nova-RAG 状态与检索界面" width="100%">
  </a>
</p>

</details>

### ▶️ 真实静态交互 Demo

<a href="https://neo-isshin.github.io/open-nova/dashboard-demo/"><strong>Dashboard 静态 Demo</strong></a>直接保存了真实 Dashboard 的 HTML、CSS、组件、布局与交互代码，只将后端 API 替换为固定静态数据，因此不会连接或改写本机 Open Nova Runtime。当前展示数据仅保留：实时总览、AI 资产、Nova-Task 任务看板、一份 W27 周报、两份普通日记，以及一份 Blank Day 日记。为完整呈现周报组件，W27 指标依据既有真实截图的字段与量级补入了明确标注的展示性数据，不代表某次真实运行统计。

<p align="center">
  ▶ <a href="https://neo-isshin.github.io/open-nova/dashboard-demo/"><strong>打开真实 Dashboard 静态 Demo</strong></a>
</p>

> [!NOTE]
> Demo 也随仓库保存在 [`docs/dashboard-demo/index.html`](docs/dashboard-demo/index.html)，可从本地 Checkout 打开。<a href="https://neo-isshin.github.io/open-nova/">官网发布页</a>仍用于版本介绍与安装，交互 Demo 使用独立的 `/dashboard-demo/` 地址。

### Runtime 布局

| 默认路径 | 用途 |
| :--- | :--- |
| `~/.open-nova` | 主 Runtime Home |
| `~/.config/open-nova/location.json` | 活动 Runtime 指针 |
| `~/.open-nova/config/settings.json` | Runtime 设置 |
| `~/.open-nova/data/nova_data.sqlite3` | Foundation SQLite 数据库 |
| `~/.open-nova/state/secrets` | LLM 与可选云端 Embedding Provider 密钥 |
| `~/.open-nova/artifacts/diary` | 日记与总结 |
| `~/.open-nova/artifacts/reports` | 报告输出 |
| `~/.open-nova/bin/open-nova` | Runtime 内的 CLI shim |

Runtime 数据库、日记、报告、日志、缓存、密钥与本地 LaunchAgent 产物都不应提交到源码仓库。

### 常用命令

搜索 `nova-RAG` 中的本地记忆：

```bash
open-nova search "deployment issue" --top-k 5
open-nova search "deployment issue" --top-k 5 --json
```

该命令通过 Dashboard 的只读外部检索接口工作。自动化脚本使用 JSON 输出时应检查 `available` 字段；RAG 暂不可用时，命令仍可能成功返回结构化状态。

手动运行每日 Pipeline：

```bash
open-nova pipeline
open-nova pipeline 2026-07-12
```

不指定日期时，Pipeline 默认处理当前配置时区的前一个日历日。Pipeline 会写入日记、报告和 Foundation 数据；如果目标日期已完整生成，只有明确使用 `--force` 才会基于冻结的 Foundation 输入重新生成。

检查或执行更新：

```bash
open-nova update
open-nova update --dry-run
open-nova update --apply
```

- `open-nova update` 仅显示更新计划。
- `--dry-run` 运行 bootstrap 和安装器的无变更预演；冷缓存时主要展示源码获取计划，不等于完整候选版本 E2E 验证。
- `--apply` 才会执行实际的受保护更新事务。

Open Nova v1.0.1 尚未提供产品级一键卸载器。请不要仅删除 `~/.open-nova`；这会遗留 LaunchAgent、CLI shim、Runtime 指针、Shell `PATH` 区块、桌面链接和安装缓存。

<a id="nova-task"></a>
## 📋 Nova-Task：真实工作图谱

`Nova-Task` 不只是另一个待办清单。它希望根据对话、文件变更、工具结果和执行证据，记录实际发生的工程工作。

这是一张**真实工作图谱**：很多有价值的工作并不从明确 ticket 开始，而是在对话、排查、修复、试验、回滚和验证中自然生长。`Nova-Task` 将这些轨迹转换为可审阅、可持续维护的任务结构。

在自动维护模式下，`Nova-Task` 可以识别层级、更新状态、挂载子任务并优化任务树。影响较大的一级节点保留人工审阅，常规的二级、三级更新可按规则自动处理；人类随时可以接管。

导入 RFC、PRD、Roadmap 或 Audit 类文档后，Open Nova 还可以调用 LLM 将其拆解为可迭代审阅和维护的 `Nova-Task` 任务树。

<a id="nova-rag"></a>
## 🤖 nova-RAG：共享记忆与只读边界

`nova-RAG` 是 Open Nova 的可选检索子系统，支持本地或云端 Embedding。它向外部 Agent Runtime 提供查询个人工作记忆的只读能力，同时拒绝写入记忆、修改索引、更改全局设置或控制服务生命周期。

检索质量在两层进行管理：

- **Server-side Agentic**：确定性、低成本、baseline-first 的自适应检索 pass。
- **Skill-side Agentic**：只在服务端返回 weak/ambiguous 证据时，才使用外部 Runtime 自身的 LLM 进一步反思。

`nova-RAG` 还通过评估查询、候选提升、受保护的索引生命周期和安全回滚路径管理召回质量。

<details>
<summary><strong>外部只读 API 概览</strong></summary>

优先使用 Dashboard Facade（默认 `http://127.0.0.1:3036`）：

```text
GET  /api/rag/external/health
GET  /api/rag/external/stats
GET  /api/rag/external/contract
POST /api/rag/external/search
```

直接 nova-RAG 服务（默认 `http://127.0.0.1:3037`）：

```text
GET  /health
GET  /stats
POST /search
```

实际主机和端口以当前 Runtime 设置为准。`POST /encode` 仅用于内部 Embedding 计算，不属于外部 Runtime 合约。

</details>

完整安全边界、请求结构与错误语义见<a href="docs/rag-external-agent-contract.md">nova-RAG 外部 Agent Runtime 合约</a>。

<a id="privacy-security"></a>
## 🔐 隐私与安全

- **本地优先**：Runtime 状态、Foundation 数据库、生成资产和索引保存在用户拥有的本地路径中。
- **密钥权限**：Provider Key 保存在 `$NOVA_HOME/state/secrets`；密钥目录使用 `0700`，密钥文件使用 `0600`。
- **Keychain 迁移**：旧的 `macos-keychain` 引用仅用于兼容迁移。能读取的旧密钥会被复制到 Runtime Secret Store；Open Nova 不会自动删除旧 Keychain item。
- **外部 Provider 边界**：如果你配置了外部 LLM 或 Embedding Provider，相关派生工作内容会按照所选 Endpoint 和 Provider 数据政策发送。
- **输入内容**：如果原始日志、日记或用户选定材料中已有密钥或敏感信息，生成日记、报告、快照与索引可能忠实保留这些内容。
- **非侵入边界**：Open Nova 不改写受支持 Agent Runtime 的历史数据或接管其执行；它会创建自己的 Runtime、CLI shim、可选 Skill 和托管服务。

<a id="development"></a>
## 📐 开发、测试与可复现发布

<details>
<summary><strong>展开开发与测试命令</strong></summary>

创建本地可编辑开发环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dashboard,rag-local]"
```

在隔离的 venv、`HOME`、`NOVA_HOME` 和固定业务时钟中运行发布测试集：

```bash
python tests/run_isolated_release_suite.py
```

运行确定性的前端与 Release Page 测试：

```bash
npm ci
node --check src/dashboard/app/static/js/app.js
npm run test:dashboard-live-context
npm run test:release-page
```

`npm run test:dashboard-live` 是显式 opt-in 且可能修改 Runtime 的真实 Dashboard Gate，只应对已播种的一次性 Runtime 运行。

复现 v1.0.1 发布构件：

```bash
python -B -m pip install -r requirements-release.txt
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)" \
python -B -m tools.release.build_release \
  --source-root . \
  --output-dir ../open-nova-release-artifacts \
  --expected-commit "$(git rev-parse HEAD)" \
  --expected-version 1.0.1
```

发布构建器只接受干净、已提交的 Git 工作树，并把输出写到仓库外。制品包括公开源码与 Runtime payload manifest、归一化 Runtime 归档、wheel、sdist、provenance 和 `SHA256SUMS`。

</details>

<a id="documentation"></a>
## 📄 文档导航

### 用户与日常操作

- ⚙️ <a href="docs/local-operations-runbook.zh-CN.md"><strong>中文本地操作 Runbook</strong></a>
- 📖 <a href="docs/new-user-onboarding-runbook.md">新用户安装手册（English）</a>
- 🧭 <a href="docs/cli-boundary.md">CLI 产品边界（English）</a>

### 集成与产品设计

- 🤖 <a href="docs/rag-external-agent-contract.md">nova-RAG 外部 Agent Runtime 合约（English）</a>
- 🧩 <a href="docs/nova-task-work-graph-reconciliation.md">Nova-Task 工作图谱对账</a>

### 发布、安全与项目历史

- ✅ <a href="docs/v1-release-assurance.md">v1.0.1 发布保证</a>
- 🧹 <a href="docs/production-clean-inventory.md">发布清理清单</a>
- 🧾 <a href="CHANGELOG.md">更新日志</a>
- 🔐 <a href="SECURITY.md">安全策略</a>
- 🕰️ <a href="HISTORY.md">公开项目历史</a>

<a id="license"></a>
## ⚖️ 许可证

Copyright © 2026 Neo-Isshin.

Open Nova 是自由软件，采用 [GNU 通用公共许可证第 3 版或任何后续版本](LICENSE)，SPDX 标识为 `GPL-3.0-or-later`。

## 🙏 致谢

Open Nova 的诞生得益于众多优秀的 AI 编程工具及其开源社区。感谢这些工具将 Token 用量和活动保存在本地日志中，使统一可视化、资产归集与跨 Runtime 记忆共享成为可能。

也感谢 <a href="https://getdesign.md">getdesign.md</a> 社区对 Dashboard 布局与视觉方向的启发。

<hr>

<a id="give-star"></a>
<div align="center">

<h2>⭐ Give me a Star</h2>

<p>
如果 Open Nova 帮助你把分散的 AI 工作沉淀为可检索、可复用的本地资产，<br>
欢迎点亮一颗 Star，让更多人发现这个项目。
</p>

<a href="https://github.com/Neo-Isshin/open-nova">
  <img src="https://img.shields.io/github/stars/Neo-Isshin/open-nova?style=for-the-badge&amp;logo=github&amp;label=Give%20me%20a%20Star&amp;color=F5B942" alt="Give Open Nova a Star">
</a>

</div>

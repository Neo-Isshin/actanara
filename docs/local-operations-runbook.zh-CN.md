# Open Nova 本地操作 Runbook

[English](local-operations-runbook.md) · [返回中文 README](../README.zh-CN.md)

状态：公开用户操作指南<br>
适用范围：当前 macOS 本地运行时

## 1. 本指南解决什么问题

本 Runbook 覆盖从首次安装到日常运行、历史数据生成、子系统操作、更新与故障排查的完整路径。README 负责五分钟快速开始；当您需要理解某个操作的前置条件、结果、日志或恢复方法时，以本指南为准。

## 2. 安装前检查

- macOS；
- Python `>= 3.11`；若受支持的 macOS 上未检测到兼容版本，安装器默认会安装受管理的独立 Python；
- `git` 和 `curl` 已在 `PATH` 中；若禁用 Python 自动安装，还需确保 `python3` 可用；
- 可以访问安装源码和所选 LLM/Embedding Provider；
- 本地至少预留足够空间安装 Dashboard 与可选的本地 Embedding 依赖。

安装器会自动检测受支持的 Agent runtime 路径，并允许您选择 Open Nova 要覆盖的工具。

## 3. 一键安装

```bash
zsh -c "$(curl -fsSL 'https://raw.githubusercontent.com/Neo-Isshin/open-nova/v1.0.1/install/bootstrap.sh')"
```

该版本化入口只用于全新安装。在写 cache 或 Runtime 前，它会拒绝任何已有的
目标/default/`NOVA_HOME`/location-pointer Runtime 或托管 Open Nova
LaunchAgent。源码获取会把最新正式 GitHub Release 固定到完整 commit，绝不回退
`main` 或 `origin/HEAD`。

安装器会引导您确认：

1. 界面和管线语言；
2. 外部 Agent runtime 路径；
3. LLM Provider、Endpoint、Model 和 API Key；
4. 是否启用 `nova-RAG`；
5. 本地或云端 Embedding 配置；
6. macOS 托管调度和 Dashboard 服务。

Provider Key 写入 Runtime 本地密钥目录 `$NOVA_HOME/state/secrets`。Open Nova 以 `0700` 权限创建目录、以 `0600` 权限创建各密钥文件；用户无需配置 Keychain，Pipeline 或 LaunchAgent 也可以无人值守运行，不会周期性要求重新授权。如配置云端 Embedding Key，也使用同一密钥目录。运行时设置、数据库、日志和生成资产保留在用户本地目录；发送到所配置 LLM/Embedding Provider 的请求遵循该 Provider 的端点与数据政策。

已有 `macos-keychain` 密钥引用仅用于兼容迁移。旧密钥可读时，Open Nova 会将其复制到 runtime-file 密钥目录，但不会自动删除旧 Keychain item；若 macOS 不允许读取旧 item，请在 Dashboard 中重新输入一次对应 Provider Key。

## 4. 安装摘要与运行时位置

安装完成后保存终端摘要，其中包含实际 Dashboard URL、运行时位置和常用命令。

| 路径 | 用途 |
| :--- | :--- |
| `~/.open-nova` | Runtime 主目录 |
| `~/.config/open-nova/location.json` | 当前活动 Runtime 指针 |
| `~/.open-nova/artifacts/diary` | 日记和周期总结 |
| `~/.open-nova/state/logs` | Pipeline、服务与安装相关日志 |
| `~/.open-nova/state/secrets` | Runtime 本地 Provider 密钥目录（目录 `0700`、文件 `0600`） |
| `~/.open-nova/bin/open-nova` | 产品 CLI |
| `~/Library/LaunchAgents/` | macOS 用户级托管服务 plist |

默认 Dashboard 地址：

```text
http://127.0.0.1:3036/dashboard
```

若 `3036` 被占用，安装器会自动选择其他端口；始终以安装摘要和当前设置为准。

## 5. 安装后基础验证

```bash
open-nova doctor
open-nova model show
open-nova onboard status
open-nova config show
```

按子系统进一步检查：

```bash
open-nova doctor --installer
open-nova doctor --pipeline
open-nova doctor --scheduler
open-nova doctor --rag        # 仅在启用 nova-RAG 时
```

Warning 不一定阻止运行；Error 或明确的 readiness failure 应先处理。重点确认 Runtime 指针、LLM Provider、Dashboard、调度器及可选 RAG Server 状态。

## 6. 配置 LLM Provider

1. 打开 Dashboard 设置页面；
2. 选择 Provider；
3. 确认 Endpoint 和 Model；
4. 输入 API Key；
5. 点击可用性检测；
6. 检测通过后保存。

随后验证：

```bash
open-nova model show
open-nova doctor --pipeline
```

不要把真实 Key 写入 README、日志、Shell 历史、Git 文件或普通 settings 字段。需要轮换时，应先在 Provider 端生成新 Key，再通过 Dashboard 替换。Open Nova 会将新 Key 写入 runtime-file 密钥目录，无需另行配置 Keychain。

## 7. 首次历史数据生成

### 7.1 计划预览

在 Dashboard 点击“生成历史数据”，选择日期范围，然后先点击“计划预览”。检查：

- 待生成的每日记录；
- 周报和月报；
- 已存在并将跳过的每日产物，以及将重新生成的周报和月报；
- 预计 LLM 调用量；
- `nova-RAG` 同步任务；
- 最终勾选的任务。

取消勾选不需要执行的项目。计划预览不会写入运行时。

### 7.2 排队生成

确认计划后点击“排队生成”。系统只执行勾选任务：

- Base Pipeline 生成每日记录与周期产物；
- Foundation 持久化标准化活动、报告和快照；
- Nova-Task 更新任务候选与证据；
- 启用且可用时，`nova-RAG` 同步检索索引。

较长范围可能运行较久。空白日期可以生成结构化占位产物，不一定需要 LLM。

### 7.3 监控、取消与重试

在“后台任务”和“消息”中查看状态。运行中的任务可请求取消；部分失败的任务可以只重试失败项。一个 Runtime 同时只应保留一个活动历史回填任务。

完成后检查：

- 日记页面；
- 周/月报告；
- AI 资产页面；
- Nova-Task 看板；
- `nova-RAG` 状态和搜索结果。

## 8. Base Pipeline 日常运行

手动运行当天或指定日期：

```bash
open-nova pipeline
open-nova pipeline YYYY-MM-DD
```

Pipeline 从已配置的外部工具路径读取活动，并根据实际证据完成工作区归属。不要依赖启动命令时的 CWD 作为唯一归属依据。

macOS 安装器默认注册托管 LaunchAgent。检查调度：

```bash
open-nova doctor --scheduler
```

如改由外部自动化或其他 cron 任务调用，请先避免与系统托管调度重复执行。

## 9. Dashboard 日常操作

Dashboard 用于：

- 查看每日、周、月报告；
- 查看实时用量和 AI 资产；
- 配置 LLM Provider、外部工具路径和调度；
- 规划历史回填；
- 监控后台任务与消息；
- 审阅 Nova-Task；
- 管理 `nova-RAG`。

Dashboard 异常时：

```bash
open-nova dashboard restart
open-nova doctor --installer
```

## 10. Nova-Task 操作

Nova-Task 是 Beta 子系统。它根据真实 Agent runtime 活动、对话和工具结果生成任务候选与证据，并允许人工接管。

```bash
open-nova task
```

操作原则：

- 一级或高影响任务应保留人工审阅；
- 普通子任务可由系统自动维护，但应定期检查错误归属；
- 导入 RFC、PRD、Roadmap 或 Audit 前先确认文档不含不应进入本地任务图谱的信息；
- 任务看板描述实际发生的工作，不等同于传统待办计划。

## 11. nova-RAG 操作

查看状态：

```bash
open-nova doctor --rag
```

搜索：

```bash
open-nova search "deployment issue" --top-k 5 --json
```

先预览维护计划：

```bash
open-nova rag-update --dry-run
open-nova rag-rebuild --dry-run
```

实际更新或重建会要求明确确认。外部 Agent runtime 应优先使用 Dashboard 的只读 Facade：

```text
GET  /api/rag/external/health
GET  /api/rag/external/stats
GET  /api/rag/external/contract
POST /api/rag/external/search
```

默认 Dashboard 基址为 `http://127.0.0.1:3036`，直接 RAG Server 默认基址为 `http://127.0.0.1:3037`。实际端口以 Runtime 设置为准。

## 12. 更新与回滚准备

预览更新：

```bash
open-nova update --dry-run
```

应用受保护更新：

```bash
open-nova update --apply
```

指定不可变 Commit（完整 40 或 64 位十六进制 object ID）：

```bash
open-nova update --dry-run --ref <full-commit-sha>
open-nova update --apply --ref <full-commit-sha>
```

省略 `--ref` 时，更新器会选择最新的非 draft、非 prerelease GitHub Release，
将其 tag peel 为完整 commit 后固定更新。自定义 `--source-url` 必须同时提供完整
commit。`--source-root` 会原样使用指定 checkout，不能与 `--ref` 组合。

更新前：

1. 确认当前任务已结束；
2. 保存 `open-nova doctor` 输出；
3. 备份 Runtime 设置和重要生成资产；
4. 记录当前 Runtime/Commit；
5. 先运行 Dry Run。

## 13. 日志与故障排查

优先检查：

```text
~/.open-nova/state/logs/
~/.open-nova/config/settings.json
~/.config/open-nova/location.json
```

常见问题顺序：

1. Runtime 指针是否正确；
2. Dashboard URL 和端口是否正确；
3. LLM Provider 是否检测通过；
4. `$NOVA_HOME/state/secrets` 是否为 `0700`、其中密钥文件是否为 `0600`，以及 Pipeline/LaunchAgent 是否使用同一个 `NOVA_HOME`；
5. 外部工具路径是否存在；
6. Scheduler 是否重复或未注册；
7. RAG Server 与活动索引是否就绪；
8. 后台任务是否失败、取消或可重试。

提交问题时保留命令、关键输出、日志路径、当前 Runtime 指针和相关 Doctor 输出；不要粘贴真实密钥。

## 14. 数据、备份与隐私

- 不要提交 Runtime 数据库、日志、缓存、生成日记或 Key；
- 截图公开前检查邮箱、用户名、项目名、本机路径和工作内容；
- 外部 LLM/Embedding 请求由用户配置的 Provider 处理；
- 定期备份重要日记、报告、Nova-Task 数据和 Runtime 设置；
- 删除或迁移 Runtime 前先停止托管服务并确认备份可恢复。

## 15. 完成检查清单

- [ ] Dashboard 可访问；
- [ ] LLM Provider 检测通过；
- [ ] Pipeline Doctor 无阻断错误；
- [ ] Scheduler 状态符合预期；
- [ ] 首次历史回填完成或处于可观察状态；
- [ ] 日记、AI 资产和 Nova-Task 有预期数据；
- [ ] 启用 `nova-RAG` 时，Server、索引和搜索正常；
- [ ] 已了解日志、更新和备份位置。

## 16. 相关参考

- [新用户安装指南](new-user-onboarding-runbook.md)
- [CLI 产品边界](cli-boundary.md)
- [RAG 外部 Agent 合约](rag-external-agent-contract.md)
- [返回中文 README](../README.zh-CN.md)

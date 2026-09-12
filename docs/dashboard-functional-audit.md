# Dashboard 功能审查

审查日期：2026-09-07。审查对象为真实 Dashboard 的 HTML、JavaScript、后端接口和已有浏览器回归；Nova-Task 页面不在本次修改范围。

## 功能清单与集成约束

| 用户目标 | 真实入口与实现 | 重构时必须保留的行为 |
| --- | --- | --- |
| 查看资产及使用量 | `showPage()` / `showPageFromHash()`；`page-home` / `page-static` / `page-overview` | 点击、直接链接、浏览器前进后退、当前导航状态、标题焦点一致；使用量独立导航仍显示实时与累计数据 |
| 查看日报、周报、月报 | `loadDiaryNav()`；`[data-diary-date]` / `[data-report-id]` / `[data-month-id]` | 展开月份和周、隐藏无活动日期、加载对应报告；移动端必须有日期/报告入口 |
| 查看任务落盘 | `/api/tasks`；`/tasks` | 首页任务数字和条目必须导向实际任务；不能接入隐藏且未实现的 `aaTaskOverview` |
| 复核 Skill 与经验 | `loadSkillAssetReview()` / `generateAndRegisterSelectedSkillAssets()`；`aaSkillAssetReview` | 勾选条目、显示原始判断与状态、可执行时才允许生成/注册；失败与成功区分 |
| 浏览和编辑 Skill | `renderSkills()` / `openSkillModal()` / `openSkillDoc()` | 工具分页、分组折叠、搜索、查看正文、权限允许时保存；保留返回上级 |
| 浏览 Agent 与文件 | `[data-aa-agent-row]` / `[data-aa-agent-tool]` / `[data-aa-agent-item]` / `[data-aa-doc-path]` | 模态框层级、打开实际文件、保存、取消与未保存内容保护；动态条目支持键盘 |
| 搜索长期记忆 | `runRagPageSearch()`；`ragPageSearchQuery` / `ragPageSearchResults` | 查询、Enter 提交、筛选、结果数限制、无结果与服务不可用区分 |
| 管理记忆服务 | `toggleNovaRagPower()`、Server 启停、迁移、覆盖检查、RAG Sync | 保留真实状态/API；用户日常检索优先，服务运维可收纳在次级区域 |
| 生成历史记录 | `openHistoryBackfillModal()`；`historyBackfillRunBtn` | 日期范围与周/月选择、先预览再排队、待生成项勾选、定时执行、覆盖确认、任务状态入口 |
| 查看后台处理 | `openBackgroundTasksModal()` / `handleBackgroundTaskAction()` | 实际任务阶段、尝试、用量与错误；取消/重试遵循后端返回的操作；刷新不覆盖别的弹窗 |
| 处理通知 | `openMsgboxModal()` / `markMsgboxRead()` / `handleMsgboxAction()` | 已读、跳页、后台动作；读取失败不能伪装成“没有消息” |
| 刷新资产和报告 | `loadAiAssets()` / `refreshAiAssetsSnapshot()`；`refreshWeeklyAssets()` / `refreshMonthlyAssets()` / 各报告总结刷新 | 区分重新读取和后台重建；禁用进行中按钮；任务完成后重新读取真实数据 |
| 导出和分享 | `openAiAssetsSharePreview()` / `openReportSharePreview()` / `window.print()` | PNG 预览、主题、复制/下载、失败重试；只分享允许的汇总信息 |
| 备份 | `openAiAssetsBackupModal()` / `saveAiAssetsBackupSettings()` / `runAiAssetsBackupNow()` / `verifyLatestAiAssetsBackup()` | 显示当前设置、保存、立即备份、最新备份校验和失败状态 |
| 配置模型与应用 | `openSettingsModal()` / `openLlmProviderModal()` | 设置各页签、进阶设置草稿、保存、模型链排序/新增/移除/测试；语言入口应具有真实效果 |
| 查看数据健康 | `loadFoundationOps()`、Daily QA、Pipeline Summary、修复审计、刷新记录 | 读取状态、选择业务日期、复制修复命令、允许范围内的修复；避免把内部术语作为首页主要操作 |
| 查看基础设施与来源 | `toggleAaInfrastructure()` / `openAaInfraActivityModal()` / `refreshToolConfigDiscovery()` | 展开设备与服务、近期活动、实际工具发现与登记、存储明细 |
| 跨设备操作 | `openMobileUtilities()`、通用模态框与文档编辑框 | 可见且可点击的入口、Tab 焦点约束、Escape 关闭、关闭后还原焦点、无页面横向溢出 |

## 原页面发现的问题

这些是改造前的代码审查发现。第 1–5 项与第 8–9 项已有对应实现及下方专项复测；第 6 项已补充键盘语义并由既有焦点机制处理；第 7 项旧示例没有被连接到新首页。

1. `openI18nTodo()` 只有“待实现”说明，尚未执行语言切换。
2. 移动端隐藏侧栏后，没有日记/周报/月报导航入口。
3. 消息读取失败会把计数置零，弹窗可能显示旧状态或“没有消息”；标记已读失败没有就地反馈。
4. 后台任务弹窗请求返回时没有核对弹窗代次。关闭弹窗或切到设置后，迟到的返回值仍可能覆盖当前弹窗，且重新开启轮询。每 5 秒替换整个内容也会收起用户已展开的任务细节。
5. 记忆搜索没有 Enter 提交，结果数只有 HTML 边界，函数未验证输入。
6. 部分动态 Agent 行、工具卡、Skill 标签和日报指标只有点击监听，不可通过键盘访问。
7. `showAgentDetail()`、`showSessionDetail()`、`showTokenDetail()`、`showCronDetail()` 和 `showSkillsDetail()` 含旧的静态示例，当前无调用点；新首页不能将它们当作真实详情。
8. 资产读取器依赖 `page-static` 可见状态；把数据用于首页和使用量页时，应明确处理首次加载、重复请求、空状态、失败重试和刷新后的同步。
9. 累计使用量图表当前依赖固定容器 `aaTools`、`aaHeatmapWrap`、`aaToolChart`、`aaModelChart`。移动容器时要保留图表销毁/重建，并在页面显示时校正尺寸。

## 验证记录

### 改造前浏览器回归

命令：

```sh
npx playwright test tests/dashboard-release-qa.spec.js tests/dashboard-share.spec.js tests/dashboard-backup.spec.js --project=chromium-desktop --workers=1
```

结果：**6 passed，31.1 秒**。已有用例在隔离环境中执行，并在用例内覆盖桌面/移动端及中文/英文组合。

覆盖：备份设置、备份执行与校验；周报/月报/资产 PNG 分享、主题、复制与下载；后台任务阶段详情；模型提供商链；RAG 外部来源模式、预检和解析状态；Tailscale/Funnel 状态。

这些用例不是“每个按钮均已验证”的证据。首页、导航调整和新资产接口仍需完成后专项复测。

### 真实隔离服务

启动脚本：`output/playwright/dashboard_qa_server.py`。脚本创建临时用户目录与 Actanara Runtime，只写入合成任务、报告、Skill、经验和累计用量，不使用用户的现有配置、凭证或工作数据。计划任务和 RAG 保持关闭。

```sh
.venv/bin/python output/playwright/dashboard_qa_server.py --port 8767
```

验证入口：`http://127.0.0.1:8767/dashboard`。前端静态改动即时可见；后端新增路由需重新启动隔离服务。

首次只读接口检查：`health`、`ai-assets`、`ai-assets/skill-assets`、`tasks`、`diary-list`、`background-tasks`、`msgbox`、`token-clock`、当周报告和当日日记均返回 HTTP 200。资产/任务/报告状态为 `ready`；实时用量为 `empty`，符合隔离环境没有实际运行 Session 的事实。

### 完成后复测

相同的 6 个既有浏览器回归用例再次全部通过：**6 passed，50.9 秒**。分享用例仅将累计用量图片的入口从旧资产页改为独立用量页，原有图片内容、隐私、复制、下载与主题断言保留。

最终后端合并后，主任务执行的完整 Dashboard Python 测试为 **346 passed，69.852 秒**，包含主技能库读取接口测试。最后一轮浏览器运行的 6 个用例也逐项通过，但其 worker 在测试完成后收尾卡住；确认该 worker 已无子浏览器/服务器后，仅终止本次测试的 worker，runner 随后退出 0。为区分用例结果与清理异常，又独立运行分享用例，**1 passed，8.1 秒**，正常退出，无需人工清理。没有终止用户浏览器或其他项目的进程。

在真实隔离服务上补充以下浏览器交互检查。失败场景仅通过当前测试浏览器拦截对应请求注入，不改变后端业务数据。

| 检查 | 结果与证据 |
| --- | --- |
| 首页真实资产计数 | 任务成果 3、主学习记录 1、Skill Pass 经验 1、主技能库 1、文档 3；不同权威来源没有相加成一个资产总量 |
| 首页搜索与筛选 | 搜索“备份”得到 1 条；报告筛选得到 3 条；点击记录可打开详情 |
| 精确文档阅读 | “打开相关页面”请求 `/api/dashboard/document?businessDate=2026-09-07&type=narrative`，HTTP 200，弹窗显示该文档实际 Markdown 正文 |
| 主技能库独立读取 | 首页“已保存技能”打开 `dashboardCanonicalSkills`，列表包含 `verify-restored-files`；不存在的搜索词得到 0 条；打开条目请求 `/api/dashboard/skills/asset-c7f1085deda9`，HTTP 200，正文与该合成 `SKILL.md` 的名称、说明和三个步骤一致 |
| 复核日期空态恢复 | 日期从 2026-09-07 切至无记录的 2020-01-01，显示空态且日期输入仍存在；切回 2026-09-07 后恢复 3 个复核条目。首次审查发现的空态丢失日期控件问题已修复并复测 |
| 独立用量菜单 | 保留累计 124.8K、今日 21.6K、146 条消息和工作区/模型图表；资产页不再包含 `aaKpi` 用量卡 |
| 用量读取失败与恢复 | 注入 HTTP 503 时显示 `Load failed: HTTP 503`；重新刷新成功后恢复真实指标和图表 |
| 图表可见性 | 工作区与模型图表分别为 427×320 和 427×360，切换页面后具有有效尺寸 |
| 日报与周报用量迁移 | 叙述/成果内容留在报告页；用量入口进入独立菜单，返回按钮回到相应报告；检查时全 DOM 无重复 ID |
| 日报重复打开 | 同一日期连续重开 2 轮，每轮用量容器数量始终为 1，仍连接 DOM，展开和返回均有效 |
| 日期路由与后退 | 日报 `?day=2026-09-07`、月报 `?month=2026-09` 刷新后恢复对应日期；移动端打开 09-06 日记后浏览器后退恢复首页 |
| 月报错误隔离 | 9 月已成功读取后，对 8 月注入 503；请求月份为 8 月、已渲染月份仍为 9 月，旧用量隐藏且标签保留 9 月，旧正文不可见，没有把旧数据显示成新月份 |
| 报告日期选择器 | 移动端“更多 → 报告与日记”可访问日期；搜索 `W37` 仅显示 `2026-W37 · 周报总览` |
| 语言切换 | 界面切换为英文并写入浏览器偏好；`pipeline.languageProfile` 切换前后均为 `zh`，没有修改报告生成语言 |
| RAG 搜索 | Enter 发出真实 `/api/memory/search` 请求并返回 HTTP 200；结果数为 0 时阻止请求并提示输入 1–20 的整数 |
| 消息失败与重试 | 消息接口 503 时计数为 `—`，弹窗显示错误与重试；恢复接口后计数为 0 并显示实际空状态 |
| 迟到弹窗响应 | 后台请求延迟 750ms，立即关闭并打开设置；响应到达后设置保持不变，后台弹窗轮询没有重新启动 |
| 后台详情轮询 | 展开合成 Pipeline 的任务和阶段，等待 5.4 秒；已发生第 2 次读取，两层详情仍展开，键盘焦点仍位于同一阶段 |
| 移动端布局 | 中文和英文 390×844 视口均为 `scrollWidth=390`；中文顶栏按钮无重叠，底部导航和更多菜单可操作 |

移动端截图：

- `output/playwright/dashboard-observatory-mobile-zh.png`
- `output/playwright/dashboard-observatory-mobile-en.png`

英文窄屏长标签省略问题已修复并复测：顶栏显示 `Tasks / Messages / History`，底部显示 `Assets / Usage / Library / Search / Maintain / More`。更新后的英文截图没有标签重叠或省略。

## 审查边界

此次没有使用用户的真实模型凭证执行报告生成、Skill 结晶或在线语义检索，也没有在用户环境实际启动/停止/安装后台服务。相应界面/API 入口、错误状态及已有隔离测试覆盖，不代表外部服务已做线上端到端验证。

隔离 Runtime 没有 managed source/venv pointers，`/api/settings/services/rag/preview` 会返回 `managed Runtime pointer is unavailable`。这是该合成运行环境的限制；没有为通过界面测试而安装真实服务。故障注入产生的 503、重启隔离服务期间的过期会话 401，与正常加载时的前端错误分开记录。

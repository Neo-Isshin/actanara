# Actanara v1.8.0 在线演示 / Public Dashboard demo

演示直接复用 v1.8.0 的 Dashboard HTML、CSS 和交互代码，以公开演示数据替代后端 API。首页聚焦已保存的任务成果、学习经验、技能和报告；Token、消息、缓存、模型与工作区排行保留在独立的「用量与活动」菜单。

The demo uses the production Dashboard frontend with a static data adapter. It never connects to a local runtime or provider account.

## 可以体验

- 资产总览：搜索和类型筛选，查看记录摘要与示例报告原文。
- 主技能库：搜索、分页和 Markdown 原文；按业务日期查看技能与经验提案。
- 用量与活动：原有公开用量样本、图表、工作区排行及分享图片。
- 日记与报告：W27 周报、7 月示例月报、07-03/07-05 日记及 07-04 空白日。
- 记忆检索：在示例经验中进行本页文本匹配；不是实时语义 RAG。
- 中英文界面、手机导航、消息和后台任务的示例状态。
- Nova-Task：保留现有任务页；已有任务的显示状态可在页面内调整。

## 演示边界

新资产、技能与报告正文为人工编写的示例；历史用量、旧日记和任务样本复用已公开且经过裁剪的静态快照。页面指标不代表当前运行状态，也不应将不同来源的计数相加。7 月示例月报复用公开样本说明布局，不代表完整月份的真实统计。

任务显示状态和消息已读只修改当前页面内存，刷新后恢复。生成历史数据、技能结晶/注册、备份、配置和服务操作需安装后使用。在线演示不要求输入模型凭证，也不读取或修改本机文件。不支持的写请求明确返回错误，不伪造执行成功。

Task display edits and message acknowledgements are in-memory examples. Generation, registration, backups, configuration and service changes require an installed runtime. No credentials or private file contents are bundled.

## 本地查看与同步

直接打开 `index.html`，或从仓库根目录启动静态服务器后访问 `/docs/dashboard-demo/`。支持 `?lang=zh` / `?lang=en` 与生产页面的 hash 链接。图表和 Markdown 依赖使用已保存的本地文件。

`tools/sync_dashboard_demo.mjs` 将生产前端转换为相对 URL，加入静态数据适配器和演示提示；不会重绘或替换生产组件。它输出可审阅的补丁，`--check` 可检查是否漂移；`--list` / `--chunk=0` 支持分批应用。

```sh
node tools/sync_dashboard_demo.mjs --check
npm run test:release-page
```

演示数据在 `js/demo-assets.js` 与 `js/static-mock.js`；专用提示样式在 `css/demo.css`。`tasks.html` 保持独立，不参与主 Dashboard 样式同步。

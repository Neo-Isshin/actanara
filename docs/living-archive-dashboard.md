# Living Archive Dashboard

The Living Archive is an opt-in Dashboard surface that coexists with the
main Actanara Dashboard.

## Routes

- `/dashboard` — asset-first Alpine Observatory Dashboard (default)
- `/dashboard-classic` — compatibility alias for the full operational Dashboard
- `/dashboard-preview` — Living Archive preview
- `/tasks` — Nova-Task, unchanged

Within the Archive preview, `#console` opens the **每日决策台**. It combines
the selected business day's newly projected assets with the current Nova-Task
L1 review queue, the latest Skill Pass Lesson/Skill proposals, and unread
operator messages. Task decisions, Lesson crystallization, Skill registration,
and message acknowledgement use the existing authenticated APIs and explicit
confirmation boundaries.

The Archive preview lives under `src/dashboard/app/static/archive/`. It remains
independent of the main Dashboard's asset-first integration described in
`docs/dashboard-assets.md`, and of the Nova-Task page.

## Selected design system

The preview uses **馆藏简报**, a quiet editorial ledger with compact page
introductions, warm paper tones, restrained semantic color, and denser
first-view reporting. It is the single Archive presentation rather than a
user-facing theme choice. The main Dashboard remains independently
available through `/dashboard` and `/dashboard-classic`.

The archive metaphor is visual only. Functional Chinese copy uses explicit
terms such as “加载数据”, “最近生成的资产”, “最后更新时间”, “日记与报告”,
and “数据来源状态”. Words such as “馆藏”, “装订”, “入馆”, “账簿”, “投影”,
and “新鲜度” are not used to explain controls, errors, empty states, or system
status. Product names such as Foundation, Skill Pass, Local FTS, and nova-RAG
remain visible with a plain-language Chinese description.

## Asset semantics

The Archive projects the private Skill Pass ledger into three public product
types:

- **Experience** — an evidence-bound causal experience.
- **Practice** — a Skill decision whose action and later result were verified.
- **Skill** — a finalized `create` or `extend` draft. It remains a draft until
  the user explicitly registers it.

`reference` and `discard` remain audit dispositions and never count as Archive
assets. Token, message, Session, model, and cache statistics are shown only in
the separate historical activity page.

## Data and operations

The preview consumes these authenticated endpoints:

- `GET /api/archive/v1/bootstrap`
- `GET /api/archive/v1/assets`
- `GET /api/archive/v1/assets/{asset_id}`
- `GET /api/archive/v1/skill-pass/runs`
- `GET /api/archive/v1/activity`

Archive responses expose stable IDs, bounded summaries, status, scores,
freshness, and relations. They do not expose absolute paths, raw Session text,
evidence locators, evidence record IDs, or Skill Markdown.

The extended pages reuse the authenticated Classic service contracts instead of
duplicating private data projections:

- **AI 资产** reads runtime detail and Skill Pass review proposals. Selected
  Lesson items can be crystallized, then finalized Skills can be registered via
  the existing two-step confirmation flow. The page only renders safe review
  fields; it never renders evidence locators or Skill body text.
- **运行与任务** reads TokenClock, message inbox, background tasks, Foundation
  QA/pipeline summaries, refresh jobs, and history-backfill plans.
- **日记与报告** reads daily Foundation projections and period reports,
  including KPI, Agent/workspace, model, task, cron, knowledge, lessons, and
  hourly activity summaries.
- **RAG 与索引** controls Local FTS sync/rebuild and the nova-RAG status,
  server, indexing, coverage, evaluation, external-source planning, and memory
  Skill registration endpoints.
- **系统设置** reads the redacted settings summary, detects Agent tools, tests
  and reorders the LLM fallback chain, and exposes diary/SQLite/service checks.
- **备份与恢复** persists the selected SQLite, diary/report, RAG, settings and
  runtime-manifest scopes, schedules or queues a backup, and verifies a backup.
  The backend intentionally has no overwrite-restore endpoint, so the restore
  tab reports that limitation instead of pretending to restore files.

Operations that require long system forms, file editors, service installation,
or Nova-Task editing remain one click away through the explicit
`/dashboard-classic` compatibility entry. This keeps the original capability
surface recoverable while the Archive page provides the common workflows.

## Failure semantics

The UI preserves `ready`, `empty`, `stale`, `degraded`, and `unsupported` as
different states. Unknown data is rendered as `—`; it is never converted to a
fake zero or replaced with demonstration metrics.

Write operations use the same CSRF, confirmation text, and backend safety checks
as the original Dashboard. Any operation that the backend does not support
(notably overwrite restore) is labelled as unavailable rather than simulated.

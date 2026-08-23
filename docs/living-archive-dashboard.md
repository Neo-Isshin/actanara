# Living Archive Dashboard

The Living Archive is an opt-in Dashboard surface that coexists with the
original Actanara Dashboard.

## Routes

- `/dashboard` — original Dashboard (default)
- `/dashboard-classic` — permanent recovery alias for the original Dashboard
- `/dashboard-preview` — Living Archive preview
- `/tasks` — Nova-Task, unchanged

The new UI lives under `src/dashboard/app/static/archive/`. It does not import
or modify the original Dashboard HTML, CSS, JavaScript, or Nova-Task page.

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

## Read-only API

The preview consumes these authenticated endpoints:

- `GET /api/archive/v1/bootstrap`
- `GET /api/archive/v1/assets`
- `GET /api/archive/v1/assets/{asset_id}`
- `GET /api/archive/v1/skill-pass/runs`
- `GET /api/archive/v1/activity`

Archive responses expose stable IDs, bounded summaries, status, scores,
freshness, and relations. They do not expose absolute paths, raw Session text,
evidence locators, evidence record IDs, or Skill Markdown.

## Failure semantics

The UI preserves `ready`, `empty`, `stale`, `degraded`, and `unsupported` as
different states. Unknown data is rendered as `—`; it is never converted to a
fake zero or replaced with demonstration metrics.

Write operations such as sync, backup, restore, and settings remain explicitly
labelled as previews until they are connected to the existing Classic services
with the same CSRF and confirmation contracts.

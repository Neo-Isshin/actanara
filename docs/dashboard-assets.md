# Asset-first Dashboard

`/dashboard` now opens the A / Alpine Observatory interface on the existing
FastAPI Dashboard. `/dashboard-classic` remains a compatibility alias for the
same operational surface. `/dashboard-preview` retains the separate Archive
preview. `/tasks` and its implementation are unchanged.

## Information architecture

- **Asset overview**: persisted completed task nodes, canonical learning
  records, canonical Skills, generated documents, recent outcomes and proposals.
- **Assets & Skills**: Skill Pass review and generation/registration, skill and
  document browsing/editing, runtime settings, storage and backups.
- **Usage & activity**: daily TokenClock, messages, cache, models, workspaces,
  lifetime usage, 30-day heatmaps and usage from viewed diaries/period reports.
- **Diaries & reports**: narrative, outcomes, lessons, report regeneration,
  export and sharing. Each report links to its measured usage in the usage menu.
- **Memory search**: search first, with indexing, service and source management
  immediately below it.
- **Data maintenance**: Foundation QA, generation results, recovery and history.

Top-level history generation, background jobs, inbox, settings, LLM chain,
language and backups use the existing authenticated handlers. None are demo
notifications or simulated writes. The mobile report picker includes full
dates, month IDs and ISO week IDs. Browser history persists exact day/month.

## Backend contracts

`GET /api/dashboard/summary` is a bounded read-only projection. It never calls
an LLM, scans external Session logs, or writes source data. Counts are separate
and may overlap conceptually; no grand asset total is calculated.

- Completed tasks: completed/done/settled task nodes, excluding groups and steps.
- Canonical learning notes and Skill Pass Lessons: separate source counts.
- Skills: canonical library assets, excluding per-tool installation copies.
- Reports/documents: ready narrative, technical and learning documents, not
  internal metric snapshots.
- Proposals: finalized Skill drafts and task candidates; a Skill draft count
  must not be interpreted as a pending-registration backlog.

Each count carries source, state and scope. Missing/unreadable data renders
`—`; genuine empty data renders `0`. Recent records are capped and labelled as
recent. The view reports limited source coverage rather than implying a full
inventory. Memory indexing is a capability of these assets, not an additional
asset count.

`GET /api/dashboard/document?businessDate=YYYY-MM-DD&type=narrative|technical|learning`
reads the exact generated document. Invalid, missing, ambiguous and unavailable
sources return distinct errors. Large content is explicitly marked truncated.
Both endpoints inherit session/CSRF boundaries and send `Cache-Control: no-store`.

`GET /api/dashboard/skills` and `GET /api/dashboard/skills/{id}` browse the
canonical Skill library and its exact Markdown independently of activity
snapshots. Installed per-tool copies stay in the original installed-Skills
browser. These read-only routes use opaque IDs and never accept caller paths.

## Integration notes

The new `static/js/dashboard.js` and `static/css/dashboard.css` extend the
existing `index.html`/`app.js`. Existing IDs, service endpoints, file editors,
confirmation requirements and access restrictions are retained. Usage widgets
are moved rather than copied so each chart/control ID stays unique. Asset
summary and Skill review load independently from activity snapshot availability.

Display language is a browser preference; switching it does not alter the
pipeline's report-generation language. Report content remains in its saved
language. Historical Skill Pass records open by business date and review ID.

Functional review and environment-dependent checks are recorded separately in
`docs/dashboard-functional-audit.md`. The isolated QA server uses only synthetic
data and must not be mistaken for the user's production runtime.

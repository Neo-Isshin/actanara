# Runtime integration coverage — v1.9.0

These adapters are included in v1.9.0. Coverage and validation differ by source;
the matrix below is the supported boundary. No external runtime is launched,
no credential store is read, and no source database is written by the collectors.

## What counts as support

Actanara distinguishes source discovery, readable conversation text, source-authored
artifacts, reusable canonical Skills, and usage events. Source task checklists are
**not** independently verified completed Nova-Task nodes. Installed Skill copies
are **not** extra canonical assets. Token usage stays in Usage & activity.

The AI Assets page has a collapsed **Runtime sources / 外部工具接入** panel showing the
actual readable counts, adapter validation level, partial/experimental state,
and read-only links to records and artifacts. It does not expose credentials,
tool payloads, thinking blocks, or encrypted conversation bodies.

## Coverage matrix

| Tool | Read boundary | Usage | Reuse and limitations |
| --- | --- | --- | --- |
| ZCode | `cli/db/db.sqlite`: session, message text parts, todo, model_usage | Per-request rows only; never add overlapping turn/message totals | Source task checklists; managed Skill registration to `skillsRoot`; real local sample checked |
| Cursor | Existing IDE session metadata, Agent CLI and explicit transcripts | Unavailable; no cloud billing calls | Adds installed Skill discovery and managed Skill registration; existing dialogue adapter retained |
| Antigravity | Existing CLI/IDE/app inventory; CLI input history; named brain artifacts | Local partial telemetry | New `task.md`, `implementation_plan.md`, `walkthrough.md` reader; other media/uploads and `.pb` bodies excluded |
| Qwen Code | `projects/**/chats/*.jsonl` and an explicit exports directory, ChatRecord format | Per-message `usageMetadata` when present | Deduplicate UUIDs and exclude forked copies; Skills supported; schema fixtures, not a real local-session acceptance test |
| Copilot CLI | `session-state/*/events.jsonl`, `plan.md`, checkpoint Markdown | Unavailable in this adapter | CLI only; NOT Copilot IDE/desktop `data.db` or cloud sessions; managed Skills supported; schema fixtures |
| Cline | Extension `tasks/*/{ui_messages,api_conversation_history,task_metadata}.json` | Explicit request usage from UI records, when present | Roles from API history, not guessed from UI `say:text`; SDK/new storage versions not covered; schema fixtures |
| Continue | IDE `sessions/*.json` history | Unavailable in this adapter | Undated messages remain undated; no automatic CLI format equivalence; schema fixtures |
| Aider | Explicit history files or Markdown copies in an imports directory | Unavailable | Original chat exports, not guessed message roles; inventory count is history files, not reconstructed sessions; schema fixtures |
| Grok Bot | Allowlisted `transcript.replicas` JSON blobs in desktop persistence cache | Unavailable | Experimental, cache-only, incomplete history; excludes streaming fragments, widgets, secret requests and attachments |

OpenClaw, Claude Code, Codex, Gemini CLI, Hermes and OpenCode keep their existing
adapters. The new source panel complements their existing surfaces; it does not
replace them or change the Nova-Task page.

## Paths and activation

Use **Settings → external tool paths** to inspect or change `externalTools`.
New catalog defaults are resolved without changing external tool configuration.
An absent path is not created by a parser. A missing tool is not an error.

Typical defaults (all are configurable):

- ZCode: `externalTools.zcode.databasePath` → `~/.zcode/cli/db/db.sqlite`.
- Qwen Code: `projectsRoot` → `~/.qwen/projects`; `exportsRoot` → `~/.qwen/exports`.
  Place compatible JSON/JSONL exports in the configured exports directory.
- Copilot CLI: `sessionsRoot` → `~/.copilot/session-state` (`COPILOT_HOME` supported).
  A `.copilot/data.db` alone is not treated as compatible CLI history.
- Cline: `tasksRoot` plus `taskRootCandidates` for common macOS/Linux Code/Cursor
  extension stores. Set the exact task directory for other IDEs or profiles.
- Continue: `sessionsRoot` → `~/.continue/sessions`.
- Aider: set `historyFiles` to the exact `.aider.chat.history.md` files to read,
  or copy histories into the configured `importsRoot`. No home/workspace-wide scan.
- Grok Bot: `cacheRoots` contains macOS/Linux desktop cache candidates. Windows
  or custom profiles require explicit paths; automatic Windows discovery is not
  claimed by this macOS/Linux project.

Example (merge only these fields through Settings; do not replace the full file):

```json
{
  "externalTools": {
    "aider": {"historyFiles": ["/workspace/example/.aider.chat.history.md"]},
    "cline": {"tasksRoot": "/path/to/cline/tasks"}
  }
}
```

The source panel is cached for up to 30 seconds; its Refresh button rereads sources. Browse shows at most 100 recent
messages (12,000 characters each); document views cap display at 200,000
characters. Complete source files stay with their owning runtime.

## Pipeline and safety

- The common adapters feed Foundation session/usage ingestion and the filtered
  dialogue collector. Existing report/learning generation can then consume the
  new dated material through its normal workflow. No LLM generation is triggered
  merely by opening a source panel.
- Explicit source artifacts enter the collector with an **unverified source
  artifact** label. File mtime is a save timestamp, not proof of task completion.
- Undated Continue/Cline messages remain browseable but are excluded from daily
  collection. Aider exports retain their original structure and are labelled
  source documents instead of reconstructed conversations.
- SQLite uses `mode=ro` and `query_only`; WAL is visible and part of change
  detection. Only named source tables/columns are queried.
- File readers reject symlinks below the configured boundary, malformed/oversized
  files, and unknown Grok cache versions. JSON/text input is capped at 16 MiB per
  file; source Markdown at 2 MiB; new transcript readers have a 64 MiB input budget
  per scan (ZCode caps dialogue text at 64 MiB); discovery at 5,000 matching files per root call.
  Unsupported records are not converted into successful empty executions.
- Skill registration still requires the existing explicit user action; existing
  customized Skills remain protected. This implementation does not automatically
  install Hooks, register MCP servers, change any provider, or access cloud APIs.

## Verification

Synthetic fixtures in `tests/test_extended_runtime_sources.py` cover source
boundaries, role attribution, duplicate events, fork exclusion, missing dates,
partial JSONL writes, schema drift, source documents, read-only behavior,
Foundation replay and Dashboard APIs. Local acceptance probes report counts
only; private transcripts and credentials must never be committed as fixtures.

```sh
PYTHONPATH=src:src/dashboard .venv/bin/python -m unittest discover -s tests -p 'test_extended_runtime_sources.py'
node tools/sync_dashboard_demo.mjs --check
npm run test:release-page
```

## Format references

- [Qwen ChatRecord implementation](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/services/chatRecordingService.ts)
- [Copilot CLI local directory contract](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference)
- [Copilot session event types](https://github.com/github/copilot-sdk/blob/main/nodejs/src/generated/session-events.ts)
- [Cline extension storage](https://github.com/cline/cline/blob/main/apps/vscode/src/core/storage/disk.ts)
- [Continue history storage](https://github.com/continuedev/continue/blob/main/core/util/history.ts)
- [Aider history configuration](https://aider.chat/docs/config/aider_conf.html)
- [ZCode Skills](https://zcode.z.ai/cn/docs/skill)
- [Cursor Skills](https://prod.cursor.com/docs/skills)
- [Antigravity walkthroughs](https://www.antigravity.google/docs/walkthrough)
- [Grok Bot cloud execution model](https://docs.x.ai/grok-bot/overview)

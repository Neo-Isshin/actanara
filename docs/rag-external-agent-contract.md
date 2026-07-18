# nova-RAG External Agent Read-Only Contract

Status: Actanara v1.0.x public interface contract

## Scope

This contract defines the initial nova-RAG recall API for external agents such as
Hermes and OpenClaw. It is read-only. External agents may inspect health, read
stats and search memory, but they must not write memories, create sources,
change settings, start or stop servers, run indexing or promote indexes.

## Allowed Dashboard Facade

External agents that call Actanara through the Dashboard process should use
only:

```text
GET  /api/rag/external/health
GET  /api/rag/external/stats
GET  /api/rag/external/contract
POST /api/rag/external/search
```

Search payload:

```json
{
  "query": "deployment issue",
  "topK": 5,
  "date": "2026-06-05",
  "role": "codex",
  "tags": ["coding"],
  "sourceSets": ["task-board-snapshot"],
  "lifecycle": "current-state",
  "workType": "task"
}
```

Only `query` is required. `topK`, `date`, `dateRange`, `project`, `role`,
`tags`, `sourceSets`, `lifecycle` and `workType` are optional. Filters that
target source-set, lifecycle or work-type metadata use raw machine contract
values; callers must not localize those values.

Local CLI wrapper:

```text
actanara search "deployment issue" --top-k 5 --json
```

The product wrapper calls only `POST /api/rag/external/search`. The legacy
compatibility form `actanara rag search-memory ...` remains available. Search
commands must not start the
nova-RAG server, run indexing, mutate memories or write settings.

## External-Agent Recall Priority

`externalAgentContract.usagePrompt` defines nova-RAG as auxiliary memory, not
an eager-search default. External agents must use evidence sources in this
order:

1. The current conversation, user-provided material and local authoritative
   files.
2. The host Agent Runtime's built-in or connected memory/history retrieval,
   when available.
3. nova-RAG only when the preceding sources do not provide enough reliable
   information.

If the user explicitly asks to query nova-RAG, that is the exception and
permits direct use. Results must still be treated as evidence rather than
authority. Subject matter alone, including questions about prior work or Open
Nova, must never be used to justify eager nova-RAG retrieval. The contract,
health and search Dashboard envelopes expose the same usage policy.

Search responses keep the standard `results` field. Result objects should
include:

```text
id
score
scoreComponents
source
date
agent
project
tags
workType
textPreview
provenance
```

Search responses use a stable schema versioned envelope:

```text
schemaVersion
available
reason
results
queryPlan
citationPack
eventAggregation
answerSynthesis
quality
retrievalController
agentic
externalAgentContract
```

`schemaVersion` is currently `2`. `available` tells callers whether the search
backend produced live results. `reason` is optional for successful searches and
required for unavailable/error responses. `externalAgentContract` confirms the
read-only boundary for Dashboard facade responses.

Search responses always include read-only Agentic evidence fields, even when
`available=false`:

```text
queryPlan
citationPack
eventAggregation
answerSynthesis
quality
retrievalController
agentic
```

These fields are derived from the final ranked results, or filled with empty
stable defaults when nova-RAG is unavailable. They do not call an LLM, write
memories, mutate indexes or change Diary generation behavior. The
`queryPlan` records server-side interpretation, filters, stages and subqueries.
The `citationPack` provides stable citation IDs, excerpts, score components and
provenance for external agent recall. `eventAggregation` groups related evidence
from the returned results without mutating memory. `answerSynthesis` is
extractive; it is not a generated answer and should be treated as evidence
summarization. `quality` reports key-term coverage, weak/strong status, whether
more evidence is needed, and flags such as `metaDiscussionTop`,
`hasNonMetaExactEvidence`, and `hasAuthoritativeEvidence`.
`retrievalController` reports the bounded server-side recall passes executed and
fused for this response. External agents should cite `citationPack` IDs when
possible and report `available=false` rather than inventing memory.

## External-Agent Multi-Pass Recall Guidance

The current external contract is read-only and does not grant agents index or
server lifecycle control. External agents may, however, issue repeated read-only
searches when the first recall is weak.

Every search runs a bounded server-side multi-pass recall controller before
returning. The server may execute separate dense query embeddings for
`baseline-hybrid`, `exact-entity-recall`, `subquery-rewrite`, and, when the
caller did not provide an explicit source-set filter,
`authoritative-source-pass`; results are then deduped, fused, optionally
reranked and quality-gated. Agents should still treat a search as evidence
rather than final truth. Recall should be considered weak when
`available=false`, `quality.needsMoreEvidence=true`, no results are returned,
top citations do not contain the user's key entities/dates/numbers, match
reasons are only generic dense similarity, `quality.flags.metaDiscussionTop` is
true for a factual question, `quality.flags.hasNonMetaExactEvidence` is false,
or the strongest evidence is episodic dialogue for a final-state question.

When recall remains weak after the server-side quality gate, agents should
perform a bounded client-side follow-up loop. Allow at most three external
search calls total: one initial search plus at most two follow-up calls chosen
adaptively from the options below; these are alternatives, not a mandatory
three-step sequence:

1. Exact pass: search the rarest IDs, dates, ports, commit hashes, file names,
   product names, or quoted phrases from the user request.
2. Rewrite pass: search one concise paraphrase with likely domain terms,
   synonyms, Chinese/English variants, and error/config/task words.
3. Filtered pass: reuse raw `sourceSet`, `lifecycle`, `workType`, `project`, or
   `dateRange` values discovered from prior responses or `/contract`.

Agents should inspect `quality.recommendations`. In particular,
`retry-with-meta-discussion-suppressed` means the first result is likely about a
prior RAG/eval discussion rather than the underlying fact, while
`retry-with-authoritative-source-pass` means the agent should prefer durable
source sets or current-state/canonical lifecycle filters.

Agents must merge these read-only results manually, dedupe by `resultId`,
`provenance.sourceId`, `provenance.dedupeKey`, or citation excerpt, and prefer
exact entity coverage plus high authority/provenance over the top rank from a
single weak call. If repeated searches remain weak or contradictory, agents
must report that nova-RAG did not provide reliable evidence instead of
inventing missing facts.

The client-side loop uses one monotonic 90-second wall-clock deadline across
all calls and allows at most three attempted searches total. Each HTTP search
passes the current `remainingBudgetMs`; that value decreases across retries and
must not be reset per call. The Dashboard facade forwards a bounded remainder
to the direct server, whose per-search cap is 60 seconds. The packaged Python
helper exposes `ExternalSearchBudget` for this shared state. CLI HTTP timeout
defaults to 65 seconds (60-second server cap plus transport grace), while an
explicit smaller timeout remains valid.

Local synchronous embedding workers cannot be forcefully terminated by Python
without risking process state. A timeout/cancellation therefore returns a
stable degraded envelope. `workerTelemetry.workerState` reports
`running_after_timeout`, `running_after_cancel`, or `finished`, and
`capacityPermitHeld=true` means server capacity remains occupied until the real
worker exits. Agents must not treat such a response as proof that computation
was hard-cancelled or immediately retry into exhausted capacity.

When nova-RAG is disabled, missing, rebuilding or the server is unavailable,
search returns `available=false`, an empty `results` list, and empty/stable
`queryPlan`, `citationPack`, `eventAggregation`, `answerSynthesis`, `quality`,
`retrievalController` and `agentic` fields instead of triggering indexing or
server lifecycle actions.
Unavailable responses keep `schemaVersion=2`, preserve the caller's normalized
`query` and `topK` inside `queryPlan`, and set evidence statuses to
`unavailable` or the most specific failure status.

## Allowed Embedding/Search Server API

When an external agent is configured to call the nova-RAG server directly, the
allowed endpoints are:

```text
GET  /health
GET  /stats
POST /search
```

`POST /encode` remains an internal embedding compute endpoint and is not part
of the external-agent contract.

In macOS v1 the direct nova-RAG server is loopback-only. New Settings writes
reject non-loopback hosts; legacy non-loopback values remain readable but
status/doctor report `Blocked: rag-server-non-loopback`, lifecycle start is
refused, and no health probe is sent. The direct ASGI boundary also rejects
non-loopback clients with HTTP `403`.

`/encode` has an additional internal authorization boundary. Every managed
server start rotates a random token in the selected Runtime's private state
directory with mode `0600`; the token value is not placed in argv, process
state JSON, logs, Settings, or API responses. Candidate sync reads that private
file and sends the token only in the internal request header. A missing or
unsafe-permission token blocks sync as
`rag-internal-authorization-unavailable`; a missing or incorrect request header
receives HTTP `403` as `rag-internal-authorization-required`.

## Rejected Mutations

The Dashboard external namespace rejects mutation attempts with HTTP `403`:

```text
PUT  /api/rag/external/settings
POST /api/rag/external/index/run
POST /api/rag/external/server/start
POST /api/rag/external/server/stop
POST /api/rag/external/memory/write
POST /api/rag/external/source/create
```

The direct nova-RAG server also rejects initial mutation paths with HTTP `403`:

```text
POST /memory/write
POST /memories
POST /index/run
POST /index/rebuild
```

These rejections must not create v2 build-run records, mutate settings, start
or stop processes, write memories, create sources or touch the production
legacy index.

## Compatibility Boundary

- Default nova-RAG mode is `v2`.
- `legacy` and `v2-shadow` modes are retired from the production search path;
  callers must report `available=false` rather than falling back to a legacy
  index when an active v2 manifest is not ready.
- Production `~/.actanara/reserved/rag/v2/manifest.json` is the active search
  boundary. External-agent calls must not run indexing, promote candidates, or
  mutate the active manifest.
- Generated Diary Markdown, embedded JSON shape, prompt payloads and output
  paths are outside this contract.
- Operator-only Dashboard endpoints such as `/api/rag/settings`,
  `/api/rag/index/run` and `/api/rag/server/start` are not external-agent APIs.
- Agentic response fields are read-only evidence metadata; they do not grant
  external agents write access to memories or index lifecycle controls.

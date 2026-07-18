# Nova-Task Work Graph Reconciliation Contract

Date: 2026-07-02 HKT

Nova-Task v2 is the task authority. `TASK_BOARD.md` is a projection, and
daily technical reports are evidence input, not direct task authority.

## Authority Layers

Nova-Task reconciliation writes into three logical layers:

| Layer | Purpose | Write policy |
| --- | --- | --- |
| Project Graph | Durable L1-L5 task structure. | L1 requires manual review. Validated L2-L5 writes are direct. |
| Evidence Ledger | Daily observed progress, blockers, validation, risks and guard rejections. | Direct, idempotent by business event. |
| Planning Overlay | Planned L1 roots and planning-document intent. | Manual L1 review; pathless planned roots are allowed. |

## L1 Boundary

L1 project/product roots are the only graph additions that require manual
confirmation. Recon may propose an L1 through `candidate_parent_tasks`, but it
must not create an L1 directly from ordinary daily evidence.

After an L1 is confirmed, recon may create L2-L5 descendants under it if the
parent chain is valid.

## Direct L2-L5 Writes

Recon can create L2-L5 nodes directly when all conditions hold:

- the chain is rooted in a real active graph L1;
- each new node is exactly one level below its resolved parent;
- each item includes `level_decision`;
- evidence is present;
- no existing equivalent child already covers the work.

Recon-created nodes are agent nodes. They may be attached under either
agent-managed or human-managed parents, but they remain agent-managed unless a
human explicitly edits or claims them.

The LLM may reference earlier new nodes in the same YAML with
`proposed_ref` / `proposed_parent_ref`, allowing one pass to create:

```text
existing L1
└─ new L2
   └─ new L3
      └─ new L4
         └─ new L5
```

If the LLM skips levels, the guard rejects the graph write and records the item
as evidence with `levelValidation=rejected`. The rejected item remains visible
through Dashboard recent direct writes as `deferredByGuard`.

## Routing Hints

`routing_hints` are inferred inside the same recon LLM call. They are not
hardcoded product rules and are not persistent authority.

Routing hints must be derived from current inputs:

- repeated technical terms;
- file/module names;
- subsystem names;
- workspace names;
- existing graph titles.

They are stored only as evidence ledger events with:

```text
hintEventType = routing_hint
nonAuthority = true
scope = current_reconciliation_only
```

## Node Management

Every graph node carries two management fields in metadata:

```text
createdBy = agent | human
managedBy = agent | human
```

`createdBy` is immutable provenance. `managedBy` is the current control boundary
for structure and status semantics.

Agent-managed nodes are created from observed engineering evidence. Human-managed
nodes are created or claimed by planning import, Dashboard edits, or operator
actions.

Recon may create, move, dedupe, and archive agent-managed nodes. Recon must not
change the structure of human-managed nodes. It may only attach evidence and
emit status signals for them.

When a human edits an agent-managed node, the node becomes human-managed. When a
human-managed node is created or moved under an agent-managed branch, the path
from that node to the root becomes human-managed; sibling branches are unchanged.

## Status Semantics

Human-managed task node statuses are:

```text
planned
active
blocked
paused
done
archived
```

`completed` may exist as a legacy compatibility value, but new human status
writes should use `done`.

Agent-managed node lifecycle values are:

```text
automatic
settled
stale
archived
```

For new L2-L5 agent nodes, recon may provide `status_decision`, but
deterministic validation maps it into the agent lifecycle:

- `settled`: implementation/fix/check finished with validation evidence;
- `automatic`: observed work or durable visible workstream/deliverable.

Daily technical evidence is normally observed work. It must not become planned
work unless the source explicitly describes future intent.

When observed agent descendants are materialized under a planned human-managed
ancestor, Nova-Task automatically promotes that planned ancestor to `active` and
writes an audit entry:

```text
auto_promote_planned_ancestor_to_active
```

For L1 compatibility, an additional audit action may be written:

```text
auto_promote_planned_l1_to_active
```

## Evidence Idempotency

Evidence events are idempotent by business event, not by recon artifact path.
The event identity is based on:

- business date;
- source type;
- event type;
- summary;
- evidence;
- matched node id.

This prevents repeated recon runs for the same technical report from inflating
the evidence ledger merely because each run writes a new artifact file.

If the LLM materially changes summary or evidence text, Nova-Task treats it as a
new evidence event.

## Recon Artifacts

Each work-graph recon run writes:

- a Markdown artifact containing the raw LLM YAML;
- a `.summary.json` sidecar with counts, guard validation groups, action counts,
  and pending-review counts.

The summary sidecar is for operator diagnostics and tests. It is not task
authority.

## Dashboard Surfaces

Dashboard Nova-Task reads SQLite authority. Current review behavior:

- L1 Review shows manual L1 proposals only;
- L2-L5 direct writes appear through graph/audit views;
- guard-rejected graph intents appear as read-only `deferredByGuard` entries in
  recent direct writes;
- Task Board uses title color to show management ownership: black for
  human-managed nodes and orange for agent-managed nodes. The usage guide in
  the Task Board summarizes import-first planning, ownership switching, and
  five-level overview editing.
- legacy candidate merge/supersede routes remain compatibility surfaces, not the
  normal L2-L5 production workflow.

## Operational Commands

Standalone recon:

```bash
PYTHONPATH=~/.actanara/app/source/src \
python3 ~/.actanara/app/source/advanced/pipeline/run_nova_task_work_graph_reconciliation.py \
  --date YYYY-MM-DD \
  --technical-report /path/to/技术进展-YYMMDD.md \
  --limit 120 \
  --apply
```

Full daily pipeline:

```bash
PYTHONPATH=~/.actanara/app/source/src \
python3 ~/.actanara/app/source/advanced/pipeline/run_daily_pipeline.py YYYY-MM-DD
```

## Regression Expectations

Before promoting runtime changes, run at least:

```bash
python3 -m unittest tests.test_nova_task_work_graph_reconciliation \
  tests.test_dashboard_nova_task_review \
  tests.test_pipeline_contract
```

For broad changes, run:

```bash
python3 -m unittest discover -s tests
```

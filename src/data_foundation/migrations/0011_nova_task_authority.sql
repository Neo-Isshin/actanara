CREATE TABLE IF NOT EXISTS nova_task_nodes (
    node_id TEXT PRIMARY KEY,
    parent_node_id TEXT REFERENCES nova_task_nodes(node_id),
    node_type TEXT NOT NULL CHECK (node_type IN ('track', 'workstream', 'task', 'subtask', 'step')),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'planned', 'blocked', 'completed', 'archived')),
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    sort_order INTEGER NOT NULL DEFAULT 0,
    scope_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_nova_task_nodes_parent
    ON nova_task_nodes(parent_node_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_nova_task_nodes_status
    ON nova_task_nodes(status);

CREATE TABLE IF NOT EXISTS nova_task_aliases (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL REFERENCES nova_task_nodes(node_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL CHECK (alias_type IN ('manual', 'llm_suggested', 'keyword', 'legacy_id')),
    created_at TEXT NOT NULL,
    UNIQUE(node_id, alias, alias_type)
);

CREATE TABLE IF NOT EXISTS nova_task_events (
    event_id TEXT PRIMARY KEY,
    business_date TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_path TEXT,
    source_sha256 TEXT,
    source_locator TEXT,
    matched_node_id TEXT REFERENCES nova_task_nodes(node_id),
    event_type TEXT NOT NULL CHECK (event_type IN ('progress', 'completion_signal', 'remaining_work', 'candidate_parent', 'candidate_subtask', 'unresolved')),
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low', 'unknown')),
    summary TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nova_task_events_date
    ON nova_task_events(business_date, event_type);

CREATE TABLE IF NOT EXISTS nova_task_candidates (
    candidate_id TEXT PRIMARY KEY,
    candidate_type TEXT NOT NULL CHECK (candidate_type IN ('parent_task', 'subtask', 'status_update', 'scope_alias')),
    proposed_title TEXT NOT NULL,
    proposed_parent_node_id TEXT REFERENCES nova_task_nodes(node_id),
    matched_node_id TEXT REFERENCES nova_task_nodes(node_id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'merged', 'rejected', 'deferred')),
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low', 'unknown')),
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    source_event_id TEXT REFERENCES nova_task_events(event_id),
    source_fingerprint TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_nova_task_candidates_status
    ON nova_task_candidates(status, updated_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_nova_task_candidates_identity
    ON nova_task_candidates(candidate_type, proposed_title, COALESCE(proposed_parent_node_id, ''), source_fingerprint);

CREATE TABLE IF NOT EXISTS nova_task_reconciliation_decisions (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT REFERENCES nova_task_candidates(candidate_id),
    decision_type TEXT NOT NULL CHECK (decision_type IN ('confirm', 'rename_confirm', 'merge', 'attach_as_subtask', 'reject', 'defer', 'status_update')),
    actor TEXT NOT NULL,
    reason TEXT,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nova_task_audit_log (
    audit_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    node_id TEXT REFERENCES nova_task_nodes(node_id),
    candidate_id TEXT REFERENCES nova_task_candidates(candidate_id),
    decision_id TEXT REFERENCES nova_task_reconciliation_decisions(decision_id),
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_nova_task_audit_time
    ON nova_task_audit_log(occurred_at);

CREATE TABLE IF NOT EXISTS nova_task_exports (
    export_id TEXT PRIMARY KEY,
    export_type TEXT NOT NULL CHECK (export_type IN ('task_board_markdown', 'dashboard_snapshot')),
    target_path TEXT,
    content_sha256 TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

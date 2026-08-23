CREATE TABLE IF NOT EXISTS environment_deliverables (
    deliverable_id TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    deliverable_type TEXT NOT NULL DEFAULT 'other',
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('unknown', 'planned', 'produced', 'verified', 'superseded', 'retired')),
    version TEXT NOT NULL DEFAULT '',
    private_locator TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'technical-pass',
    first_seen_date TEXT NOT NULL,
    last_seen_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_environment_deliverables_status_date
    ON environment_deliverables(status, last_seen_date DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS environment_deliverable_events (
    event_id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    deliverable_id TEXT NOT NULL REFERENCES environment_deliverables(deliverable_id) ON DELETE CASCADE,
    business_date TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('observed', 'planned', 'produced', 'verified', 'updated', 'superseded', 'retired')),
    summary TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL DEFAULT 'medium'
        CHECK (confidence IN ('high', 'medium', 'low')),
    source TEXT NOT NULL DEFAULT 'technical-pass',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_environment_deliverable_events_date
    ON environment_deliverable_events(business_date DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_environment_deliverable_events_deliverable
    ON environment_deliverable_events(deliverable_id, business_date DESC, created_at DESC);

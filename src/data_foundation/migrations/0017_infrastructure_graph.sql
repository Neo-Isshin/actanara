CREATE TABLE IF NOT EXISTS infrastructure_entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('device', 'service')),
    canonical_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unknown',
    location TEXT NOT NULL DEFAULT '',
    host_entity_id TEXT REFERENCES infrastructure_entities(entity_id),
    endpoint TEXT NOT NULL DEFAULT '',
    port TEXT NOT NULL DEFAULT '',
    protocol TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_date TEXT,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS infrastructure_entity_aliases (
    entity_id TEXT NOT NULL REFERENCES infrastructure_entities(entity_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(entity_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_infrastructure_entity_aliases_lookup
    ON infrastructure_entity_aliases(normalized_alias);

CREATE INDEX IF NOT EXISTS idx_infrastructure_entities_type_status
    ON infrastructure_entities(entity_type, status, updated_at);

CREATE TABLE IF NOT EXISTS infrastructure_events (
    event_id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    entity_id TEXT NOT NULL REFERENCES infrastructure_entities(entity_id) ON DELETE CASCADE,
    business_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    field TEXT NOT NULL DEFAULT '',
    previous_value TEXT NOT NULL DEFAULT '',
    current_value TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL DEFAULT 'medium',
    source TEXT NOT NULL DEFAULT 'learning-pass',
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_infrastructure_events_entity_date
    ON infrastructure_events(entity_id, business_date DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_infrastructure_events_date
    ON infrastructure_events(business_date DESC, created_at DESC);

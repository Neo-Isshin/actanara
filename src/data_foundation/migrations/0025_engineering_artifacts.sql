CREATE TABLE IF NOT EXISTS engineering_artifacts (
    artifact_id TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    artifact_type TEXT NOT NULL DEFAULT 'other',
    status TEXT NOT NULL DEFAULT 'observed'
        CHECK (status IN ('observed', 'produced', 'verified', 'superseded', 'archived')),
    version TEXT NOT NULL DEFAULT '',
    locator TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'asset-reconciliation',
    first_seen_date TEXT NOT NULL,
    last_seen_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_engineering_artifacts_status_date
    ON engineering_artifacts(status, last_seen_date DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS engineering_artifact_aliases (
    artifact_id TEXT NOT NULL REFERENCES engineering_artifacts(artifact_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(artifact_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_engineering_artifact_aliases_lookup
    ON engineering_artifact_aliases(normalized_alias);

CREATE TABLE IF NOT EXISTS engineering_artifact_events (
    event_id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    artifact_id TEXT NOT NULL REFERENCES engineering_artifacts(artifact_id) ON DELETE CASCADE,
    business_date TEXT NOT NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('observed', 'produced', 'verified', 'updated', 'superseded', 'archived')),
    summary TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL DEFAULT 'medium'
        CHECK (confidence IN ('high', 'medium', 'low')),
    source TEXT NOT NULL DEFAULT 'asset-reconciliation',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_engineering_artifact_events_date
    ON engineering_artifact_events(business_date DESC, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_engineering_artifact_events_artifact
    ON engineering_artifact_events(artifact_id, business_date DESC, created_at DESC);

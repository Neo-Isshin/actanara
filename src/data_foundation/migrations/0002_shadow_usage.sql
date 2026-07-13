CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_key TEXT NOT NULL REFERENCES tool_sources(tool_key),
    external_session_key TEXT NOT NULL,
    started_at TEXT,
    last_active_at TEXT,
    initial_cwd TEXT,
    agent_key TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(tool_key, external_session_key)
);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_key TEXT NOT NULL REFERENCES tool_sources(tool_key),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    external_event_key TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    business_date TEXT NOT NULL,
    model_key TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    protocol_total_tokens INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 1,
    source_artifact_id INTEGER REFERENCES source_artifacts(id),
    raw_locator_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(tool_key, external_event_key)
);

CREATE INDEX IF NOT EXISTS usage_events_business_date_idx
    ON usage_events(business_date, tool_key);

CREATE TABLE IF NOT EXISTS activity_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    occurred_at TEXT NOT NULL,
    business_date TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    observed_path TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    source_artifact_id INTEGER REFERENCES source_artifacts(id),
    raw_locator_json TEXT NOT NULL DEFAULT '{}',
    confidence TEXT NOT NULL,
    UNIQUE(session_id, occurred_at, evidence_type, normalized_path)
);

CREATE TABLE IF NOT EXISTS asset_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    business_date TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    asset_key TEXT NOT NULL,
    count_value INTEGER,
    size_mb REAL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
    UNIQUE(business_date, asset_type, asset_key, ingestion_run_id)
);

CREATE TABLE IF NOT EXISTS daily_tool_usage (
    business_date TEXT NOT NULL,
    tool_key TEXT NOT NULL REFERENCES tool_sources(tool_key),
    tokens INTEGER NOT NULL,
    messages INTEGER NOT NULL,
    sessions INTEGER NOT NULL,
    api_calls INTEGER NOT NULL,
    source_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
    PRIMARY KEY(business_date, tool_key)
);

CREATE TABLE IF NOT EXISTS daily_model_usage (
    business_date TEXT NOT NULL,
    model_key TEXT NOT NULL,
    tool_key TEXT NOT NULL REFERENCES tool_sources(tool_key),
    tokens INTEGER NOT NULL,
    messages INTEGER NOT NULL,
    sessions INTEGER NOT NULL,
    source_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
    PRIMARY KEY(business_date, model_key, tool_key)
);

CREATE TABLE IF NOT EXISTS daily_project_usage (
    business_date TEXT NOT NULL,
    project_id_or_bucket TEXT NOT NULL,
    tool_key TEXT NOT NULL REFERENCES tool_sources(tool_key),
    tokens INTEGER NOT NULL,
    messages INTEGER NOT NULL,
    active_sessions INTEGER NOT NULL,
    evidence_confidence TEXT NOT NULL,
    source_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
    PRIMARY KEY(business_date, project_id_or_bucket, tool_key)
);

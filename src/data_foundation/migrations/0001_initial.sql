CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_sources (
    tool_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    retired_at TEXT,
    last_successful_ingestion_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_type TEXT NOT NULL,
    business_date TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    adapter_versions_json TEXT NOT NULL DEFAULT '{}',
    error_summary TEXT
);

CREATE TABLE IF NOT EXISTS source_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_key TEXT NOT NULL REFERENCES tool_sources(tool_key),
    canonical_path TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    byte_size INTEGER,
    modified_at TEXT,
    cursor_json TEXT,
    last_ingestion_run_id INTEGER REFERENCES ingestion_runs(id),
    status TEXT NOT NULL,
    UNIQUE(tool_key, canonical_path)
);

CREATE TABLE IF NOT EXISTS ingestion_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
    tool_key TEXT NOT NULL REFERENCES tool_sources(tool_key),
    artifact_id INTEGER REFERENCES source_artifacts(id),
    error_code TEXT NOT NULL,
    message TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL UNIQUE,
    canonical_root TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    UNIQUE(project_id, alias, alias_type)
);

CREATE TABLE IF NOT EXISTS system_components (
    component_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    component_type TEXT NOT NULL,
    authority TEXT NOT NULL,
    status TEXT NOT NULL,
    version TEXT,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    entrypoints_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retired_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_system_components_type
    ON system_components(component_type);

CREATE INDEX IF NOT EXISTS idx_system_components_status
    ON system_components(status);

CREATE TABLE IF NOT EXISTS ai_asset_usage_source_files (
    tool_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    file_mtime REAL NOT NULL,
    file_size INTEGER NOT NULL,
    session_id TEXT,
    usage_group TEXT,
    session_count_unit INTEGER NOT NULL DEFAULT 1,
    parser_version TEXT NOT NULL,
    parsed_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    PRIMARY KEY(tool_name, source_path)
);

CREATE TABLE IF NOT EXISTS ai_asset_usage_records (
    tool_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    raw_input_tokens INTEGER,
    timestamp TEXT,
    model TEXT,
    message_count INTEGER NOT NULL DEFAULT 1,
    usage_group TEXT,
    metadata_json TEXT,
    PRIMARY KEY(tool_name, source_path, event_index),
    FOREIGN KEY(tool_name, source_path)
        REFERENCES ai_asset_usage_source_files(tool_name, source_path)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ai_asset_usage_records_tool
    ON ai_asset_usage_records(tool_name);

CREATE INDEX IF NOT EXISTS idx_ai_asset_usage_records_timestamp
    ON ai_asset_usage_records(timestamp);

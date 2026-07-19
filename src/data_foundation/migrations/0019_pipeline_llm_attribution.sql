CREATE TABLE IF NOT EXISTS pipeline_llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_run_id INTEGER NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    stage_id TEXT NOT NULL,
    pass_id TEXT,
    call_id TEXT NOT NULL,
    chunk_id TEXT,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    provider_id TEXT,
    model TEXT,
    api_type TEXT,
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    cache_read_tokens INTEGER CHECK (cache_read_tokens IS NULL OR cache_read_tokens >= 0),
    cache_write_tokens INTEGER CHECK (cache_write_tokens IS NULL OR cache_write_tokens >= 0),
    reasoning_tokens INTEGER CHECK (reasoning_tokens IS NULL OR reasoning_tokens >= 0),
    total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
    usage_source TEXT NOT NULL DEFAULT 'unavailable' CHECK (
        usage_source IN ('response', 'estimated', 'unavailable')
    ),
    estimation_method TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    fallback_count INTEGER NOT NULL DEFAULT 0 CHECK (fallback_count >= 0),
    failure_class TEXT,
    error_summary TEXT,
    attempts_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (pipeline_run_id, call_id)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_llm_calls_run_stage
    ON pipeline_llm_calls(pipeline_run_id, stage_id, id);

CREATE INDEX IF NOT EXISTS idx_pipeline_llm_calls_run_status
    ON pipeline_llm_calls(pipeline_run_id, status, id);

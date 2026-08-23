CREATE TABLE IF NOT EXISTS skill_candidates (
    candidate_id TEXT PRIMARY KEY,
    source_assessment_id TEXT,
    source_lesson_id TEXT,
    technical_chain_id TEXT NOT NULL,
    source_pipeline_run_id INTEGER,
    source_date TEXT NOT NULL,
    source_agent TEXT NOT NULL DEFAULT '',
    language_profile TEXT NOT NULL DEFAULT 'zh',
    canonical_key TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    model_tier TEXT NOT NULL CHECK (
        model_tier IN ('strong', 'recommended', 'lesson_only', 'reject')
    ),
    effective_tier TEXT NOT NULL CHECK (
        effective_tier IN ('strong', 'recommended', 'lesson_only', 'reject')
    ),
    worthiness_json TEXT NOT NULL DEFAULT '{}',
    compatible_tools_json TEXT NOT NULL DEFAULT '[]',
    excluded_tools_json TEXT NOT NULL DEFAULT '[]',
    evidence_state TEXT NOT NULL DEFAULT 'pending' CHECK (
        evidence_state IN (
            'pending', 'dialogue_confirmed', 'agent_reported',
            'unverified', 'contradicted', 'stale'
        )
    ),
    procedure_authority TEXT NOT NULL DEFAULT 'none',
    status TEXT NOT NULL DEFAULT 'suggested' CHECK (
        status IN (
            'suggested', 'draft_queued', 'drafting', 'drafted',
            'publish_queued', 'publishing', 'published', 'dismissed',
            'blocked_unverified', 'blocked_safety', 'no_skill', 'failed',
            'superseded'
        )
    ),
    source_fingerprint TEXT NOT NULL UNIQUE,
    blocked_reasons_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_skill_candidates_source_date
    ON skill_candidates(source_date, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_skill_candidates_status_tier
    ON skill_candidates(status, effective_tier, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_skill_candidates_technical_chain
    ON skill_candidates(technical_chain_id, created_at DESC);

CREATE TABLE IF NOT EXISTS skill_promotion_jobs (
    job_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES skill_candidates(candidate_id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('draft', 'draft_and_publish', 'publish')),
    origin TEXT NOT NULL CHECK (origin IN ('manual', 'automatic')),
    status TEXT NOT NULL CHECK (
        status IN (
            'queued', 'running', 'completed', 'blocked_unverified',
            'blocked_safety', 'no_skill', 'failed'
        )
    ),
    language_profile TEXT NOT NULL DEFAULT 'zh',
    target_tools_json TEXT NOT NULL DEFAULT '[]',
    effective_target_tools_json TEXT NOT NULL DEFAULT '[]',
    idempotency_key TEXT NOT NULL UNIQUE,
    requested_by TEXT NOT NULL,
    artifact_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    error_summary TEXT,
    receipt_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_promotion_jobs_status
    ON skill_promotion_jobs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_skill_promotion_jobs_candidate
    ON skill_promotion_jobs(candidate_id, created_at DESC);

CREATE TABLE IF NOT EXISTS skill_artifacts (
    artifact_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES skill_candidates(candidate_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision > 0),
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    markdown TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    technical_chain_sha256 TEXT NOT NULL,
    procedure_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    verification_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    compatible_tools_json TEXT NOT NULL DEFAULT '[]',
    excluded_tools_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'validated', 'ready_to_publish', 'published', 'blocked')
    ),
    validation_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE(candidate_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_skill_artifacts_candidate
    ON skill_artifacts(candidate_id, revision DESC);

CREATE INDEX IF NOT EXISTS idx_skill_artifacts_slug
    ON skill_artifacts(slug, created_at DESC);

CREATE TABLE IF NOT EXISTS skill_registrations (
    registration_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES skill_artifacts(artifact_id) ON DELETE CASCADE,
    artifact_revision INTEGER NOT NULL CHECK (artifact_revision > 0),
    tool_id TEXT NOT NULL,
    target_root TEXT,
    target_path TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'registered', 'failed', 'superseded')),
    content_sha256 TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    error_summary TEXT,
    receipt_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    registered_at TEXT,
    UNIQUE(artifact_id, tool_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_registrations_artifact
    ON skill_registrations(artifact_id, status, tool_id);

CREATE INDEX IF NOT EXISTS idx_skill_registrations_tool
    ON skill_registrations(tool_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS skill_promotion_budget_reservations (
    operation TEXT NOT NULL CHECK (operation IN ('draft', 'publish')),
    job_id TEXT NOT NULL REFERENCES skill_promotion_jobs(job_id) ON DELETE CASCADE,
    day_key TEXT NOT NULL,
    week_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('reserved', 'consumed', 'released')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(operation, job_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_promotion_budget_day
    ON skill_promotion_budget_reservations(operation, day_key, status);

CREATE INDEX IF NOT EXISTS idx_skill_promotion_budget_week
    ON skill_promotion_budget_reservations(operation, week_key, status);

ALTER TABLE skill_candidates ADD COLUMN evidence_source_type TEXT NOT NULL DEFAULT 'technical_chain';
ALTER TABLE skill_candidates ADD COLUMN skill_experience_id TEXT;
ALTER TABLE skill_candidates ADD COLUMN dialogue_evidence_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE skill_candidates ADD COLUMN evidence_chain_sha256 TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_candidates_skill_experience
    ON skill_candidates(skill_experience_id)
    WHERE evidence_source_type = 'skill_experience' AND skill_experience_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_skill_candidates_evidence_source
    ON skill_candidates(evidence_source_type, source_date, status, created_at DESC);

ALTER TABLE skill_artifacts ADD COLUMN evidence_source_type TEXT NOT NULL DEFAULT 'technical_chain';
ALTER TABLE skill_artifacts ADD COLUMN skill_experience_id TEXT;
ALTER TABLE skill_artifacts ADD COLUMN evidence_chain_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE skill_artifacts ADD COLUMN dialogue_evidence_sha256 TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_skill_artifacts_evidence_chain
    ON skill_artifacts(evidence_source_type, evidence_chain_sha256, created_at DESC);

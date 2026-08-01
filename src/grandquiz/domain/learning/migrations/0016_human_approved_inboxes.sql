CREATE TABLE IF NOT EXISTS material_discovery_batches (
    batch_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL,
    source_policy TEXT NOT NULL,
    provider_adapter TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ready', 'failed')),
    error_code TEXT,
    error_message TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS material_candidates (
    candidate_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES material_discovery_batches(batch_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    snippet TEXT NOT NULL,
    provider_adapter TEXT NOT NULL,
    provider_rank INTEGER NOT NULL,
    quality_flags TEXT NOT NULL,
    eligibility TEXT NOT NULL CHECK (
        eligibility IN ('eligible', 'duplicate_batch', 'existing_resource', 'insufficient_preview')
    ),
    duplicate_resource_id TEXT,
    why TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'approved', 'rejected')),
    review_request_id TEXT,
    review_reason TEXT,
    reviewed_at REAL,
    acquisition_run_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_material_candidates_batch_rank
ON material_candidates(batch_id, provider_rank, candidate_id);

CREATE TABLE IF NOT EXISTS acquisition_activation_outbox (
    run_id TEXT PRIMARY KEY REFERENCES acquisition_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS eval_inbox_candidates (
    candidate_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('verdict_correction', 'blind_grading_label')),
    dedupe_key TEXT NOT NULL,
    source_request_id TEXT NOT NULL,
    payload_schema_version TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('active', 'superseded')),
    review_status TEXT NOT NULL CHECK (review_status IN ('pending', 'approved', 'rejected')),
    release_gate_eligible INTEGER NOT NULL,
    privacy_review_required INTEGER NOT NULL,
    review_request_id TEXT,
    review_reason TEXT,
    reviewed_at REAL,
    created_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_inbox_active_dedupe
ON eval_inbox_candidates(source_kind, dedupe_key)
WHERE lifecycle_status = 'active';

CREATE TABLE IF NOT EXISTS eval_import_commands (
    request_id TEXT PRIMARY KEY,
    manifest_hash TEXT NOT NULL,
    candidate_ids TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_dataset_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    redaction_profile TEXT NOT NULL,
    candidate_count INTEGER NOT NULL,
    eligible_blind_count INTEGER NOT NULL,
    exploratory_count INTEGER NOT NULL,
    items TEXT NOT NULL,
    created_at REAL NOT NULL
);

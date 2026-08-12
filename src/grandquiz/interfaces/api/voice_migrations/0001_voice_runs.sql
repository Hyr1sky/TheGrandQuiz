CREATE TABLE voice_runs (
    voice_run_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    assessment_session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    mime_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    client_duration_ms INTEGER NOT NULL,
    audio_sha256 TEXT NOT NULL,
    hint_set_id TEXT NOT NULL,
    hint_count INTEGER NOT NULL,
    hints_applied INTEGER NOT NULL,
    hints_payload TEXT NOT NULL,
    provider_attempt_count INTEGER NOT NULL,
    active_provider_attempt_id TEXT,
    reviewable_transcript TEXT,
    retryable INTEGER NOT NULL,
    error_code TEXT,
    error_stage TEXT,
    error_reason TEXT,
    trace_id TEXT NOT NULL,
    run_span_id TEXT NOT NULL,
    retry_request_id TEXT,
    submit_request_id TEXT,
    submitted_answer_sha256 TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL
);

CREATE INDEX idx_voice_runs_assessment_question
    ON voice_runs (assessment_session_id, question_id, created_at);

CREATE INDEX idx_voice_runs_status_expiry
    ON voice_runs (status, expires_at);

CREATE TABLE voice_provider_attempts (
    provider_attempt_id TEXT PRIMARY KEY,
    voice_run_id TEXT NOT NULL REFERENCES voice_runs(voice_run_id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    provider_request_id TEXT,
    latency_ms INTEGER,
    error_code TEXT,
    error_reason TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    UNIQUE(voice_run_id, attempt_number)
);

CREATE TABLE voice_request_cancellations (
    request_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_facts (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    source_event_seq INTEGER NOT NULL CHECK (source_event_seq >= 0),
    source_event_ts REAL NOT NULL,
    payload_schema_version TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    redaction_profile TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learning_facts_trace
ON learning_facts(trace_id, source_event_seq, event_id);

CREATE INDEX IF NOT EXISTS idx_learning_facts_type_entity
ON learning_facts(event_type, entity_id);

CREATE TABLE IF NOT EXISTS learning_fact_outbox (
    event_id TEXT PRIMARY KEY
        REFERENCES learning_facts(event_id) ON DELETE CASCADE,
    published INTEGER NOT NULL DEFAULT 0 CHECK (published IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_learning_fact_outbox_pending
ON learning_fact_outbox(published, event_id);

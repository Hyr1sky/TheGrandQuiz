CREATE TABLE IF NOT EXISTS vocabulary_terms (
    term_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    term_key TEXT NOT NULL,
    label_zh TEXT NOT NULL,
    aliases TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'deprecated')),
    replacement_term_id TEXT,
    taxonomy_version TEXT NOT NULL,
    UNIQUE(namespace, term_key)
);

CREATE TABLE IF NOT EXISTS knowledge_classifications (
    classification_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES knowledge_items(item_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    supersedes_id TEXT REFERENCES knowledge_classifications(classification_id),
    primary_kind TEXT NOT NULL,
    orientations TEXT NOT NULL,
    classified_by TEXT NOT NULL CHECK (classified_by IN ('rule', 'model', 'user')),
    review_status TEXT NOT NULL CHECK (
        review_status IN ('proposed', 'approved', 'rejected')
    ),
    lifecycle_status TEXT NOT NULL CHECK (
        lifecycle_status IN ('active', 'superseded', 'retracted')
    ),
    trace_id TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    UNIQUE(item_id, revision)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_classification_active
ON knowledge_classifications(item_id)
WHERE lifecycle_status = 'active' AND review_status = 'approved';

CREATE TABLE IF NOT EXISTS resource_revision_classifications (
    classification_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES resource_revisions(revision_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    supersedes_id TEXT REFERENCES resource_revision_classifications(classification_id),
    primary_source_genre TEXT NOT NULL,
    classified_by TEXT NOT NULL CHECK (classified_by IN ('rule', 'model', 'user')),
    review_status TEXT NOT NULL CHECK (
        review_status IN ('proposed', 'approved', 'rejected')
    ),
    lifecycle_status TEXT NOT NULL CHECK (
        lifecycle_status IN ('active', 'superseded', 'retracted')
    ),
    trace_id TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    UNIQUE(revision_id, revision)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_resource_revision_classification_active
ON resource_revision_classifications(revision_id)
WHERE lifecycle_status = 'active';

CREATE TABLE IF NOT EXISTS tag_candidates (
    candidate_id TEXT PRIMARY KEY,
    raw_value TEXT NOT NULL,
    namespace TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK (
        review_status IN ('proposed', 'approved', 'rejected')
    ),
    trace_id TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tag_assignments (
    assignment_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES knowledge_items(item_id),
    term_id TEXT NOT NULL REFERENCES vocabulary_terms(term_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    supersedes_id TEXT REFERENCES tag_assignments(assignment_id),
    assigned_by TEXT NOT NULL CHECK (assigned_by IN ('rule', 'model', 'user')),
    review_status TEXT NOT NULL CHECK (
        review_status IN ('proposed', 'approved', 'rejected')
    ),
    lifecycle_status TEXT NOT NULL CHECK (
        lifecycle_status IN ('active', 'superseded', 'retracted')
    ),
    trace_id TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    UNIQUE(item_id, term_id, revision)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tag_assignment_active
ON tag_assignments(item_id, term_id)
WHERE lifecycle_status = 'active';

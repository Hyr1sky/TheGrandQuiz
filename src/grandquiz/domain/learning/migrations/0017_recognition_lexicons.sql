CREATE TABLE IF NOT EXISTS recognition_lexicons (
    lexicon_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL,
    builder_version TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY (revision_id) REFERENCES resource_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS recognition_lexicons_by_revision
    ON recognition_lexicons (revision_id, lexicon_id);

CREATE TABLE IF NOT EXISTS recognition_lexicon_current (
    revision_id TEXT PRIMARY KEY,
    lexicon_id TEXT NOT NULL,
    FOREIGN KEY (revision_id) REFERENCES resource_revisions(revision_id),
    FOREIGN KEY (lexicon_id) REFERENCES recognition_lexicons(lexicon_id)
);

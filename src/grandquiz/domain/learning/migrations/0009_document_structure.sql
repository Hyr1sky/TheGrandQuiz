-- ADR-0008 / DS-S1：获批正文版本化，并以确定性 DocumentNode 树保存结构。
CREATE TABLE resource_revisions (
    revision_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    raw_content TEXT NOT NULL,
    trusted INTEGER NOT NULL,
    UNIQUE (resource_id, content_hash)
);
CREATE INDEX idx_resource_revisions_resource ON resource_revisions (resource_id);

ALTER TABLE resources ADD COLUMN current_revision_id TEXT
    REFERENCES resource_revisions(revision_id);

CREATE TABLE document_nodes (
    node_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES resource_revisions(revision_id) ON DELETE CASCADE,
    parent_node_id TEXT REFERENCES document_nodes(node_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('document', 'section', 'paragraph', 'list', 'table', 'code')),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    depth INTEGER NOT NULL CHECK (depth >= 0),
    title TEXT,
    section_path TEXT NOT NULL,
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset >= start_offset),
    content_fingerprint TEXT NOT NULL,
    synthetic INTEGER NOT NULL,
    summary TEXT
);
CREATE INDEX idx_document_nodes_revision_order
    ON document_nodes (revision_id, ordinal, node_id);
CREATE INDEX idx_document_nodes_parent ON document_nodes (parent_node_id);

-- ADR-0008 / DS-S2：Evidence 从 knowledge_items JSON 投影为可检索、可校验的精确引用。
CREATE TABLE knowledge_item_evidence (
    item_id TEXT NOT NULL REFERENCES knowledge_items(item_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    quote TEXT NOT NULL,
    quote_hash TEXT NOT NULL,
    revision_id TEXT REFERENCES resource_revisions(revision_id),
    node_id TEXT REFERENCES document_nodes(node_id),
    section_path TEXT,
    start_offset INTEGER CHECK (start_offset >= 0),
    end_offset INTEGER CHECK (end_offset >= 0),
    page_start INTEGER CHECK (page_start >= 1),
    page_end INTEGER CHECK (page_end >= 1),
    block_id TEXT,
    resolved INTEGER NOT NULL CHECK (resolved IN (0, 1)),
    PRIMARY KEY (item_id, ordinal),
    CHECK ((start_offset IS NULL) = (end_offset IS NULL)),
    CHECK (end_offset IS NULL OR end_offset > start_offset),
    CHECK (page_end IS NULL OR page_start IS NULL OR page_end >= page_start),
    CHECK (
        resolved = 0 OR (
            revision_id IS NOT NULL AND node_id IS NOT NULL AND
            section_path IS NOT NULL AND start_offset IS NOT NULL AND end_offset IS NOT NULL
        )
    )
);
CREATE INDEX idx_knowledge_item_evidence_revision
    ON knowledge_item_evidence (revision_id, start_offset);
CREATE INDEX idx_knowledge_item_evidence_node
    ON knowledge_item_evidence (node_id, start_offset);

-- ADR-0008 / DS-S4：只索引 current revision 的节点投影；切换由 Store 同事务维护。
CREATE VIRTUAL TABLE document_nodes_fts USING fts5(
    node_id UNINDEXED,
    revision_id UNINDEXED,
    resource_id UNINDEXED,
    title,
    section_path,
    summary,
    body,
    tokenize = 'unicode61'
);

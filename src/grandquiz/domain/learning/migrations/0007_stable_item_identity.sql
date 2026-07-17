-- ADR-0007：旧的序号 KnowledgeItem 身份无法可靠映射到概念指纹，备份后清空知识数据重建。
-- preferences 不锚定 KnowledgeItem，保留。其余知识与关联账按带外键的新形状重建。
-- 若测试/恢复流程从更新 schema 显式回退 user_version 后重放本迁移，先移除 ADR-0008 的
-- revision/tree 表；正常从 v6 升级时它们尚不存在，IF EXISTS 为空操作。
DROP TABLE IF EXISTS document_nodes_fts;
DROP TABLE IF EXISTS knowledge_item_evidence;
DROP TABLE IF EXISTS document_nodes;
DROP TABLE IF EXISTS resource_revisions;
DROP TABLE IF EXISTS asked_questions;
DROP TABLE IF EXISTS difficulty;
DROP TABLE IF EXISTS learning_memory;
DROP TABLE IF EXISTS knowledge_items;
DROP TABLE IF EXISTS resources;

CREATE TABLE resources (
    resource_id  TEXT PRIMARY KEY,
    url          TEXT    NOT NULL,
    raw_content  TEXT,
    content_hash TEXT,
    trusted      INTEGER NOT NULL,
    status       TEXT    NOT NULL,
    topic        TEXT
);

CREATE TABLE knowledge_items (
    item_id     TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES resources(resource_id) ON DELETE CASCADE,
    concept     TEXT NOT NULL,
    summary     TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    confidence  REAL NOT NULL,
    concept_key TEXT
);
CREATE INDEX idx_items_resource ON knowledge_items (resource_id);

CREATE TABLE learning_memory (
    memory_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id              TEXT UNIQUE REFERENCES knowledge_items(item_id) ON DELETE CASCADE,
    state                TEXT    NOT NULL,
    consecutive_correct  INTEGER NOT NULL,
    verdict_history      TEXT    NOT NULL
);

CREATE TABLE asked_questions (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  TEXT NOT NULL REFERENCES knowledge_items(item_id) ON DELETE CASCADE,
    question TEXT NOT NULL
);
CREATE INDEX idx_asked_questions_item ON asked_questions (item_id);

CREATE TABLE difficulty (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL UNIQUE REFERENCES knowledge_items(item_id) ON DELETE CASCADE,
    tier    INTEGER NOT NULL
);

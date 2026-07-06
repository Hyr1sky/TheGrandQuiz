-- learning 领域的持久化表：任务 / 资源 / 知识点 / 学习记忆。
-- 走独立 db 文件（与 kernel 的 trace.db 分开），自有 PRAGMA user_version 与迁移序列。
-- **无任何时间戳列**（决策 2：创建 / 深读 / 答题的时序来自事件流的 seq/ts，非墙上时间），
-- 保证 replay 逐字节一致。list 字段（evidence / verdict_history）存 JSON 文本
-- （写入侧 json.dumps sort_keys 稳定序）；trusted 存 0/1 整数（SQLite 无原生 bool）。

-- LearningTask：学习主题容器与考核范围。domain 可空（粗领域，人工可选填）。
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    title   TEXT NOT NULL,
    domain  TEXT
);

-- LearningResource：挂在 task 下的学习资源。raw_content / content_hash 深读前为空；
-- trusted 恒 0（抓取内容不可信，注入防护）；status ∈ pending/read/failed。
CREATE TABLE resources (
    resource_id  TEXT PRIMARY KEY,
    task_id      TEXT    NOT NULL,
    url          TEXT    NOT NULL,
    raw_content  TEXT,
    content_hash TEXT,
    trusted      INTEGER NOT NULL,
    status       TEXT    NOT NULL
);
CREATE INDEX idx_resources_task ON resources (task_id);

-- KnowledgeItem：深读产出的最小知识单元，资源内唯一（ADR-0002）。evidence 存 JSON
-- （list[Evidence]，每条 {quote, locator}）；concept_key 二期跨资源归并预留，MVP 恒空。
CREATE TABLE knowledge_items (
    item_id     TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    concept     TEXT NOT NULL,
    summary     TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    confidence  REAL NOT NULL,
    concept_key TEXT
);
CREATE INDEX idx_items_resource ON knowledge_items (resource_id);

-- Learning Memory：被追踪的薄弱概念（薄弱 / 观察中两态；销账 = 删行，不是第三态）。
-- 锚定 item_id（ADR-0002 / ADR-0003）。consecutive_correct：薄弱恒 0、观察中恒 1
-- （不变量由 ConceptRecord 的 model_validator 在反序列化时兜底）。verdict_history 存 JSON。
CREATE TABLE learning_memory (
    item_id              TEXT PRIMARY KEY,
    state                TEXT    NOT NULL,
    consecutive_correct  INTEGER NOT NULL,
    verdict_history      TEXT    NOT NULL
);

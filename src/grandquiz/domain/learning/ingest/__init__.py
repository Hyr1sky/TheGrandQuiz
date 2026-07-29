"""材料摄入管线：抓取（fetch/web_fetch）→ 深读（reader，MVP 唯一 subagent）→ 编排入库（pipeline）。

包公开面只有 ``pipeline.py`` 的编排入口（``ingest_resource`` / ``IngestResult``，见下方
re-export）；``fetch``/``web_fetch``/``reader`` 各自的类型走精确子模块路径导入
（如 ``from grandquiz.domain.learning.ingest.reader import Reader``），不在此处再转手。
"""

from grandquiz.domain.learning.ingest.pipeline import (
    IngestResult,
    PreparedIngest,
    abort_ingest,
    commit_prepared_ingest,
    emit_prepared_ingest_committed,
    ingest_resource,
    persist_prepared_ingest,
    prepare_ingest,
)

__all__ = [
    "IngestResult",
    "PreparedIngest",
    "abort_ingest",
    "commit_prepared_ingest",
    "emit_prepared_ingest_committed",
    "ingest_resource",
    "persist_prepared_ingest",
    "prepare_ingest",
]

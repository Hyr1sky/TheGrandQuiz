"""Run a disposable real FastAPI/SSE fixture for Web interaction and visual QA."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from grandquiz.domain.learning.citations import ground_items
from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.providers.base import Completion, Message, Role, ToolSpec, Usage

CONTENT = """\
# Runtime

Runtime 负责管理智能体的执行循环与状态机转移。它通过事件总线接收输入、派发处理器，
并在每个 turn 结束时提交新的状态快照。所有可观察的副作用都必须进入事件记录。

## Events

事件是系统唯一的事实来源。每个事件不可变并按因果顺序追加，订阅者可以独立重放。
事件总线保证了时间有序的传播，同一 turn 内的历史在处理过程中稳定不变。

## Durable processors

durable processor 订阅事件并执行有状态逻辑。失败后继续当前 turn 会让后续副作用依赖不完整状态，
破坏事件历史的因果一致性与可重放性，因此必须阻断当前 turn 并触发恢复流程。

## Recovery

恢复流程从事件日志和最近的成功快照重建状态，重放尚未完成的 turn，直到重新达到一致性边界。
"""


class _FixtureProvider:
    def __init__(self) -> None:
        self._question_calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        if role == "enrich":
            self._question_calls += 1
            return Completion(
                text=json.dumps(
                    {
                        "question": (
                            "durable processor 失败后为什么要阻断当前 turn？"
                            f"（fixture #{self._question_calls}）"
                        ),
                        "options": [
                            "避免后续副作用依赖不完整状态",
                            "为了清空浏览器缓存",
                            "为了跳过事件记录",
                        ],
                        "answer_index": 0,
                        "cited_evidence": ["失败后继续当前 turn 会让后续副作用依赖不完整状态"],
                    },
                    ensure_ascii=False,
                ),
                usage=Usage(prompt_tokens=180, completion_tokens=45),
            )
        return Completion(
            text=(
                '{"answer":"失败后继续执行会让后续副作用依赖不完整状态，'
                '破坏事件历史的因果一致性与可重放性，因此必须阻断当前 turn。",'
                '"citations":[{"node_key":"n0","quote":"'
                '失败后继续当前 turn 会让后续副作用依赖不完整状态"}]}'
            ),
            usage=Usage(prompt_tokens=180, completion_tokens=45),
        )


def _seed(db_path: Path) -> None:
    resource = LearningResource.create(url="file://fixture/agent-runtime.md").model_copy(
        update={
            "raw_content": CONTENT,
            "content_hash": hashlib.sha256(CONTENT.encode()).hexdigest(),
            "status": "read",
            "topic": "Agent Runtime：事件总线与可恢复执行",
            "trusted": True,
        }
    )
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="durable processor 失败处理",
        summary="失败后必须阻断当前 turn，避免因果链继续建立在不完整状态上。",
        evidence=[Evidence(quote="失败后继续当前 turn 会让后续副作用依赖不完整状态")],
        confidence=0.96,
    )
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    item = ground_items(snapshot, [item])[0]
    with LearningPersistence(db_path) as persistence:
        persistence.store.replace_snapshot(resource, [item])


def main() -> None:
    temp_dir = tempfile.TemporaryDirectory(prefix="grandquiz-web-fixture-")
    root = Path(temp_dir.name)
    learning_db = root / "learning.db"
    _seed(learning_db)
    app = create_app(
        settings=ApiSettings(
            learning_db_path=learning_db,
            trace_db_path=root / "trace.db",
        ),
        provider=_FixtureProvider(),
    )
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()

"""Run a disposable real FastAPI/SSE fixture for Web interaction and visual QA."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import uvicorn

from grandquiz.domain.learning.citations import ground_items
from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.ingest.fetch import FetchResult
from grandquiz.domain.learning.ingest.web_search import SearchResult
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    ProviderStreamEvent,
    Role,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)
from grandquiz.providers.speech import TranscriptionRequest, TranscriptionResult

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

![不可信远程图片](https://attacker.invalid/should-not-load.png)

```text
receive_event -> persist_trace -> notify_observers -> checkpoint_state -> resume_from_sequence
```

| 输入事件 | 持久事务 | 通知阶段 | 成功快照 | 游标恢复 | 错误分类 | 恢复决策 | 回放评测 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| turn | 先写 | 后发 | 快照 | 续传 | 失败 | 阻断 | 一致 |

## Recovery

恢复流程从事件日志和最近的成功快照重建状态，重放尚未完成的 turn，直到重新达到一致性边界。
"""


class _FixtureSearchProvider:
    adapter_name = "fixture-search"

    async def search(
        self,
        query: str,
        *,
        limit: int,
        domains: tuple[str, ...] = (),
    ) -> list[SearchResult]:
        del domains
        suffix = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
        return [
            SearchResult(
                title="Agent Memory 工程指南",
                url=f"https://example.com/agent-memory/{suffix}",
                snippet="一份关于 Agent Memory 分层、检索与工程权衡的候选材料摘要。",
                adapter=self.adapter_name,
                rank=1,
            )
        ][:limit]


class _FixtureHttpSource:
    async def fetch(self, url: str, *, max_bytes: int) -> FetchResult:
        encoded = CONTENT.encode("utf-8")
        assert len(encoded) <= max_bytes
        return FetchResult(
            requested_url=url,
            final_url=url,
            content=CONTENT,
            content_type="text/markdown",
            content_hash=hashlib.sha256(encoded).hexdigest(),
        )


class _FixtureProvider:
    def __init__(self, resource_id: str) -> None:
        self._question_calls = 0
        self._resource_id = resource_id

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        reader_payload: dict[str, object] = {}
        for message in reversed(messages):
            if message.role != "user":
                continue
            try:
                payload = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and "untrusted_document_nodes" in payload:
                reader_payload = payload
                break
        nodes = reader_payload.get("untrusted_document_nodes")
        if isinstance(nodes, list):
            source = next(
                (
                    node
                    for node in nodes
                    if isinstance(node, dict)
                    and "事件是系统唯一的事实来源" in str(node.get("content", ""))
                ),
                None,
            )
            if isinstance(source, dict):
                content = str(source["content"])
                quote = "事件是系统唯一的事实来源"
                start = content.index(quote)
                return Completion(
                    text=json.dumps(
                        {
                            "topic": "上传材料：事件脊柱",
                            "candidates": [
                                {
                                    "concept": "事件事实源",
                                    "summary": "事件不可变追加，并作为系统唯一事实来源。",
                                    "confidence": 0.97,
                                    "evidence": [
                                        {
                                            "node_key": source["node_key"],
                                            "start_offset": start,
                                            "end_offset": start + len(quote),
                                            "quote": quote,
                                        }
                                    ],
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    usage=Usage(prompt_tokens=120, completion_tokens=30),
                )
        if role == "enrich":
            self._question_calls += 1
            if any("expected_points" in message.content for message in messages):
                open_questions = [
                    "durable processor 失败后为什么要阻断当前 turn？",
                    "为什么继续当前 turn 会破坏 durable processor 的恢复边界？",
                    "durable processor 应如何处理建立在不完整状态上的后续副作用？",
                    "当前 turn 持久化失败时，durable processor 为什么不能继续执行？",
                ]
                return Completion(
                    text=json.dumps(
                        {
                            "question": (
                                open_questions[(self._question_calls - 1) % len(open_questions)]
                                + f"（fixture {self._question_calls}）"
                            ),
                            "expected_points": [
                                {
                                    "point_id": "incomplete_state",
                                    "description": "说明继续执行会让后续副作用依赖不完整状态",
                                    "cited_evidence": (
                                        "失败后继续当前 turn 会让后续副作用依赖不完整状态"
                                    ),
                                }
                            ],
                            "critical_point_ids": ["incomplete_state"],
                            "reference_answer": (
                                "继续执行会让后续副作用建立在不完整状态上，所以必须阻断当前 turn。"
                            ),
                            "cited_evidence": ["失败后继续当前 turn 会让后续副作用依赖不完整状态"],
                        },
                        ensure_ascii=False,
                    ),
                    usage=Usage(prompt_tokens=180, completion_tokens=45),
                )
            questions = [
                "durable processor 失败后为什么要阻断当前 turn？",
                "继续执行会怎样破坏事件历史的因果一致性？",
                "恢复流程为什么必须从完整事件记录开始？",
                "持久化失败后，哪条执行边界不能跨越？",
            ]
            return Completion(
                text=json.dumps(
                    {
                        "question": (
                            questions[(self._question_calls - 1) % len(questions)]
                            + f"（fixture {self._question_calls}）"
                        ),
                        "options": [
                            "避免后续副作用依赖不完整状态",
                            "允许后续副作用跳过事件持久化",
                            "让恢复流程忽略最近成功快照",
                            "把当前材料切换为另一个资源",
                            "清空事件序列并从零继续执行",
                            "在内存中标记成功而不追加事件",
                        ],
                        "answer_index": 0,
                        "cited_evidence": ["失败后继续当前 turn 会让后续副作用依赖不完整状态"],
                    },
                    ensure_ascii=False,
                ),
                usage=Usage(prompt_tokens=180, completion_tokens=45),
            )
        if role == "basic" and any("待评干扰项：" in message.content for message in messages):
            return Completion(
                text=json.dumps(
                    {
                        "label": "合理干扰",
                        "rationale": "与题干同域，但不符合原文证据。",
                    },
                    ensure_ascii=False,
                ),
                usage=Usage(prompt_tokens=90, completion_tokens=20),
            )
        if role == "basic" and any("本题原子评分点" in message.content for message in messages):
            grading_input = messages[-1].content
            answer_units = re.findall(
                r"^- \[(v1e\d+_\d+)\] (.+)$",
                grading_input,
                flags=re.MULTILINE,
            )
            accepted = any("依赖不完整状态" in text for _, text in answer_units)
            return Completion(
                text=json.dumps(
                    {
                        "verdict": "对" if accepted else "错",
                        "point_assessments": [
                            {
                                "point_id": "incomplete_state",
                                "label": "matched" if accepted else "missing",
                                "answer_evidence_ids": ([answer_units[-1][0]] if accepted else []),
                                "reason": (
                                    "明确说明了不完整状态依赖。"
                                    if accepted
                                    else "没有说明不完整状态依赖。"
                                ),
                            }
                        ],
                        "diagnosis": "complete" if accepted else "wrong_focus",
                        "reason": (
                            "结合补充说明，已覆盖阻断原因。"
                            if accepted
                            else "原回答没有解释阻断原因。"
                        ),
                        "cited_evidence": ["失败后继续当前 turn 会让后续副作用依赖不完整状态"],
                    },
                    ensure_ascii=False,
                ),
                usage=Usage(prompt_tokens=130, completion_tokens=35),
            )
        if tools is not None:
            user_text = next(
                (message.content for message in reversed(messages) if message.role == "user"),
                "",
            )
            if "保持生成" in user_text:
                await asyncio.Event().wait()
            has_tool_result = any(message.role == "tool" for message in messages)
            if "考" in user_text and not has_tool_result:
                return Completion(
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="fixture-start-assessment",
                            name="start_assessment",
                            arguments={
                                "resource_id": self._resource_id,
                                "rounds": 1,
                                "question_type": ("简答题" if "简答题" in user_text else "选择题"),
                            },
                        )
                    ],
                    usage=Usage(prompt_tokens=120, completion_tokens=20),
                )
            system_context = "\n".join(
                message.content for message in messages if message.role == "system"
            )
            active_scope = next(
                (
                    line
                    for line in system_context.splitlines()
                    if line.startswith("active_resource_id=")
                ),
                "active_resource_id=missing",
            )
            return Completion(
                text=f"（fixture）{active_scope}；收到：{user_text[:60]}",
                usage=Usage(prompt_tokens=120, completion_tokens=30),
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

    async def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        completion = await self.complete(
            messages,
            role=role,
            tools=tools,
        )
        for offset in range(0, len(completion.text), 8):
            yield TextDelta(text=completion.text[offset : offset + 8])
            # 给浏览器验收一个确定性窗口：必须先观察到 partial delta，再收到 authoritative final。
            await asyncio.sleep(0.15 if offset == 0 else 0.01)
        yield CompletionFinished(completion=completion)


class _FixtureSpeechProvider:
    provider_identity = "fixture-speech"

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        assert request.mime_type == "audio/webm;codecs=opus"
        return TranscriptionResult(
            transcript="继续执行会让后续副作用依赖不完整状态，所以必须阻断当前 turn。",
            provider_request_id="fixture-speech-request",
            provider_audio_duration_ms=1_000,
            latency_ms=25,
        )


def _seed(db_path: Path) -> str:
    resource = LearningResource.create(url="file://fixture/agent-runtime.md").model_copy(
        update={
            "raw_content": CONTENT,
            "content_hash": hashlib.sha256(CONTENT.encode()).hexdigest(),
            "status": "read",
            "topic": "Agent Runtime：事件总线与可恢复执行",
            "trusted": True,
        }
    )
    items = [
        KnowledgeItem.create(
            resource_id=resource.resource_id,
            concept=f"durable processor 失败处理 {index}",
            summary="失败后必须阻断当前 turn，避免因果链继续建立在不完整状态上。",
            evidence=[Evidence(quote="失败后继续当前 turn 会让后续副作用依赖不完整状态")],
            confidence=0.96,
        )
        for index in range(1, 9)
    ]
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    items = ground_items(snapshot, items)
    with LearningPersistence(db_path) as persistence:
        persistence.store.replace_snapshot(resource, items)
    return resource.resource_id


def main() -> None:
    artifact_root = Path(__file__).parents[1] / "web" / "test-results"
    artifact_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="fixture-runtime-", dir=artifact_root))
    (artifact_root / "runtime-location.txt").write_text(str(root), encoding="utf-8")
    learning_db = root / "learning.db"
    resource_id = _seed(learning_db)
    app = create_app(
        settings=ApiSettings(
            learning_db_path=learning_db,
            trace_db_path=root / "trace.db",
        ),
        provider=_FixtureProvider(resource_id),
        search_provider=_FixtureSearchProvider(),
        acquisition_http_source=_FixtureHttpSource(),
        speech_provider=_FixtureSpeechProvider(),
    )
    port = int(os.environ.get("GRANDQUIZ_FIXTURE_PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()

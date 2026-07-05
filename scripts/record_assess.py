"""录制真实 LLM 的单题考核（出题 + 判卷）到 cassette——手动运行，看质量 + 产回放 fixture。

    uv run --env-file .env python scripts/record_assess.py [你的作答]

hand-stock 一个小知识库，用真实 provider 跑 assess_once（出题=qwen/enrich、判卷=deepseek/basic），
打印生成的题 + 你的作答 + 判决，落 cassette 到 tests/fixtures/。给了作答参数就用它，否则用内置样例。
换不同作答（对 / 半对 / 错）多跑几次，看判卷是否给出合理的 对/勉强/错——这是判卷 prompt 的调优判据。
"""

import asyncio
import sys
from pathlib import Path

from grandquiz.domain.learning.assessment import assess_once
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
    LearningTask,
)
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_FIXTURE = Path("tests/fixtures/assess.cassette.json")
_URL = "https://example.com/sample"
_SEED = 42
_DEFAULT_ANSWER = "闭包就是函数记住了外层变量，函数返回后还能读写它。"

# hand-stock 的代表性知识点（真实调优时可换成 record_ingest 抽出的真 item）。
_ITEMS = [
    ("闭包", "能访问外层函数作用域变量的函数", "闭包捕获的是变量本身而非当时的值快照"),
    ("pass@k", "k 次尝试中至少成功一次", "pass@k means success in at least one of k attempts"),
]


async def main() -> None:
    answer = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_ANSWER
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(provider, cassette, provider.model_for_role)

    store = LearningStore()
    task = LearningTask.create("样例主题")
    resource = LearningResource.create(task_id=task.task_id, url=_URL)
    store.add_task(task)
    store.add_resource(resource)
    store.add_items(
        [
            KnowledgeItem.create(
                resource_id=resource.resource_id,
                index=index,
                concept=concept,
                summary=summary,
                evidence=[Evidence(quote=quote)],
                confidence=0.9,
            )
            for index, (concept, summary, quote) in enumerate(_ITEMS)
        ]
    )

    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="record-assess")

    try:
        result = await assess_once(
            task,
            store=store,
            provider=recording,
            responder=ScriptedResponder(answer=answer),
            emitter=emitter,
            rng=new_rng(_SEED),
        )
    finally:
        await provider.aclose()

    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    cassette.save(_FIXTURE)

    asked = next((e for e in events if e.type == LearningEvent.QUESTION_ASKED), None)
    judged = next((e for e in events if e.type == LearningEvent.ANSWER_JUDGED), None)
    print(f"cassette 已存：{_FIXTURE}（status={result.status}）\n")
    if asked is not None:
        print(f"● 被考知识点：{asked.payload['item_id']}")
        print(f"● 出题（enrich）：{asked.payload['question']}")
        print(f"  锚定证据：{asked.payload['cited_evidence']}")
    print(f"● 作答：{answer}")
    print(f"● 判决（basic）：{result.verdict}   weak_item_id={result.weak_item_id}")
    if judged is not None:
        print(f"  判卷引据：{judged.payload['cited_evidence']}")


if __name__ == "__main__":
    asyncio.run(main())

"""录制真实 LLM 响应到 cassette——手动运行，产出可回放的 eval fixture + 让你看抽取质量。

    uv run --env-file .env python scripts/record_ingest.py [content_file]

用真实 OpenAICompatProvider 跑 ingest 竖切：抓取源注入本机内容（无网络）、Reader 深读打真实
LLM（basic=deepseek）、审批全过。落 cassette 到 tests/fixtures/，之后 CI 可用 ReplayProvider
逐字节回放、零 token。给了 content_file 就读它作深读材料，否则用内置样例；抽出的 KnowledgeItem
逐条打印供你判断质量——这就是 prompt 调优环的输入（觉得不好就改 prompts/reader_extract.md 重录）。
"""

import asyncio
import sys
from pathlib import Path

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.ingest import ingest_resource
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_FIXTURE = Path("tests/fixtures/reader_extract.cassette.json")
_URL = "https://example.com/sample"
_ALLOWED = {"example.com"}
_SAMPLE = (
    "闭包（closure）是指有权访问另一个函数作用域中变量的函数。即使外部函数已经返回，"
    "内部函数依然能读写外部函数的局部变量，因为它捕获的是变量本身而非当时的值快照。"
    "常见用途包括数据私有化、函数工厂与回调中保存状态。"
)


def _keep_all(_item: KnowledgeItem) -> bool:
    return True


async def main() -> None:
    content = Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1 else _SAMPLE
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(provider, cassette, provider.model_for_role)
    store = LearningStore()
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="record")
    try:
        result = await ingest_resource(
            _URL,
            source=lambda _url: content,
            provider=recording,
            store=store,
            approval=ScriptedApprovalGate(keep=_keep_all),
            emitter=emitter,
            max_bytes=1_000_000,
            allowed_domains=_ALLOWED,
        )
    finally:
        await provider.aclose()

    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    cassette.save(_FIXTURE)
    resource = store.get_resource(result.resource_id)
    topic = resource.topic if resource is not None else None
    print(f"cassette 已存：{_FIXTURE}（status={result.status}，{len(result.items)} 个 item）")
    print(f"资源级 topic（RAG-metadata，落 resources.topic）：{topic!r}\n")
    for item in result.items:
        print(f"● {item.concept}（confidence={item.confidence}）")
        print(f"  摘要：{item.summary}")
        for evidence in item.evidence:
            print(f"  证据：{evidence.quote!r}")
        print()


if __name__ == "__main__":
    asyncio.run(main())

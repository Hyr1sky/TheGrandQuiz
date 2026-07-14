"""端到端真机演示——喂 URL（内容用本地材料）→ 真 Reader 深读入库 → 多轮考核 → 薄弱记账。

    uv run --env-file .env python scripts/demo_end_to_end.py [材料文件，默认 eval_paper.txt]

真实消耗 token。展示整条考核循环：抽知识点 → 题型路由 → 出题 → 判卷 → 三态记账 → 追问给正解。
作答用固定占位——首轮走选择题（MC），占位作答多半判错 → 概念入薄弱 → 复考路由到追问深挖，
借此演示题型随掌握度变化（fetch 用注入内容、无真实网络；深读 / 出题 / 判卷是真实 LLM）。
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.assessment.engine import assess_once
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.ingest import ingest_resource
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.llm import OpenAICompatProvider

_URL = "https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents"
_MATERIAL = Path("tests/materials/eval_paper.txt")
_ROUNDS = 2
_ANSWER = "评估就是拿一批任务反复跑、记录轨迹、检查真实结果并多维度打分。"


def _keep_all(_item: KnowledgeItem) -> bool:
    return True


def _payload(events: list[AgentEvent], etype: str) -> dict[str, Any]:
    event = next((e for e in events if e.type == etype), None)
    return dict(event.payload) if event is not None else {}


async def main() -> None:
    material = Path(sys.argv[1]) if len(sys.argv) > 1 else _MATERIAL
    content = material.read_text(encoding="utf-8")
    provider = OpenAICompatProvider.from_env()
    store = LearningStore()
    memory = LearningMemory()

    try:
        # 1) 喂 URL → 真 Reader 深读入库（内容注入本地材料，url 作标识）。
        emitter = EventEmitter(EventSink(), ManualClock(), trace_id="ingest")
        ingest = await ingest_resource(
            _URL,
            source=lambda _url: content,
            provider=provider,
            store=store,
            approval=ScriptedApprovalGate(keep=_keep_all),
            emitter=emitter,
            max_bytes=1_000_000,
            allowed_domains={"www.anthropic.com"},
        )
        print(f"\n=== 深读入库 ===  status={ingest.status}，{len(ingest.items)} 个知识点")
        for item in ingest.items[:10]:
            print(f"  · {item.concept}")
        if len(ingest.items) > 10:
            print(f"  …（共 {len(ingest.items)} 个）")

        # 2) 多轮考核——题型随被考概念在记忆里的状态路由。
        for rnd in range(1, _ROUNDS + 1):
            events: list[AgentEvent] = []
            sink = EventSink()
            sink.subscribe(events.append)
            round_emitter = EventEmitter(sink, ManualClock(), trace_id=f"assess-{rnd}")
            result = await assess_once(
                store=store,
                provider=provider,
                responder=ScriptedResponder(answer=_ANSWER),
                memory=memory,
                emitter=round_emitter,
                rng=new_rng(42),
            )
            asked = _payload(events, LearningEvent.QUESTION_ASKED)
            print(f"\n=== 第 {rnd} 轮 ===  题型={result.question_type}")
            print(f"  被考知识点：{asked.get('item_id')}")
            print(f"  出题：{asked.get('question')}")
            if asked.get("options"):
                print(f"  选项：{asked['options']}")
            print(f"  作答：{_ANSWER}")
            print(f"  判决：{result.verdict}   概念状态 → {result.concept_state}")
            followup = _payload(events, LearningEvent.FOLLOWUP_GIVEN)
            if followup:
                print(f"  追问给正解：{followup['correct_answer']}")
        print(f"\n=== 薄弱记忆 ===  {sorted(memory.weak_item_ids())}")
    finally:
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(main())

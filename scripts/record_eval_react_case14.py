"""录制 eval case14（大批量出题不能编造）的真机 ReAct cassette——手动运行，看行为 + 产回放 fixture。

    uv run --env-file .env python scripts/record_eval_react_case14.py

复用 ``evals.harness.solve`` 的 ``_solve_react`` 装配（避免录制脚本与 eval Solver 的接线各写一份、
messages 组装漂移导致回放对不上）：真实 provider 包一层 ``RecordingProvider``，跑一遍
``case14`` 的用户消息，落 cassette，并打印实际拿到的事件类型序列——写 case14 YAML 的
``expected_events`` 时直接照抄这里打印的序列。
"""

import asyncio
from pathlib import Path

from grandquiz.evals.harness import Case, solve
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_FIXTURE = Path("tests/fixtures/eval_case14_bulk_quiz.cassette.json")

# 必须与 evals/cases/case14_*.yaml 的 setup 逐字一致，否则 messages 对不上、回放落空。
_CASE = Case(
    id="case14",
    kind="react",
    expected_events=[],  # 录制阶段不需要；写 YAML 时用下方打印的真实序列
    answer="随便选一个",
    user_messages=["帮我出3道选择题"],
    cassette="eval_case14_bulk_quiz.cassette.json",
)


async def main() -> None:
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(provider, cassette, provider.model_for_role)
    try:
        result = await solve(_CASE, provider_override=recording)
    finally:
        await provider.aclose()

    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    cassette.save(_FIXTURE)

    print(f"cassette 已存：{_FIXTURE}\n")
    print("● 事件类型序列（写 YAML 的 expected_events 时照抄这份）：")
    for event in result.events:
        print(f"  - {event.type}")
    tool_calls = [e for e in result.events if e.type == "tool_call.started"]
    print(f"\n● tool_call.started 次数：{len(tool_calls)}")
    for tc in tool_calls:
        print(f"  - {tc.payload.get('tool_name')}({tc.payload.get('arguments')})")


if __name__ == "__main__":
    asyncio.run(main())

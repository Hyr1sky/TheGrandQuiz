"""真实录制 case15：自然材料问答 → 高层 grounded workflow → exact citation。

运行：uv run --env-file .env python scripts/record_eval_react_case15.py
"""

import asyncio

from grandquiz.evals.harness import Case, solve
from grandquiz.evals.resources import eval_fixture_path
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider

_FIXTURE = eval_fixture_path("eval_case15_natural_grounded_answer.cassette.json")
_CASE = Case(
    id="case15",
    kind="react",
    expected_events=[],
    user_messages=["根据库存里的 Agent Runtime 材料，事件总线为什么被称为信封？请给出原文出处。"],
    cassette=_FIXTURE.name,
    react_fixture="grounded",
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
    print(f"cassette 已存：{_FIXTURE}")
    for event in result.events:
        print(f"  - {event.type}")
    tool_calls = [event for event in result.events if event.type == "tool_call.started"]
    for event in tool_calls:
        print(f"  tool: {event.payload.get('tool_name')}({event.payload.get('arguments')})")


if __name__ == "__main__":
    asyncio.run(main())

"""真实录制 case15：自然材料问答 → 高层 grounded workflow → exact citation。

运行：uv run --env-file .env python scripts/record_eval_react_case15.py
"""

import asyncio

from grandquiz.evals.case import ReactCase
from grandquiz.evals.harness import load_cases, solve
from grandquiz.evals.resources import eval_fixture_target
from grandquiz.providers.llm import OpenAICompatProvider
from grandquiz.providers.replay import Cassette, RecordingProvider


async def main() -> None:
    case = next(case for case in load_cases() if case.id == "case15")
    if not isinstance(case, ReactCase):
        raise RuntimeError("case15 必须是 ReactCase")
    fixture = eval_fixture_target(case.cassette)
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(provider, cassette, provider.model_for_role)
    try:
        result = await solve(case, provider_override=recording)
    finally:
        await provider.aclose()

    fixture.parent.mkdir(parents=True, exist_ok=True)
    cassette.save(fixture)
    print(f"cassette 已存：{fixture}")
    for event in result.events:
        print(f"  - {event.type}")
    tool_calls = [event for event in result.events if event.type == "tool_call.started"]
    for event in tool_calls:
        print(f"  tool: {event.payload.get('tool_name')}({event.payload.get('arguments')})")


if __name__ == "__main__":
    asyncio.run(main())

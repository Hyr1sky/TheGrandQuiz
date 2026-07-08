"""DemoEchoProvider——确定性、无网络的 provider，供开发与测试用。"""

from collections.abc import Sequence

from grandquiz.providers.base import Completion, Message, Role, ToolSpec, Usage


class DemoEchoProvider:
    """回声最近一条 user 消息。给定输入即确定，无需 record/replay。``role`` / ``tools`` 接收但忽略
    （不做 function-calling）。"""

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        text = f"echo: {last_user}"
        prompt_tokens = sum(len(m.content.split()) for m in messages)
        usage = Usage(prompt_tokens=prompt_tokens, completion_tokens=len(text.split()))
        return Completion(text=text, usage=usage)

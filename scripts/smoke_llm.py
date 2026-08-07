"""真实 LLM 连通性冒烟测试——手动运行，验证两个角色的 .env 配置能打通端点。

用法（用 uv 的 --env-file 载入 .env，无需额外依赖）：

    uv run --env-file .env python scripts/smoke_llm.py

只打印模型是否连通 / 回复片段 / token 用量，**绝不打印密钥**。会真实消耗少量 token。
若某角色的方言或 thinking 参数被端点拒绝，这里会立刻暴露。
"""

import asyncio

from grandquiz.providers.base import Message, Role
from grandquiz.providers.llm import OpenAICompatProvider


async def main() -> None:
    provider = OpenAICompatProvider.from_env()
    roles: list[Role] = ["basic", "enrich"]
    try:
        for role in roles:
            messages = [Message(role="user", content="用一句话回答：请回复“连通正常”。")]
            try:
                reply = await provider.complete(messages, role=role)
            except Exception as exc:
                # 冒烟脚本：把任何失败原样报给人看（含端点拒绝 thinking 扩展字段）。
                print(f"[{role}] 失败：{exc!r}")
                continue
            snippet = reply.text.strip().replace("\n", " ")[:50]
            print(f"[{role}] ok | 回复={snippet!r} | tokens={reply.usage.total_tokens}")
    finally:
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(main())

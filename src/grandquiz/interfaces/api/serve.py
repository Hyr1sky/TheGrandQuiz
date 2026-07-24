"""`grandquiz-web` 的 loopback-only 本地启动入口。"""

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.providers.llm import OpenAICompatProvider

_HOST = "127.0.0.1"
_PORT = 8000


def create_default_app() -> FastAPI:
    """由 uvicorn factory 调用；读取 `.env`，但 DB 仍延迟到 lifespan 打开。"""
    load_dotenv()
    provider = OpenAICompatProvider.from_env()
    return create_app(
        settings=ApiSettings.default(),
        provider=provider,
        provider_close=provider.aclose,
    )


def main() -> None:
    uvicorn.run(
        "grandquiz.interfaces.api.serve:create_default_app",
        factory=True,
        host=_HOST,
        port=_PORT,
    )

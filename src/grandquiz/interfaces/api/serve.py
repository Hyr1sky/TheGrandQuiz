"""`grandquiz-web` 的 loopback-only 本地启动入口。"""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.interfaces.search_config import search_provider_from_env
from grandquiz.providers.dashscope_speech import DashScopeSpeechRecognitionAdapter
from grandquiz.providers.llm import OpenAICompatProvider

_HOST = "127.0.0.1"
_PORT = 8000
_STATIC_DIR = Path(__file__).parent / "static"


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


class _SpaStaticFiles(StaticFiles):
    """静态资源 404 保持 404；前端无扩展名路由回退到 index.html。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        request_path = str(scope.get("path", ""))
        can_fallback = (
            scope.get("method") in {"GET", "HEAD"}
            and not request_path.startswith("/api/")
            and Path(path).suffix == ""
        )
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or not can_fallback:
                raise
            return await super().get_response("index.html", scope)


def mount_web_static(app: FastAPI, directory: Path = _STATIC_DIR) -> bool:
    """把 wheel 内生产前端挂到 API 最后一条路由；缺资产时供源码开发模式显式跳过。"""
    if not (directory / "index.html").is_file():
        return False
    app.mount("/", _SpaStaticFiles(directory=directory, html=True), name="web")
    return True


def create_default_app() -> FastAPI:
    """由 uvicorn factory 调用；读取 `.env`，但 DB 仍延迟到 lifespan 打开。"""
    load_dotenv()
    provider = OpenAICompatProvider.from_env()
    speech_provider = (
        DashScopeSpeechRecognitionAdapter.from_env()
        if os.environ.get("DASHSCOPE_API_KEY") and os.environ.get("DASHSCOPE_WORKSPACE_ID")
        else None
    )
    app = create_app(
        settings=ApiSettings.default(),
        provider=provider,
        provider_close=provider.aclose,
        search_provider=search_provider_from_env(),
        speech_provider=speech_provider,
        asr_hints_default=_env_flag("ASR_ENABLE_HINTS"),
    )
    mount_web_static(app)
    return app


def main() -> None:
    uvicorn.run(
        "grandquiz.interfaces.api.serve:create_default_app",
        factory=True,
        host=_HOST,
        port=_PORT,
    )

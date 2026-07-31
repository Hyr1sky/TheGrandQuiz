"""FastAPI application factory for the local-first Web interface."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api.acquisition_routes import router as acquisitions_router
from grandquiz.interfaces.api.acquisitions import AcquisitionManager
from grandquiz.interfaces.api.assessment_routes import router as assessments_router
from grandquiz.interfaces.api.assessment_runs import AssessmentManager
from grandquiz.interfaces.api.chat import ChatManager
from grandquiz.interfaces.api.chat_routes import router as chat_router
from grandquiz.interfaces.api.errors import install_error_handlers
from grandquiz.interfaces.api.learning_routes import router as learning_router
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.interfaces.api.observability_routes import router as observability_router
from grandquiz.interfaces.api.resources import router as resources_router
from grandquiz.interfaces.api.run_routes import router as runs_router
from grandquiz.interfaces.api.runs import RunManager
from grandquiz.interfaces.learning_outbox import publish_pending_learning_facts
from grandquiz.kernel.clock import Clock, SystemClock
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Provider


@dataclass(frozen=True)
class ApiSettings:
    """API 进程所拥有的本地持久化路径。"""

    learning_db_path: Path
    trace_db_path: Path

    @classmethod
    def default(cls) -> "ApiSettings":
        data_dir = Path.home() / ".grandquiz"
        return cls(
            learning_db_path=data_dir / "learning.db",
            trace_db_path=data_dir / "trace.db",
        )


class HealthResponse(BaseModel):
    status: str
    api_version: str


async def health() -> HealthResponse:
    return HealthResponse(status="ok", api_version="v1")


def create_app(
    *,
    settings: ApiSettings,
    provider: Provider,
    provider_close: Callable[[], Awaitable[None]] | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """创建可注入 provider/DB 的 app；模块导入本身不触碰 `.env` 或数据库。"""

    app_clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        settings.learning_db_path.parent.mkdir(parents=True, exist_ok=True)
        settings.trace_db_path.parent.mkdir(parents=True, exist_ok=True)
        persistence = LearningPersistence(settings.learning_db_path, clock=app_clock)
        trace_store = TraceStore(settings.trace_db_path)
        publish_pending_learning_facts(persistence.learning_facts, trace_store)
        trace_observatory = TraceObservatory(trace_store)
        run_manager = RunManager(
            store=persistence.store,
            provider=provider,
            trace_store=trace_store,
            trace_observatory=trace_observatory,
        )
        assessment_manager = AssessmentManager(
            store=persistence.store,
            provider=provider,
            memory=persistence.memory,
            asked_questions=persistence.asked_questions,
            preferences=persistence.preferences,
            difficulty=persistence.difficulty,
            learning_facts=persistence.learning_facts,
            trace_store=trace_store,
            trace_observatory=trace_observatory,
        )
        chat_manager = ChatManager(
            persistence=persistence,
            provider=provider,
            trace_store=trace_store,
            trace_observatory=trace_observatory,
        )
        acquisition_manager = AcquisitionManager(
            persistence=persistence,
            provider=provider,
            trace_store=trace_store,
            clock=app_clock,
        )
        app.state.persistence = persistence
        app.state.provider = provider
        app.state.settings = settings
        app.state.run_manager = run_manager
        app.state.assessment_manager = assessment_manager
        app.state.chat_manager = chat_manager
        app.state.acquisition_manager = acquisition_manager
        app.state.trace_observatory = trace_observatory
        app.state.trace_store = trace_store
        app.state.clock = app_clock
        try:
            yield
        finally:
            await acquisition_manager.aclose()
            await chat_manager.aclose()
            await assessment_manager.aclose()
            await run_manager.aclose()
            trace_store.close()
            persistence.close()
            if provider_close is not None:
                await provider_close()

    app = FastAPI(
        title="TheGrandQuiz Local API",
        version="0.1.0",
        lifespan=lifespan,
    )
    install_error_handlers(app)

    app.add_api_route(
        "/api/v1/health",
        health,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["system"],
    )
    app.include_router(resources_router)
    app.include_router(acquisitions_router)
    app.include_router(runs_router)
    app.include_router(assessments_router)
    app.include_router(learning_router)
    app.include_router(chat_router)
    app.include_router(observability_router)
    return app

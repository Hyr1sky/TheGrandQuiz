"""FastAPI application factory for the local-first Web interface."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from grandquiz import __version__
from grandquiz.domain.learning.ingest.fetch import FetchSource
from grandquiz.domain.learning.ingest.web_search import SearchProvider
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.domain.learning.preference import resolve_asr_material_hints
from grandquiz.interfaces.api.acquisition_routes import router as acquisitions_router
from grandquiz.interfaces.api.acquisitions import AcquisitionManager
from grandquiz.interfaces.api.assessment_routes import router as assessments_router
from grandquiz.interfaces.api.assessment_runs import AssessmentManager
from grandquiz.interfaces.api.chat import ChatManager
from grandquiz.interfaces.api.chat_routes import router as chat_router
from grandquiz.interfaces.api.diagnostics import DiagnosticBundleExporter
from grandquiz.interfaces.api.discoveries import DiscoveryManager
from grandquiz.interfaces.api.discovery_routes import router as discoveries_router
from grandquiz.interfaces.api.errors import install_error_handlers
from grandquiz.interfaces.api.eval_management import EvalManagementService
from grandquiz.interfaces.api.eval_routes import router as eval_router
from grandquiz.interfaces.api.learning_routes import router as learning_router
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.interfaces.api.observability_routes import router as observability_router
from grandquiz.interfaces.api.resources import router as resources_router
from grandquiz.interfaces.api.run_routes import router as runs_router
from grandquiz.interfaces.api.runs import RunManager
from grandquiz.interfaces.api.settings import DataLocationView, LocalSettings
from grandquiz.interfaces.api.settings_routes import router as settings_router
from grandquiz.interfaces.api.voice_routes import router as voice_router
from grandquiz.interfaces.api.voice_runs import VoiceRunManager
from grandquiz.interfaces.learning_outbox import publish_pending_learning_facts
from grandquiz.kernel.clock import Clock, SystemClock
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Provider
from grandquiz.providers.speech import SpeechRecognitionProvider


@dataclass(frozen=True)
class ApiSettings:
    """API 进程所拥有的本地持久化路径。"""

    learning_db_path: Path
    trace_db_path: Path
    voice_db_path: Path | None = None

    @classmethod
    def default(cls) -> "ApiSettings":
        data_dir = Path.home() / ".grandquiz"
        return cls(
            learning_db_path=data_dir / "learning.db",
            trace_db_path=data_dir / "trace.db",
            voice_db_path=data_dir / "voice.db",
        )

    def resolved_voice_db_path(self) -> Path:
        return self.voice_db_path or self.learning_db_path.with_name("voice.db")


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
    search_provider: SearchProvider | None = None,
    acquisition_http_source: FetchSource | None = None,
    speech_provider: SpeechRecognitionProvider | None = None,
    asr_hints_default: bool = False,
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
            persistence=persistence,
            provider=provider,
            trace_store=trace_store,
            clock=app_clock,
            trace_observatory=trace_observatory,
        )
        voice_run_manager = (
            None
            if speech_provider is None
            else VoiceRunManager(
                db_path=settings.resolved_voice_db_path(),
                speech_provider=speech_provider,
                hints=persistence.recognition_lexicons,
                assessments=assessment_manager,
                clock=app_clock,
                trace_store=trace_store,
                trace_observatory=trace_observatory,
                hints_enabled=resolve_asr_material_hints(
                    persistence.preferences,
                    default=asr_hints_default,
                ),
            )
        )
        local_settings = LocalSettings(
            persistence=persistence,
            provider=provider,
            speech_provider=speech_provider,
            voice_hint_policy=voice_run_manager,
            asr_hints_default=asr_hints_default,
            data_locations=[
                DataLocationView(
                    kind="learning",
                    path=str(settings.learning_db_path.expanduser().resolve()),
                ),
                DataLocationView(
                    kind="trace",
                    path=str(settings.trace_db_path.expanduser().resolve()),
                ),
                DataLocationView(
                    kind="voice",
                    path=str(settings.resolved_voice_db_path().expanduser().resolve()),
                ),
            ],
        )
        diagnostic_exporter = DiagnosticBundleExporter(
            observatory=trace_observatory,
            provider_views=local_settings.provider_views,
            clock=app_clock,
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
            http_source=acquisition_http_source,
        )
        discovery_manager = DiscoveryManager(
            persistence=persistence,
            acquisitions=acquisition_manager,
            search_provider=search_provider,
            trace_store=trace_store,
            clock=app_clock,
        )
        eval_management = EvalManagementService(
            persistence=persistence,
            trace_store=trace_store,
            clock=app_clock,
        )
        app.state.persistence = persistence
        app.state.provider = provider
        app.state.settings = settings
        app.state.run_manager = run_manager
        app.state.assessment_manager = assessment_manager
        app.state.voice_run_manager = voice_run_manager
        app.state.local_settings = local_settings
        app.state.diagnostic_exporter = diagnostic_exporter
        app.state.chat_manager = chat_manager
        app.state.acquisition_manager = acquisition_manager
        app.state.discovery_manager = discovery_manager
        app.state.eval_management = eval_management
        app.state.trace_observatory = trace_observatory
        app.state.trace_store = trace_store
        app.state.clock = app_clock
        try:
            yield
        finally:
            if voice_run_manager is not None:
                await voice_run_manager.aclose()
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
        version=__version__,
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
    app.include_router(discoveries_router)
    app.include_router(runs_router)
    app.include_router(assessments_router)
    app.include_router(voice_router)
    app.include_router(settings_router)
    app.include_router(learning_router)
    app.include_router(eval_router)
    app.include_router(chat_router)
    app.include_router(observability_router)
    return app

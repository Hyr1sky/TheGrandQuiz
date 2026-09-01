"""Versioned diagnostic bundle assembled exclusively from safe projections."""

from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel

from grandquiz import __version__
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.interfaces.api.settings import ProviderSettingView
from grandquiz.interfaces.trace_projection import SafeTraceEventV1, SafeTraceSummaryV1
from grandquiz.kernel.clock import Clock


class DiagnosticProviderIdentityV1(BaseModel):
    role: Literal["basic", "enrich", "speech"]
    configured: bool
    model: str | None
    endpoint_host: str | None


class DiagnosticConfigIdentityV1(BaseModel):
    application_version: str
    settings_schema_version: Literal["settings.v1"] = "settings.v1"
    providers: list[DiagnosticProviderIdentityV1]


class DiagnosticManifestV1(BaseModel):
    created_at: float


class DiagnosticBundleV1(BaseModel):
    schema_version: Literal["diagnostic_bundle.v1"] = "diagnostic_bundle.v1"
    trace_id: str
    config_identity: DiagnosticConfigIdentityV1
    summary: SafeTraceSummaryV1
    events: list[SafeTraceEventV1]
    manifest: DiagnosticManifestV1


class DiagnosticBundleExporter:
    """Own the diagnostic allowlist; routes never inspect raw trace events."""

    def __init__(
        self,
        *,
        observatory: TraceObservatory,
        provider_views: Callable[[], Sequence[ProviderSettingView]],
        clock: Clock,
        application_version: str = __version__,
    ) -> None:
        self._observatory = observatory
        self._provider_views = provider_views
        self._clock = clock
        self._application_version = application_version

    def export(self, trace_id: str) -> DiagnosticBundleV1:
        snapshot = self._observatory.snapshot(trace_id)
        return DiagnosticBundleV1(
            trace_id=trace_id,
            config_identity=DiagnosticConfigIdentityV1(
                application_version=self._application_version,
                providers=[
                    DiagnosticProviderIdentityV1(
                        role=provider.role,
                        configured=provider.configured,
                        model=provider.model,
                        endpoint_host=provider.endpoint_host,
                    )
                    for provider in self._provider_views()
                ],
            ),
            summary=snapshot.summary,
            events=snapshot.events,
            manifest=DiagnosticManifestV1(created_at=self._clock.now()),
        )

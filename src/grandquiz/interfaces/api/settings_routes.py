"""HTTP projection for local settings; secrets remain environment-only."""

from typing import cast

from fastapi import APIRouter, Request

from grandquiz.interfaces.api.settings import LocalSettings, SettingsPatch, SettingsView

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def settings_from(request: Request) -> LocalSettings:
    return cast("LocalSettings", request.app.state.local_settings)


@router.get("", response_model=SettingsView)
async def get_settings(request: Request) -> SettingsView:
    return settings_from(request).view()


@router.patch("", response_model=SettingsView)
async def update_settings(command: SettingsPatch, request: Request) -> SettingsView:
    return settings_from(request).update(command)

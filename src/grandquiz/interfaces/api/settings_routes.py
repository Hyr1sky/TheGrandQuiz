"""HTTP projection for local settings; secrets remain environment-only."""

from ipaddress import ip_address
from typing import cast

from fastapi import APIRouter, Request

from grandquiz.interfaces.api.settings import LocalSettings, SettingsPatch, SettingsView

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def settings_from(request: Request) -> LocalSettings:
    return cast("LocalSettings", request.app.state.local_settings)


def _is_loopback(request: Request) -> bool:
    peer = request.client
    if peer is None:
        return False
    try:
        return ip_address(peer.host).is_loopback
    except ValueError:
        return False


@router.get("", response_model=SettingsView)
async def get_settings(request: Request) -> SettingsView:
    return settings_from(request).view(include_data_locations=_is_loopback(request))


@router.patch("", response_model=SettingsView)
async def update_settings(command: SettingsPatch, request: Request) -> SettingsView:
    return settings_from(request).update(
        command,
        include_data_locations=_is_loopback(request),
    )

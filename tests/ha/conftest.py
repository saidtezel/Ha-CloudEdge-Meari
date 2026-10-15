"""Fixtures for tests that run against a real Home Assistant core.

These need ``pytest-homeassistant-custom-component`` and run separately from
the stub-based unittest suite:  ``pytest tests/ha``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest_plugins = "pytest_homeassistant_custom_component"

FRONT = {"deviceID": 101, "snNum": "SN-FRONT", "deviceName": "Front", "_category": "snap"}
BACK = {"deviceID": 102, "snNum": "SN-BACK", "deviceName": "Back", "_category": "ipc"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/cloudplus."""
    yield


@pytest.fixture
def cloud_devices() -> list[dict]:
    """Cameras the fake Meari cloud reports for the account."""
    return [dict(FRONT), dict(BACK)]


async def _fake_start(self) -> None:
    """Coordinator start without the cloud threads: just become available."""
    self._running = False
    self._available = True


@pytest.fixture(autouse=True)
def fake_cloud(cloud_devices):
    """No Meari traffic and no coordinator threads; everything else is real."""
    api = Mock()
    api.get_camera_devices.side_effect = lambda: [dict(d) for d in cloud_devices]
    coordinator = "custom_components.cloudplus.coordinator.CloudEdgeMeariCoordinator"
    with (
        patch("custom_components.cloudplus.build_api_client", return_value=api),
        patch("custom_components.cloudplus.config_flow.build_api_client", return_value=api),
        patch(f"{coordinator}.async_start", _fake_start),
        patch(f"{coordinator}.async_stop", AsyncMock()),
        patch(f"{coordinator}.prefetch_battery"),
        patch(f"{coordinator}.prefetch_lamp"),
    ):
        yield api

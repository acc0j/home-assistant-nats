"""Live tests against a real NATS broker (see docker-compose.yml).

Skipped unless NATS_LIVE=1. Run with: docker compose up -d && NATS_LIVE=1 pytest tests/
"""

import asyncio
import json
import os

import nats
import pytest

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.nats_io.const import DOMAIN

pytestmark = [
    pytest.mark.skipif(not os.environ.get("NATS_LIVE"), reason="NATS_LIVE not set"),
    pytest.mark.usefixtures("socket_enabled"),
]

# 127.0.0.1, not localhost: pytest-socket only allows that host
HOST = "127.0.0.1"
PORT = 4222
USER = PASSWORD = "ha"


@pytest.fixture
async def nc():
    """Return a NATS client acting as the external system."""
    client = await nats.connect(f"nats://{USER}:{PASSWORD}@{HOST}:{PORT}")
    yield client
    await client.drain()


@pytest.fixture
async def entry(hass: HomeAssistant):
    """Set up the integration against the live broker."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_USERNAME: USER,
            CONF_PASSWORD: PASSWORD,
            CONF_SSL: False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    yield entry
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_state_published(hass: HomeAssistant, entry, nc) -> None:
    """State changes arrive on ha.state.<area>.<entity_id>."""
    sub = await nc.subscribe("ha.state.>")
    await nc.flush()

    hass.states.async_set("light.living_room", "on")
    msg = await sub.next_msg(timeout=2)

    assert msg.subject == "ha.state.no_area.light.living_room"
    assert json.loads(msg.data)["state"] == "on"


async def test_state_request(hass: HomeAssistant, entry, nc) -> None:
    """ha.request.state replies with the current state."""
    hass.states.async_set("light.living_room", "off")

    msg = await nc.request(
        "ha.request.state", json.dumps({"entity_id": "light.living_room"}).encode(), timeout=2
    )

    assert json.loads(msg.data)["state"] == "off"


async def test_service_command(hass: HomeAssistant, entry, nc) -> None:
    """ha.service messages call the HA service."""
    calls = async_mock_service(hass, "light", "turn_on")

    await nc.publish(
        "ha.service",
        json.dumps({"action": "light.turn_on", "target": {"entity_id": "light.living_room"}}).encode(),
    )
    await nc.flush()
    async with asyncio.timeout(2):
        while not calls:
            await asyncio.sleep(0.05)

    assert calls[0].data["entity_id"] == "light.living_room"

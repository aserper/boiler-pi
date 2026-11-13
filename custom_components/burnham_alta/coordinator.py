"""Polling coordinator for the Burnham ALTA integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN, scan_interval_seconds
from .decoder import Snapshot
from .modbus import (
    AltaConnectionError,
    AltaError,
    AltaModbusClient,
    AltaResponseError,
)

__all__ = ["BurnhamAltaCoordinator"]

_LOGGER = logging.getLogger(__name__)


class BurnhamAltaCoordinator(DataUpdateCoordinator[Snapshot]):
    """Fetch one HR 13..20 snapshot per polling interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AltaModbusClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(
                seconds=scan_interval_seconds(entry.options)
            ),
        )
        # Set explicitly for compatibility with Home Assistant releases whose
        # DataUpdateCoordinator does not take a config_entry keyword.
        self.config_entry = entry
        self.client = client

    async def _async_update_data(self) -> Snapshot:
        """Read the boiler once and translate transport errors for HA."""
        try:
            return await self.client.async_read_snapshot()
        except AltaConnectionError as err:
            raise UpdateFailed(f"Cannot reach the Burnham ALTA: {err}") from err
        except AltaResponseError as err:
            raise UpdateFailed(
                f"Invalid response from the Burnham ALTA: {err}"
            ) from err
        except AltaError as err:
            raise UpdateFailed(f"Burnham ALTA read failed: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error reading the Burnham ALTA")
            raise UpdateFailed(f"Unexpected error reading the boiler: {err}") from err

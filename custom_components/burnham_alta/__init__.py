"""Read-only Home Assistant integration for a Burnham ALTA boiler.

The integration performs one Modbus function-03 read of holding registers
13..20 per polling cycle and exposes the modelled values as sensors.  It does
not create writable entities or services, and it never calls a Modbus write
function.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BurnhamAltaCoordinator
from .modbus import AltaModbusClient

__all__ = ["async_setup_entry", "async_unload_entry"]

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Burnham ALTA from a config entry."""
    client = AltaModbusClient(entry.data)
    coordinator = BurnhamAltaCoordinator(hass, entry, client)

    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        # Also close on cancellation, before awaiting any further cleanup.
        client.close()
        await coordinator.async_shutdown()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        domain_data = hass.data.get(DOMAIN)
        if domain_data is not None:
            domain_data.pop(entry.entry_id, None)
            if not domain_data:
                hass.data.pop(DOMAIN, None)
        client.close()
        await coordinator.async_shutdown()
        raise

    # Returning True is the legacy contract; current Home Assistant tolerates
    # it for custom integrations, and older releases require it.
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and close the Modbus transport."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    coordinator: BurnhamAltaCoordinator | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if coordinator is not None:
        await coordinator.async_shutdown()
        coordinator.client.close()

    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)

    return True

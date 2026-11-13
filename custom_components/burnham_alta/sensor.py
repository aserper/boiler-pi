"""Read-only sensors for the Burnham ALTA integration.

Only the six points supported by the field capture and the manual's table 4-4
are created.  The target dashboard also mentions demand source, flame signal,
fan RPM, hold/lockout, pump/demand states, runtime/cycles and history.  None of
those has a validated register mapping, so no entity is created for them and
none is given a placeholder value.

The entity keys below are deliberately stable and dashboard-friendly.  Future
entities should reuse the same naming convention and be added only with a
confirmed register and unit.  Reserved keys (not implemented):

    demand_source, flame_signal, fan_speed, hold_state, lockout_state,
    pump_state, demand_state, runtime_hours, cycle_count

``supply_return_delta`` is also reserved: it can be derived from two sensors,
but the supply/return labels have not been independently confirmed against the
boiler display, so the integration does not publish a delta yet.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import BurnhamAltaCoordinator
from .decoder import Reading, Register

__all__ = [
    "BurnhamAltaSensor",
    "SENSOR_SPECS",
    "async_setup_entry",
]

_TEMPERATURE_DESCRIPTION: dict[str, Any] = {
    "device_class": SensorDeviceClass.TEMPERATURE,
    "state_class": SensorStateClass.MEASUREMENT,
    "native_unit_of_measurement": UnitOfTemperature.FAHRENHEIT,
    "suggested_display_precision": 0,
}

# Only confidently supported readings are listed.  Burner control state is a
# raw integer; its enum labels are not validated and must not be translated.
SENSOR_SPECS: tuple[tuple[Register, SensorEntityDescription], ...] = (
    (
        Register.BURNER_CONTROL_STATE,
        SensorEntityDescription(
            key="burner_control_state",
            translation_key="burner_control_state",
            icon="mdi:fire",
        ),
    ),
    (
        Register.ACTIVE_SETPOINT,
        SensorEntityDescription(
            key="target_temperature",
            translation_key="target_temperature",
            **_TEMPERATURE_DESCRIPTION,
        ),
    ),
    (
        Register.SUPPLY_TEMPERATURE,
        SensorEntityDescription(
            key="supply_temperature",
            translation_key="supply_temperature",
            **_TEMPERATURE_DESCRIPTION,
        ),
    ),
    (
        Register.RETURN_TEMPERATURE,
        SensorEntityDescription(
            key="return_temperature",
            translation_key="return_temperature",
            **_TEMPERATURE_DESCRIPTION,
        ),
    ),
    (
        Register.DHW_TEMPERATURE,
        SensorEntityDescription(
            key="dhw_temperature",
            translation_key="dhw_temperature",
            **_TEMPERATURE_DESCRIPTION,
        ),
    ),
    (
        Register.STACK_TEMPERATURE,
        SensorEntityDescription(
            key="stack_temperature",
            translation_key="stack_temperature",
            **_TEMPERATURE_DESCRIPTION,
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one sensor per supported register."""
    coordinator: BurnhamAltaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BurnhamAltaSensor(coordinator, register, description)
            for register, description in SENSOR_SPECS
        ]
    )


class BurnhamAltaSensor(
    CoordinatorEntity[BurnhamAltaCoordinator],
    SensorEntity,
):
    """A read-only value decoded from one holding register."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BurnhamAltaCoordinator,
        register: Register,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._register = register
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name=coordinator.config_entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def _reading(self) -> Reading | None:
        """Return the current reading, if this poll produced one."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.readings.get(self._register)

    @property
    def native_value(self) -> int | None:
        """Return the decoded value, or ``None`` when unavailable."""
        reading = self._reading
        return None if reading is None else reading.value

    @property
    def available(self) -> bool:
        """Unavailable on transport failure or a 0x7FFF sensor sentinel."""
        return (
            super().available
            and self._reading is not None
            and self._reading.available
        )

    @property
    def extra_state_attributes(self) -> dict[str, int | str] | None:
        """Expose the wire address and raw value for diagnosis."""
        reading = self._reading
        if reading is None:
            return None
        return {
            "register": reading.address,
            "raw_value": f"0x{reading.raw:04X}",
        }

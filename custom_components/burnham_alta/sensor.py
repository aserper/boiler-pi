"""Read-only sensors for the Burnham ALTA integration.

Only values documented in the manufacturer's table 4-4 are created.  Raw codes
are deliberately not translated: the manual and the manufacturer's ProtoAir
point list disagree about some enum numbering, and no label has been validated
against the boiler display.  A dashboard can therefore show the number but
must not pretend to know its name.

``0x7FFF`` is exposed as unavailable only for temperatures, as in the decoder.
``supply_return_delta`` is not published: the supply/return labels have not
been independently confirmed against the boiler display, so a derived delta
would imply more certainty than exists.
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
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CH1_NAME,
    CONF_CH2_NAME,
    CONF_DHW_NAME,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    consumer_name,
)
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

# The three heat-demand registers describe loads whose identity depends on the
# installation, so each one carries an operator-supplied consumer label.  The
# label changes only the friendly name and a diagnostic attribute; the unique
# ID stays derived from the register key so entity IDs are stable across an
# options reload.
_CONSUMER_NAME_OPTIONS: dict[Register, str] = {
    Register.DHW_HEAT_DEMAND: CONF_DHW_NAME,
    Register.CH1_HEAT_DEMAND: CONF_CH1_NAME,
    Register.CH2_HEAT_DEMAND: CONF_CH2_NAME,
}

# Only documented table 4-4 readings are listed.  Raw codes are plain integers:
# their enum labels are not validated and must not be translated.  The fan
# unit is a literal rpm string because Home Assistant treats it as a custom
# unit; the manual describes the value as blower speed in rpm.
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
    (
        Register.DHW_HEAT_DEMAND,
        SensorEntityDescription(
            key="dhw_heat_demand",
            translation_key="dhw_heat_demand",
            icon="mdi:water-boiler",
        ),
    ),
    (
        Register.CH1_HEAT_DEMAND,
        SensorEntityDescription(
            key="ch1_heat_demand",
            translation_key="ch1_heat_demand",
            icon="mdi:radiator",
        ),
    ),
    (
        Register.CH2_HEAT_DEMAND,
        SensorEntityDescription(
            key="ch2_heat_demand",
            translation_key="ch2_heat_demand",
            icon="mdi:radiator",
        ),
    ),
    (
        Register.HOLD_CODE,
        SensorEntityDescription(
            key="hold_code",
            translation_key="hold_code",
            icon="mdi:pause-circle-outline",
        ),
    ),
    (
        Register.LOCKOUT_CODE,
        SensorEntityDescription(
            key="lockout_code",
            translation_key="lockout_code",
            icon="mdi:alert-octagon-outline",
        ),
    ),
    (
        Register.DEMAND_SOURCE,
        SensorEntityDescription(
            key="demand_source",
            translation_key="demand_source",
            icon="mdi:source-branch",
        ),
    ),
    (
        Register.FLAME_SIGNAL,
        SensorEntityDescription(
            key="flame_signal",
            translation_key="flame_signal",
            icon="mdi:fire",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=PERCENTAGE,
            suggested_display_precision=0,
        ),
    ),
    (
        Register.FAN_SPEED_DEMANDED,
        SensorEntityDescription(
            key="fan_speed_demanded",
            translation_key="fan_speed_demanded",
            icon="mdi:fan",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="rpm",
            suggested_display_precision=0,
        ),
    ),
    (
        Register.FAN_SPEED_FEEDBACK,
        SensorEntityDescription(
            key="fan_speed_feedback",
            translation_key="fan_speed_feedback",
            icon="mdi:fan",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="rpm",
            suggested_display_precision=0,
        ),
    ),
    (
        Register.PUMP_A_STATUS,
        SensorEntityDescription(
            key="pump_a_status",
            translation_key="pump_a_status",
            icon="mdi:pump",
        ),
    ),
    (
        Register.PUMP_B_STATUS,
        SensorEntityDescription(
            key="pump_b_status",
            translation_key="pump_b_status",
            icon="mdi:pump",
        ),
    ),
    (
        Register.PUMP_C_STATUS,
        SensorEntityDescription(
            key="pump_c_status",
            translation_key="pump_c_status",
            icon="mdi:pump",
        ),
    ),
    (
        Register.BURNER_RUN_TIME_HOURS,
        SensorEntityDescription(
            key="burner_run_time_hours",
            translation_key="burner_run_time_hours",
            icon="mdi:timer-outline",
            device_class=SensorDeviceClass.DURATION,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=UnitOfTime.HOURS,
            suggested_display_precision=0,
        ),
    ),
    (
        Register.BURNER_CYCLE_COUNT,
        SensorEntityDescription(
            key="burner_cycle_count",
            translation_key="burner_cycle_count",
            icon="mdi:counter",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
    ),
    (
        Register.PUMP_A_CYCLES,
        SensorEntityDescription(
            key="pump_a_cycles",
            translation_key="pump_a_cycles",
            icon="mdi:counter",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
    ),
    (
        Register.PUMP_B_CYCLES,
        SensorEntityDescription(
            key="pump_b_cycles",
            translation_key="pump_b_cycles",
            icon="mdi:counter",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
    ),
    (
        Register.PUMP_C_CYCLES,
        SensorEntityDescription(
            key="pump_c_cycles",
            translation_key="pump_c_cycles",
            icon="mdi:counter",
            state_class=SensorStateClass.TOTAL_INCREASING,
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
        self._consumer_name: str | None = None
        name_key = _CONSUMER_NAME_OPTIONS.get(register)
        if name_key is not None:
            self._consumer_name = consumer_name(
                coordinator.config_entry.options, name_key
            )
            self._attr_name = self._consumer_name
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
        """Unavailable on transport failure, a failed block, or 0x7FFF."""
        return (
            super().available
            and self._reading is not None
            and self._reading.available
        )

    @property
    def extra_state_attributes(
        self,
    ) -> dict[str, int | str | list[str]] | None:
        """Expose the wire address and raw value for diagnosis.

        32-bit counters also expose both raw words in received order, so the
        provisional high-word-first interpretation can be checked against the
        wire without a second tool.  The three heat-demand sensors also expose
        the configured consumer label for dashboards.
        """
        reading = self._reading
        if reading is None:
            # The consumer label is configuration rather than a measurement,
            # so it remains readable while the block is unavailable.
            if self._consumer_name is not None:
                return {"consumer_name": self._consumer_name}
            return None
        digits = reading.width // 4
        attributes: dict[str, int | str | list[str]] = {
            "register": reading.address,
            "raw_value": f"0x{reading.raw:0{digits}X}",
        }
        if reading.width > 16:
            attributes["raw_words"] = [
                f"0x{word:04X}" for word in reading.raw_words
            ]
        if self._consumer_name is not None:
            attributes["consumer_name"] = self._consumer_name
        return attributes

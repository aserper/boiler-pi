"""Integration-level tests without a boiler, Home Assistant, or pymodbus.

The integration imports Home Assistant and pymodbus at module load.  This
checkout has neither installed, so the test installs small contract fakes in
``sys.modules`` before importing the integration.  The fakes mirror the
specific APIs used by this integration; they are not a substitute for running
the real Home Assistant test harness (see the limitations in the README and
the task report).  The fake modules remain in ``sys.modules`` for this test
process, so do not run these tests in the same process as real Home Assistant
integration tests.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
import types
import unittest
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Generic, TypeVar
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CAPTURE_RAW = (0x0001, 0x008C, 0x7FFF, 0x0079, 0x0078, 0x7FFF, 0x0000, 0x005C)

# Responses keyed by the requested (address, count).  The HR 13 block is the
# original field capture; the remaining values are from the 2026-11-13 live
# read-only capture.  HR 24..29 combines the captured 24..27 and 28..29 reads.
LIVE_READ_RESPONSES: dict[tuple[int, int], tuple[int, ...]] = {
    (13, 8): CAPTURE_RAW,
    (3, 1): (0x0000,),
    (9, 2): (0x0000, 0x0000),
    (24, 6): (0x0000, 0x0017, 0x0000, 0x011C, 0x0000, 0x0000),
    (100, 2): (0x0001, 0x0000),
    (103, 2): (0x0000, 0x0000),
    (106, 3): (0x0000, 0x0000, 0x0000),
    (686, 6): (0x0000, 0x010B, 0x0000, 0x0199, 0x0000, 0x0018),
}

EXPECTED_SENSOR_KEYS = {
    "burner_control_state",
    "target_temperature",
    "supply_temperature",
    "return_temperature",
    "dhw_temperature",
    "stack_temperature",
    "dhw_heat_demand",
    "ch1_heat_demand",
    "ch2_heat_demand",
    "hold_code",
    "lockout_code",
    "demand_source",
    "flame_signal",
    "fan_speed_demanded",
    "fan_speed_feedback",
    "pump_a_status",
    "pump_b_status",
    "pump_c_status",
    "burner_run_time_hours",
    "burner_cycle_count",
    "pump_a_cycles",
    "pump_b_cycles",
    "pump_c_cycles",
}

# ---------------------------------------------------------------------------
# Contract fakes
# ---------------------------------------------------------------------------


def _module(name: str, **attributes: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module
    return module


class FakeResponse:
    """Minimal stand-in for a pymodbus register response."""

    def __init__(
        self,
        registers: tuple[int, ...] | list[int] | None = None,
        *,
        error: bool = False,
    ) -> None:
        self.registers = list(registers) if registers is not None else None
        self._error = error

    def isError(self) -> bool:  # noqa: N802 - mirrors pymodbus
        return self._error

    def __repr__(self) -> str:
        return f"FakeResponse(registers={self.registers!r}, error={self._error})"


class FakeModbusClient:
    """Fake async pymodbus client shared by TCP and serial tests."""

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self._connected = False
        self.connect_result = True
        self.connect_calls = 0
        self.close_calls = 0
        self.read_calls: list[dict[str, int]] = []
        self.responses: dict[tuple[int, int], FakeResponse] = {
            key: FakeResponse(values)
            for key, values in LIVE_READ_RESPONSES.items()
        }
        self.error_reads: set[tuple[int, int]] = set()
        self.fail_reads: dict[tuple[int, int], BaseException] = {}
        self.read_exception: BaseException | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        self.connect_calls += 1
        self._connected = self.connect_result
        return self.connect_result

    async def read_holding_registers(
        self,
        address: int,
        *,
        count: int = 1,
        device_id: int = 1,
        no_response_expected: bool = False,
    ) -> FakeResponse:
        self.read_calls.append(
            {"address": address, "count": count, "device_id": device_id}
        )
        if self.read_exception is not None:
            raise self.read_exception
        key = (address, count)
        failure = self.fail_reads.get(key)
        if failure is not None:
            raise failure
        if key in self.error_reads:
            return FakeResponse(error=True)
        response = self.responses.get(key)
        if response is None:
            return FakeResponse(())
        return response

    def close(self) -> None:
        self.close_calls += 1
        self._connected = False


class FakeTcpClient(FakeModbusClient):
    """Fake AsyncModbusTcpClient."""


class FakeSerialClient(FakeModbusClient):
    """Fake AsyncModbusSerialClient."""


# pymodbus
_pymodbus = _module("pymodbus")
_pymodbus_client = _module(
    "pymodbus.client",
    AsyncModbusTcpClient=FakeTcpClient,
    AsyncModbusSerialClient=FakeSerialClient,
)
_pymodbus.client = _pymodbus_client


# Home Assistant constants
class Platform(str, Enum):
    SENSOR = "sensor"


class UnitOfTemperature(str, Enum):
    FAHRENHEIT = "°F"


class UnitOfTime(str, Enum):
    HOURS = "h"


PERCENTAGE = "%"


_ha_const = _module(
    "homeassistant.const",
    Platform=Platform,
    PERCENTAGE=PERCENTAGE,
    UnitOfTemperature=UnitOfTemperature,
    UnitOfTime=UnitOfTime,
    MAJOR_VERSION=2026,
    MINOR_VERSION=11,
    PATCH_VERSION="1",
)


class HomeAssistant:
    """Fake HomeAssistant object."""


def callback(func: Any) -> Any:
    return func


_ha_core = _module(
    "homeassistant.core",
    HomeAssistant=HomeAssistant,
    callback=callback,
)


class ConfigEntryNotReady(Exception):
    """Fake Home Assistant ConfigEntryNotReady."""


class HomeAssistantError(Exception):
    """Fake Home Assistant HomeAssistantError."""


_ha_exceptions = _module(
    "homeassistant.exceptions",
    ConfigEntryNotReady=ConfigEntryNotReady,
    HomeAssistantError=HomeAssistantError,
)


class AbortFlow(Exception):
    """Fake config-flow abort exception."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


_ha_data_entry_flow = _module(
    "homeassistant.data_entry_flow",
    AbortFlow=AbortFlow,
)


@dataclass
class ConfigEntry:
    """Fake ConfigEntry with the attributes this integration uses."""

    entry_id: str = "entry-1"
    data: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    title: str = "Burnham ALTA test"
    domain: str = "burnham_alta"
    unique_id: str | None = None


class FakeConfigEntries:
    """Fake hass.config_entries manager."""

    def __init__(self) -> None:
        self.unique_ids: set[str] = set()
        self.entries: dict[str, ConfigEntry] = {}
        self.forwarded: list[tuple[str, list[Platform]]] = []
        self.unloaded: list[tuple[str, list[Platform]]] = []
        self.unload_result = True

    def async_get_known_entry(self, entry_id: str) -> ConfigEntry:
        """Return a configured entry, like the real ConfigEntries manager."""
        return self.entries[entry_id]

    async def async_forward_entry_setups(
        self, entry: ConfigEntry, platforms: list[Platform]
    ) -> None:
        self.forwarded.append((entry.entry_id, list(platforms)))

    async def async_unload_platforms(
        self, entry: ConfigEntry, platforms: list[Platform]
    ) -> bool:
        self.unloaded.append((entry.entry_id, list(platforms)))
        return self.unload_result


class FakeHass:
    """Fake HomeAssistant with the data and config-entry surfaces used here."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.config_entries = FakeConfigEntries()


class ConfigFlow:
    """Small stand-in for homeassistant.config_entries.ConfigFlow."""

    VERSION = 1

    def __init_subclass__(cls, *, domain: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.domain = domain

    def __init__(self) -> None:
        self.hass: FakeHass | None = None
        self.context: dict[str, Any] = {}
        self._unique_id: str | None = None
        self.flow_id = "flow-1"

    async def async_set_unique_id(
        self, unique_id: str, *, raise_on_progress: bool = True
    ) -> None:
        self._unique_id = unique_id
        self.context["unique_id"] = unique_id

    def _abort_if_unique_id_configured(self, *args: Any, **kwargs: Any) -> None:
        if (
            self.hass is not None
            and self._unique_id in self.hass.config_entries.unique_ids
        ):
            raise AbortFlow("already_configured")

    def async_create_entry(
        self,
        *,
        title: str | None = None,
        data: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": dict(data)}

    def async_show_form(
        self,
        *,
        step_id: str | None = None,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_show_menu(
        self,
        *,
        step_id: str | None = None,
        menu_options: list[str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "type": "menu",
            "step_id": step_id,
            "menu_options": list(menu_options),
        }


class OptionsFlow:
    """Small stand-in for homeassistant.config_entries.OptionsFlow."""

    def __init__(self) -> None:
        self.hass: FakeHass | None = None
        self.context: dict[str, Any] = {}
        self.handler: str | None = None
        self.flow_id = "options-flow-1"

    @property
    def config_entry(self) -> ConfigEntry:
        if self.hass is None or self.handler is None:
            raise ValueError("The config entry is not available")
        return self.hass.config_entries.async_get_known_entry(self.handler)

    def async_create_entry(
        self,
        *,
        data: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"type": "create_entry", "data": dict(data)}

    def async_show_form(
        self,
        *,
        step_id: str | None = None,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }


class OptionsFlowWithReload(OptionsFlow):
    """Fake automatic-reload options flow base."""

    automatic_reload = True


ConfigFlowResult = dict[str, Any]

_ha_config_entries = _module(
    "homeassistant.config_entries",
    ConfigEntry=ConfigEntry,
    ConfigFlow=ConfigFlow,
    ConfigFlowResult=ConfigFlowResult,
    OptionsFlow=OptionsFlow,
    OptionsFlowWithReload=OptionsFlowWithReload,
)


# update_coordinator
_DataT = TypeVar("_DataT")


class UpdateFailed(Exception):
    """Fake DataUpdateCoordinator.UpdateFailed."""


class DataUpdateCoordinator(Generic[_DataT]):
    """Fake DataUpdateCoordinator with the methods used by this integration."""

    def __init__(
        self,
        hass: FakeHass,
        logger: Any,
        *,
        name: str,
        update_interval: timedelta | None = None,
        config_entry: ConfigEntry | None = None,
        **kwargs: Any,
    ) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.config_entry = config_entry
        self.data: _DataT | None = None
        self.last_update_success = True
        self.last_exception: BaseException | None = None
        self.shutdown_called = False

    async def _async_update_data(self) -> _DataT:  # pragma: no cover - overridden
        raise NotImplementedError

    async def async_config_entry_first_refresh(self) -> None:
        try:
            self.data = await self._async_update_data()
        except UpdateFailed as err:
            self.last_update_success = False
            self.last_exception = err
            raise ConfigEntryNotReady(str(err)) from err
        self.last_update_success = True

    async def async_refresh(self) -> None:
        try:
            self.data = await self._async_update_data()
        except UpdateFailed as err:
            self.last_update_success = False
            self.last_exception = err
            return
        self.last_update_success = True

    async def async_shutdown(self) -> None:
        self.shutdown_called = True

    def async_add_listener(self, update_callback: Any, context: Any = None) -> Any:
        return lambda: None


class CoordinatorEntity(Generic[_DataT]):
    """Fake CoordinatorEntity."""

    def __init__(self, coordinator: DataUpdateCoordinator[Any]) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    def async_write_ha_state(self) -> None:
        return None


_ha_helpers = _module("homeassistant.helpers")
_ha_update_coordinator = _module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=DataUpdateCoordinator,
    CoordinatorEntity=CoordinatorEntity,
    UpdateFailed=UpdateFailed,
)
_ha_helpers.update_coordinator = _ha_update_coordinator


# device_registry / entity_platform
def DeviceInfo(**kwargs: Any) -> dict[str, Any]:  # noqa: N802 - mirrors HA
    return dict(kwargs)


_ha_device_registry = _module(
    "homeassistant.helpers.device_registry",
    DeviceInfo=DeviceInfo,
)
_ha_helpers.device_registry = _ha_device_registry

_ha_entity_platform = _module(
    "homeassistant.helpers.entity_platform",
    AddEntitiesCallback=object,
)
_ha_helpers.entity_platform = _ha_entity_platform


# components.sensor
class SensorDeviceClass(str, Enum):
    DURATION = "duration"
    TEMPERATURE = "temperature"


class SensorStateClass(str, Enum):
    MEASUREMENT = "measurement"
    TOTAL_INCREASING = "total_increasing"


@dataclass(frozen=True, kw_only=True)
class SensorEntityDescription:
    key: str
    translation_key: str | None = None
    name: str | None = None
    icon: str | None = None
    device_class: Any = None
    state_class: Any = None
    native_unit_of_measurement: str | None = None
    suggested_display_precision: int | None = None


class SensorEntity:
    """Fake SensorEntity."""

    _attr_has_entity_name = False
    entity_description: SensorEntityDescription | None = None
    _attr_unique_id: str | None = None
    _attr_device_info: dict[str, Any] | None = None

    @property
    def unique_id(self) -> str | None:
        return self._attr_unique_id

    @property
    def name(self) -> str | None:
        """Mirror Entity.name: an explicit name wins over the description."""
        return getattr(self, "_attr_name", None)

    @property
    def device_info(self) -> dict[str, Any] | None:
        return self._attr_device_info

    @property
    def available(self) -> bool:
        return True

    def async_write_ha_state(self) -> None:
        return None


_ha_components = _module("homeassistant.components")
_ha_sensor = _module(
    "homeassistant.components.sensor",
    SensorDeviceClass=SensorDeviceClass,
    SensorEntity=SensorEntity,
    SensorEntityDescription=SensorEntityDescription,
    SensorStateClass=SensorStateClass,
)
_ha_components.sensor = _ha_sensor


# helpers.selector
class _SelectorConfig(dict[str, Any]):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


class NumberSelector:
    def __init__(self, config: Any = None) -> None:
        self.config = config


class NumberSelectorConfig(_SelectorConfig):
    pass


class NumberSelectorMode(str, Enum):
    BOX = "box"
    SLIDER = "slider"


class TextSelector:
    def __init__(self, config: Any = None) -> None:
        self.config = config


class TextSelectorConfig(_SelectorConfig):
    pass


class TextSelectorType(str, Enum):
    TEXT = "text"


class SerialPortSelector:
    def __init__(self, config: Any = None) -> None:
        self.config = config


_ha_selector = _module(
    "homeassistant.helpers.selector",
    NumberSelector=NumberSelector,
    NumberSelectorConfig=NumberSelectorConfig,
    NumberSelectorMode=NumberSelectorMode,
    SerialPortSelector=SerialPortSelector,
    TextSelector=TextSelector,
    TextSelectorConfig=TextSelectorConfig,
    TextSelectorType=TextSelectorType,
)
_ha_helpers.selector = _ha_selector


# probatio (HA 2026.11 schema library); its API mirrors voluptuous.
class _Identity:
    def __call__(self, value: Any) -> Any:
        return value


class Invalid(Exception):
    pass


class MultipleInvalid(Invalid):
    pass


class Schema:
    def __init__(self, schema: Any = None, **kwargs: Any) -> None:
        self.schema = schema

    def __call__(self, data: Any) -> Any:
        return data


class _SchemaKey(str):
    """String schema key that also carries its default, like real schemas do."""

    default: Any

    def __new__(
        cls, key: str, default: Any = None, **kwargs: Any
    ) -> "_SchemaKey":
        instance = super().__new__(cls, key)
        instance.default = default
        return instance


def Required(key: str, default: Any = None, **kwargs: Any) -> _SchemaKey:  # noqa: N802
    return _SchemaKey(key, default)


def Optional(key: str, default: Any = None, **kwargs: Any) -> _SchemaKey:  # noqa: N802
    return _SchemaKey(key, default)


def All(*validators: Any) -> _Identity:  # noqa: N802
    return _Identity()


def Coerce(type_: Any) -> _Identity:  # noqa: N802
    return _Identity()


def Range(min: Any = None, max: Any = None) -> _Identity:  # noqa: N802
    return _Identity()


def In(container: Any) -> _Identity:  # noqa: N802
    return _Identity()


_probatio = _module(
    "probatio",
    Schema=Schema,
    Required=Required,
    Optional=Optional,
    All=All,
    Coerce=Coerce,
    Range=Range,
    In=In,
    Invalid=Invalid,
    MultipleInvalid=MultipleInvalid,
)


# Parent package attributes, so ``from homeassistant.x import y`` works even
# though these modules are synthetic.
_homeassistant = _module("homeassistant")
_homeassistant.const = _ha_const
_homeassistant.core = _ha_core
_homeassistant.exceptions = _ha_exceptions
_homeassistant.data_entry_flow = _ha_data_entry_flow
_homeassistant.config_entries = _ha_config_entries
_homeassistant.helpers = _ha_helpers
_homeassistant.components = _ha_components


# ---------------------------------------------------------------------------
# Integration imports (only after the fakes exist)
# ---------------------------------------------------------------------------

from custom_components.burnham_alta import (  # noqa: E402
    async_setup_entry,
    async_unload_entry,
)
from custom_components.burnham_alta import config_flow as config_flow_module  # noqa: E402
from custom_components.burnham_alta import modbus as modbus_module  # noqa: E402
from custom_components.burnham_alta import sensor as sensor_module  # noqa: E402
from custom_components.burnham_alta.const import (  # noqa: E402
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CH1_NAME,
    CONF_CH2_NAME,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_ID,
    CONF_DHW_NAME,
    CONF_HOST,
    CONF_PARITY,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL_PORT,
    CONF_STOPBITS,
    CONNECTION_SERIAL,
    CONNECTION_TCP,
    DEFAULT_CH1_NAME,
    DEFAULT_CH2_NAME,
    DEFAULT_DHW_NAME,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MAX_CONSUMER_NAME_LENGTH,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    READ_BLOCKS,
    consumer_name,
    validate_consumer_name,
)
from custom_components.burnham_alta.coordinator import BurnhamAltaCoordinator  # noqa: E402
from custom_components.burnham_alta.decoder import (  # noqa: E402
    Register,
    decode_blocks,
    decode_registers,
)
from custom_components.burnham_alta.modbus import (  # noqa: E402
    AltaConnectionError,
    AltaModbusClient,
    AltaModbusError,
    AltaResponseError,
    create_client,
    entry_title_for,
    normalize_config,
    unique_id_for,
    validate_connection,
)

LIVE_BLOCKS = tuple(
    (address, values)
    for (address, _count), values in LIVE_READ_RESPONSES.items()
)
SNAPSHOT = decode_blocks(LIVE_BLOCKS)
CONFIG_TCP = {
    CONF_CONNECTION_TYPE: CONNECTION_TCP,
    CONF_HOST: "192.0.2.10",
    CONF_PORT: 1502,
    CONF_DEVICE_ID: 1,
}
CONFIG_SERIAL = {
    CONF_CONNECTION_TYPE: CONNECTION_SERIAL,
    CONF_SERIAL_PORT: "/dev/ttyUSB0",
    CONF_BAUDRATE: 38400,
    CONF_DEVICE_ID: 1,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class NormalizeConfigTests(unittest.TestCase):
    def test_tcp_defaults(self):
        normalized = normalize_config(
            {CONF_CONNECTION_TYPE: CONNECTION_TCP, CONF_HOST: " gw.local "}
        )
        self.assertEqual(normalized[CONF_HOST], "gw.local")
        self.assertEqual(normalized[CONF_PORT], 502)
        self.assertEqual(normalized[CONF_DEVICE_ID], 1)

    def test_serial_framing_is_fixed(self):
        normalized = normalize_config(
            {
                CONF_CONNECTION_TYPE: CONNECTION_SERIAL,
                CONF_SERIAL_PORT: "/dev/ttyUSB0",
                CONF_BAUDRATE: "38400",
                CONF_DEVICE_ID: 1,
            }
        )
        self.assertEqual(normalized[CONF_BAUDRATE], 38400)
        self.assertEqual(normalized[CONF_BYTESIZE], 8)
        self.assertEqual(normalized[CONF_PARITY], "N")
        self.assertEqual(normalized[CONF_STOPBITS], 1)

    def test_invalid_connection_type(self):
        with self.assertRaises(ValueError):
            normalize_config({CONF_CONNECTION_TYPE: "rtuovertcp"})

    def test_invalid_ranges(self):
        for key, value in (
            (CONF_DEVICE_ID, 0),
            (CONF_DEVICE_ID, 248),
            (CONF_PORT, 0),
            (CONF_PORT, 65536),
        ):
            with self.subTest(key=key, value=value):
                config = dict(CONFIG_TCP)
                config[key] = value
                with self.assertRaises(ValueError):
                    normalize_config(config)

    def test_unique_ids_are_stable_and_distinct(self):
        first = unique_id_for(CONFIG_TCP)
        second = unique_id_for({**CONFIG_TCP, CONF_HOST: "192.0.2.10"})
        self.assertEqual(first, "burnham_alta:tcp:192.0.2.10:1502:1")
        self.assertEqual(first, second)
        self.assertNotEqual(first, unique_id_for(CONFIG_SERIAL))

    def test_entry_title(self):
        self.assertIn("192.0.2.10:1502", entry_title_for(CONFIG_TCP))
        self.assertIn("ttyUSB0", entry_title_for(CONFIG_SERIAL))


class ConsumerNameTests(unittest.TestCase):
    def test_generic_defaults(self):
        self.assertEqual(consumer_name(None, CONF_CH1_NAME), DEFAULT_CH1_NAME)
        self.assertEqual(consumer_name({}, CONF_CH2_NAME), DEFAULT_CH2_NAME)
        self.assertEqual(consumer_name({}, CONF_DHW_NAME), DEFAULT_DHW_NAME)

    def test_labels_are_trimmed(self):
        self.assertEqual(
            consumer_name({CONF_CH1_NAME: "  Ground floor  "}, CONF_CH1_NAME),
            "Ground floor",
        )

    def test_blank_missing_and_non_text_fall_back_to_default(self):
        for value in ("", "   ", None, 3, True, ["Basement"]):
            with self.subTest(value=value):
                self.assertEqual(
                    consumer_name({CONF_CH1_NAME: value}, CONF_CH1_NAME),
                    DEFAULT_CH1_NAME,
                )

    def test_overlong_stored_label_is_truncated(self):
        label = consumer_name(
            {CONF_CH1_NAME: "x" * (MAX_CONSUMER_NAME_LENGTH + 20)},
            CONF_CH1_NAME,
        )
        self.assertEqual(len(label), MAX_CONSUMER_NAME_LENGTH)

    def test_validation_accepts_trimmed_text_and_blank_default(self):
        self.assertEqual(
            validate_consumer_name(CONF_CH2_NAME, "  Attic  "), "Attic"
        )
        self.assertEqual(
            validate_consumer_name(CONF_CH2_NAME, "   "), DEFAULT_CH2_NAME
        )
        self.assertEqual(
            validate_consumer_name(CONF_DHW_NAME, "x" * MAX_CONSUMER_NAME_LENGTH),
            "x" * MAX_CONSUMER_NAME_LENGTH,
        )

    def test_validation_rejects_absurd_length_and_non_text(self):
        for value in (
            "x" * (MAX_CONSUMER_NAME_LENGTH + 1),
            3,
            True,
            None,
            ["Basement"],
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_consumer_name(CONF_CH1_NAME, value)


class CreateClientTests(unittest.TestCase):
    def test_tcp_client_kwargs(self):
        client = create_client(CONFIG_TCP)
        self.assertIsInstance(client, FakeTcpClient)
        self.assertEqual(client.init_kwargs["host"], "192.0.2.10")
        self.assertEqual(client.init_kwargs["port"], 1502)
        self.assertEqual(client.init_kwargs["timeout"], 3.0)
        self.assertEqual(client.init_kwargs["retries"], 1)

    def test_serial_client_kwargs(self):
        client = create_client(CONFIG_SERIAL)
        self.assertIsInstance(client, FakeSerialClient)
        self.assertEqual(client.init_kwargs["port"], "/dev/ttyUSB0")
        self.assertEqual(client.init_kwargs["baudrate"], 38400)
        self.assertEqual(client.init_kwargs["bytesize"], 8)
        self.assertEqual(client.init_kwargs["parity"], "N")
        self.assertEqual(client.init_kwargs["stopbits"], 1)


class ModbusClientTests(unittest.IsolatedAsyncioTestCase):
    def test_client_creation_is_lazy_for_the_running_loop(self):
        with patch.object(
            modbus_module,
            "create_client",
            side_effect=AssertionError("must not be called in __init__"),
        ):
            subject = AltaModbusClient(CONFIG_TCP)
        self.assertIsNone(subject.client)

    async def test_every_documented_block_is_requested_once(self):
        fake = FakeTcpClient()
        subject = AltaModbusClient(CONFIG_TCP, client=fake)

        snapshot = await subject.async_read_snapshot()

        self.assertEqual(fake.connect_calls, 1)
        self.assertEqual(
            fake.read_calls,
            [
                {"address": address, "count": count, "device_id": 1}
                for address, count in READ_BLOCKS
            ],
        )
        self.assertEqual(
            snapshot.readings[Register.SUPPLY_TEMPERATURE].value, 121
        )
        self.assertEqual(snapshot.readings[Register.RETURN_TEMPERATURE].value, 120)
        self.assertIsNone(snapshot.readings[Register.DHW_TEMPERATURE].value)
        self.assertEqual(
            snapshot.readings[Register.BURNER_RUN_TIME_HOURS].value, 23
        )
        self.assertEqual(snapshot.readings[Register.BURNER_CYCLE_COUNT].value, 284)
        self.assertEqual(snapshot.readings[Register.PUMP_A_CYCLES].value, 267)
        self.assertEqual(snapshot.readings[Register.PUMP_B_CYCLES].value, 409)
        self.assertEqual(snapshot.readings[Register.PUMP_C_CYCLES].value, 24)
        self.assertEqual(snapshot.block_errors, {})
        self.assertEqual(fake.close_calls, 0)

    async def test_cancelled_connect_closes_transport(self):
        fake = FakeTcpClient()
        fake.connect = AsyncMock(side_effect=asyncio.CancelledError)
        subject = AltaModbusClient(CONFIG_TCP, client=fake)
        with self.assertRaises(asyncio.CancelledError):
            await subject.async_read_snapshot()
        self.assertEqual(fake.close_calls, 1)
        self.assertIsNone(subject.client)

    async def test_cancelled_read_closes_transport(self):
        fake = FakeTcpClient()
        fake.read_exception = asyncio.CancelledError()
        subject = AltaModbusClient(CONFIG_TCP, client=fake)
        with self.assertRaises(asyncio.CancelledError):
            await subject.async_read_snapshot()
        self.assertEqual(fake.close_calls, 1)
        self.assertIsNone(subject.client)

    async def test_connection_failure_is_reported_and_closed(self):
        fake = FakeTcpClient()
        fake.connect_result = False
        subject = AltaModbusClient(CONFIG_TCP, client=fake)

        with self.assertRaises(AltaConnectionError):
            await subject.async_read_snapshot()

        self.assertEqual(fake.close_calls, 1)
        self.assertIsNone(subject.client)

    async def test_read_failure_closes_for_reconnect(self):
        fake = FakeTcpClient()
        fake.read_exception = OSError("link down")
        subject = AltaModbusClient(CONFIG_TCP, client=fake)

        with self.assertRaises(AltaConnectionError):
            await subject.async_read_snapshot()

        self.assertEqual(fake.close_calls, 1)
        self.assertIsNone(subject.client)

    async def test_modbus_error_response(self):
        fake = FakeTcpClient()
        fake.error_reads.update(LIVE_READ_RESPONSES)
        subject = AltaModbusClient(CONFIG_TCP, client=fake)

        with self.assertRaises(AltaModbusError):
            await subject.async_read_snapshot()

    async def test_short_response_is_rejected(self):
        fake = FakeTcpClient()
        fake.responses[(13, 8)] = FakeResponse(CAPTURE_RAW[:3])
        subject = AltaModbusClient(CONFIG_TCP, client=fake)

        with self.assertRaises(AltaResponseError):
            await subject.async_read_snapshot()

        self.assertEqual(fake.close_calls, 1)

    async def test_validate_connection_always_closes(self):
        fake = FakeTcpClient()
        with patch.object(
            modbus_module, "create_client", return_value=fake
        ) as factory:
            snapshot = await validate_connection(CONFIG_TCP)
            factory.assert_called_once()

        self.assertEqual(snapshot.readings[Register.STACK_TEMPERATURE].value, 92)
        self.assertEqual(snapshot.readings[Register.DEMAND_SOURCE].value, 1)
        self.assertEqual(len(fake.read_calls), len(READ_BLOCKS))
        self.assertEqual(fake.close_calls, 1)


    async def test_one_error_block_returns_partial_snapshot(self):
        fake = FakeTcpClient()
        fake.error_reads.add((686, 6))
        subject = AltaModbusClient(CONFIG_TCP, client=fake)

        snapshot = await subject.async_read_snapshot()

        self.assertIn(Register.SUPPLY_TEMPERATURE, snapshot.readings)
        self.assertIn(Register.BURNER_CYCLE_COUNT, snapshot.readings)
        self.assertNotIn(Register.PUMP_A_CYCLES, snapshot.readings)
        self.assertEqual(snapshot.failed_blocks, {(686, 6)})
        self.assertIn((686, 6), snapshot.block_errors)
        # A Modbus exception response is not a transport failure: keep the
        # connection and read every other block.
        self.assertEqual(fake.close_calls, 0)
        self.assertEqual(
            [call["address"] for call in fake.read_calls],
            [address for address, _ in READ_BLOCKS],
        )

    async def test_transport_failure_mid_cycle_keeps_earlier_blocks(self):
        fake = FakeTcpClient()
        fake.fail_reads[(100, 2)] = OSError("link down")
        subject = AltaModbusClient(CONFIG_TCP, client=fake)

        snapshot = await subject.async_read_snapshot()

        self.assertIn(Register.SUPPLY_TEMPERATURE, snapshot.readings)
        self.assertIn(Register.BURNER_RUN_TIME_HOURS, snapshot.readings)
        self.assertNotIn(Register.DEMAND_SOURCE, snapshot.readings)
        self.assertNotIn(Register.PUMP_A_CYCLES, snapshot.readings)
        self.assertEqual(snapshot.failed_blocks, {(100, 2)})
        self.assertEqual(
            [call["address"] for call in fake.read_calls],
            [13, 3, 9, 24, 100],
        )
        self.assertEqual(fake.close_calls, 1)
        self.assertIsNone(subject.client)

        # The next cycle reconnects and reads the full sequence again.
        fake.fail_reads.clear()
        with patch.object(modbus_module, "create_client", return_value=fake):
            snapshot = await subject.async_read_snapshot()
        self.assertEqual(fake.connect_calls, 2)
        self.assertEqual(snapshot.failed_blocks, set())
        self.assertEqual(snapshot.readings[Register.DEMAND_SOURCE].value, 1)
        self.assertEqual(len(fake.read_calls), len(READ_BLOCKS) + 5)


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    def _flow(self):
        flow = config_flow_module.BurnhamAltaConfigFlow()
        flow.hass = FakeHass()
        return flow

    async def test_user_step_shows_menu(self):
        result = await self._flow().async_step_user()
        self.assertEqual(result["type"], "menu")
        self.assertEqual(result["menu_options"], ["tcp", "serial"])

    async def test_tcp_creates_entry(self):
        flow = self._flow()
        with patch.object(
            config_flow_module,
            "validate_connection",
            new=AsyncMock(return_value=SNAPSHOT),
        ):
            result = await flow.async_step_tcp(
                {
                    "host": "gw.local",
                    "port": 1502,
                    "device_id": 1,
                    "connection_type": "serial",
                }
            )

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            result["data"],
            {
                CONF_CONNECTION_TYPE: CONNECTION_TCP,
                CONF_HOST: "gw.local",
                CONF_PORT: 1502,
                CONF_DEVICE_ID: 1,
            },
        )
        self.assertEqual(flow._unique_id, "burnham_alta:tcp:gw.local:1502:1")

    async def test_serial_creates_entry_with_fixed_framing(self):
        flow = self._flow()
        with patch.object(
            config_flow_module,
            "validate_connection",
            new=AsyncMock(return_value=SNAPSHOT),
        ):
            result = await flow.async_step_serial(
                {"serial_port": "/dev/ttyUSB0", "baudrate": 38400, "device_id": 1}
            )

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][CONF_BYTESIZE], 8)
        self.assertEqual(result["data"][CONF_PARITY], "N")
        self.assertEqual(result["data"][CONF_STOPBITS], 1)

    async def test_connection_error_returns_form(self):
        flow = self._flow()
        with patch.object(
            config_flow_module,
            "validate_connection",
            new=AsyncMock(side_effect=AltaConnectionError("down")),
        ):
            result = await flow.async_step_tcp(
                {"host": "gw.local", "port": 502, "device_id": 1}
            )

        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "cannot_connect")

    async def test_modbus_and_response_errors_return_form(self):
        for error, expected in (
            (AltaModbusError("exception 2"), "modbus_error"),
            (AltaResponseError("short"), "invalid_response"),
        ):
            with self.subTest(error=type(error).__name__):
                flow = self._flow()
                with patch.object(
                    config_flow_module,
                    "validate_connection",
                    new=AsyncMock(side_effect=error),
                ):
                    result = await flow.async_step_tcp(
                        {"host": "gw.local", "port": 502, "device_id": 1}
                    )
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["errors"]["base"], expected)

    async def test_invalid_config_does_not_connect(self):
        flow = self._flow()
        with patch.object(
            config_flow_module,
            "validate_connection",
            new=AsyncMock(return_value=SNAPSHOT),
        ) as validator:
            result = await flow.async_step_tcp(
                {"host": "", "port": 502, "device_id": 1}
            )

        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "invalid_config")
        validator.assert_not_called()

    async def test_already_configured_aborts(self):
        flow = self._flow()
        flow.hass.config_entries.unique_ids.add(unique_id_for(CONFIG_TCP))

        with patch.object(
            config_flow_module,
            "validate_connection",
            new=AsyncMock(return_value=SNAPSHOT),
        ):
            with self.assertRaises(AbortFlow) as context:
                await flow.async_step_tcp(
                    {"host": "192.0.2.10", "port": 1502, "device_id": 1}
                )

        self.assertEqual(context.exception.reason, "already_configured")


class OptionsFlowTests(unittest.IsolatedAsyncioTestCase):
    def _flow(self, options: dict[str, Any] | None = None):
        entry = ConfigEntry(
            entry_id="entry-1",
            data=dict(CONFIG_TCP),
            options=dict(options or {}),
        )
        flow = config_flow_module.BurnhamAltaOptionsFlow()
        flow.hass = FakeHass()
        flow.hass.config_entries.entries[entry.entry_id] = entry
        flow.handler = entry.entry_id
        return flow, entry

    @staticmethod
    def _defaults(result: dict[str, Any]) -> dict[str, Any]:
        return {
            key: key.default
            for key in result["data_schema"].schema
        }

    def test_config_flow_exposes_reloading_options_flow(self):
        flow = config_flow_module.BurnhamAltaConfigFlow.async_get_options_flow(
            ConfigEntry()
        )
        self.assertIsInstance(flow, config_flow_module.BurnhamAltaOptionsFlow)
        self.assertIsInstance(flow, config_flow_module.OptionsFlowWithReload)
        self.assertTrue(flow.automatic_reload)

    async def test_form_defaults_to_ten_for_existing_entry(self):
        flow, _ = self._flow()
        result = await flow.async_step_init()

        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "init")
        defaults = self._defaults(result)
        self.assertEqual(
            defaults[CONF_SCAN_INTERVAL], DEFAULT_SCAN_INTERVAL_SECONDS
        )
        self.assertEqual(defaults[CONF_CH1_NAME], DEFAULT_CH1_NAME)
        self.assertEqual(defaults[CONF_CH2_NAME], DEFAULT_CH2_NAME)
        self.assertEqual(defaults[CONF_DHW_NAME], DEFAULT_DHW_NAME)

    async def test_form_shows_the_current_options(self):
        flow, _ = self._flow(
            {
                CONF_SCAN_INTERVAL: 45,
                CONF_CH1_NAME: "Ground floor",
                CONF_CH2_NAME: "Upstairs",
                CONF_DHW_NAME: "Hot water",
            }
        )
        result = await flow.async_step_init()

        defaults = self._defaults(result)
        self.assertEqual(defaults[CONF_SCAN_INTERVAL], 45)
        self.assertEqual(defaults[CONF_CH1_NAME], "Ground floor")
        self.assertEqual(defaults[CONF_CH2_NAME], "Upstairs")
        self.assertEqual(defaults[CONF_DHW_NAME], "Hot water")

    async def test_valid_submission_creates_options(self):
        flow, _ = self._flow()
        result = await flow.async_step_init({CONF_SCAN_INTERVAL: 120})

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            result["data"],
            {
                CONF_SCAN_INTERVAL: 120,
                CONF_CH1_NAME: DEFAULT_CH1_NAME,
                CONF_CH2_NAME: DEFAULT_CH2_NAME,
                CONF_DHW_NAME: DEFAULT_DHW_NAME,
            },
        )

    async def test_submission_stores_trimmed_labels(self):
        flow, _ = self._flow()
        result = await flow.async_step_init(
            {
                CONF_SCAN_INTERVAL: 20,
                CONF_CH1_NAME: "  Ground floor  ",
                CONF_CH2_NAME: "Loft",
                CONF_DHW_NAME: "  Cylinder  ",
            }
        )

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][CONF_CH1_NAME], "Ground floor")
        self.assertEqual(result["data"][CONF_CH2_NAME], "Loft")
        self.assertEqual(result["data"][CONF_DHW_NAME], "Cylinder")

    async def test_blank_label_restores_the_default(self):
        flow, _ = self._flow(
            {
                CONF_SCAN_INTERVAL: 20,
                CONF_CH1_NAME: "Ground floor",
            }
        )
        result = await flow.async_step_init(
            {
                CONF_SCAN_INTERVAL: 20,
                CONF_CH1_NAME: "   ",
                CONF_CH2_NAME: "",
                CONF_DHW_NAME: "",
            }
        )

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][CONF_CH1_NAME], DEFAULT_CH1_NAME)
        self.assertEqual(result["data"][CONF_CH2_NAME], DEFAULT_CH2_NAME)
        self.assertEqual(result["data"][CONF_DHW_NAME], DEFAULT_DHW_NAME)

    async def test_submission_preserves_unrelated_options(self):
        flow, _ = self._flow({"future_option": "keep me"})
        result = await flow.async_step_init({CONF_SCAN_INTERVAL: 20})

        self.assertEqual(
            result["data"],
            {
                "future_option": "keep me",
                CONF_SCAN_INTERVAL: 20,
                CONF_CH1_NAME: DEFAULT_CH1_NAME,
                CONF_CH2_NAME: DEFAULT_CH2_NAME,
                CONF_DHW_NAME: DEFAULT_DHW_NAME,
            },
        )

    async def test_invalid_submissions_return_field_error(self):
        for value in (
            0,
            MIN_SCAN_INTERVAL_SECONDS - 1,
            MAX_SCAN_INTERVAL_SECONDS + 1,
            "not-a-number",
            True,
        ):
            with self.subTest(value=value):
                flow, _ = self._flow()
                result = await flow.async_step_init(
                    {CONF_SCAN_INTERVAL: value}
                )
                self.assertEqual(result["type"], "form")
                self.assertEqual(
                    result["errors"][CONF_SCAN_INTERVAL],
                    "invalid_scan_interval",
                )

    async def test_invalid_consumer_labels_return_field_errors(self):
        cases = (
            (CONF_CH1_NAME, "x" * (MAX_CONSUMER_NAME_LENGTH + 1)),
            (CONF_CH2_NAME, 7),
            (CONF_DHW_NAME, ["Cylinder"]),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                flow, _ = self._flow()
                result = await flow.async_step_init(
                    {CONF_SCAN_INTERVAL: 20, key: value}
                )
                self.assertEqual(result["type"], "form")
                self.assertEqual(
                    result["errors"], {key: "invalid_consumer_name"}
                )

    async def test_missing_value_falls_back_to_default(self):
        flow, _ = self._flow()
        result = await flow.async_step_init({})

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            result["data"][CONF_SCAN_INTERVAL],
            DEFAULT_SCAN_INTERVAL_SECONDS,
        )
        self.assertEqual(result["data"][CONF_CH1_NAME], DEFAULT_CH1_NAME)


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def _coordinator(self, options: dict[str, Any] | None = None):
        hass = FakeHass()
        entry = ConfigEntry(
            entry_id="entry-1",
            data=dict(CONFIG_TCP),
            options=dict(options or {}),
        )
        fake = FakeTcpClient()
        client = AltaModbusClient(CONFIG_TCP, client=fake)
        coordinator = BurnhamAltaCoordinator(hass, entry, client)
        return coordinator, fake

    async def test_first_refresh_reads_every_block(self):
        coordinator, fake = await self._coordinator()
        await coordinator.async_config_entry_first_refresh()

        self.assertIsNotNone(coordinator.data)
        self.assertEqual(
            coordinator.data.readings[Register.BURNER_CONTROL_STATE].value, 1
        )
        self.assertEqual(
            coordinator.update_interval,
            timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        self.assertEqual(len(fake.read_calls), len(READ_BLOCKS))

    async def test_options_set_the_polling_interval(self):
        coordinator, fake = await self._coordinator({CONF_SCAN_INTERVAL: 30})
        await coordinator.async_config_entry_first_refresh()

        self.assertEqual(coordinator.update_interval, timedelta(seconds=30))
        self.assertEqual(len(fake.read_calls), len(READ_BLOCKS))

    async def test_bounds_are_accepted(self):
        for seconds in (MIN_SCAN_INTERVAL_SECONDS, MAX_SCAN_INTERVAL_SECONDS):
            with self.subTest(seconds=seconds):
                coordinator, _ = await self._coordinator(
                    {CONF_SCAN_INTERVAL: seconds}
                )
                self.assertEqual(
                    coordinator.update_interval, timedelta(seconds=seconds)
                )

    async def test_invalid_options_cannot_poll_aggressively_or_crash(self):
        for value, expected in (
            (0, MIN_SCAN_INTERVAL_SECONDS),
            (1, MIN_SCAN_INTERVAL_SECONDS),
            (-30, MIN_SCAN_INTERVAL_SECONDS),
            (MAX_SCAN_INTERVAL_SECONDS + 1, MAX_SCAN_INTERVAL_SECONDS),
            ("not-a-number", DEFAULT_SCAN_INTERVAL_SECONDS),
            (None, DEFAULT_SCAN_INTERVAL_SECONDS),
            (True, DEFAULT_SCAN_INTERVAL_SECONDS),
            (float("inf"), DEFAULT_SCAN_INTERVAL_SECONDS),
            (4.9, MIN_SCAN_INTERVAL_SECONDS),
        ):
            with self.subTest(value=value):
                coordinator, fake = await self._coordinator(
                    {CONF_SCAN_INTERVAL: value}
                )
                self.assertEqual(
                    coordinator.update_interval, timedelta(seconds=expected)
                )
                await coordinator.async_config_entry_first_refresh()
                self.assertIsNotNone(coordinator.data)
                self.assertEqual(len(fake.read_calls), len(READ_BLOCKS))

    async def test_each_refresh_reads_every_block_once(self):
        coordinator, fake = await self._coordinator()
        await coordinator.async_config_entry_first_refresh()
        await coordinator.async_refresh()
        self.assertEqual(len(fake.read_calls), 2 * len(READ_BLOCKS))

    async def test_failure_is_config_entry_not_ready(self):
        coordinator, fake = await self._coordinator()
        fake.read_exception = OSError("down")

        with self.assertRaises(ConfigEntryNotReady):
            await coordinator.async_config_entry_first_refresh()

        self.assertFalse(coordinator.last_update_success)
        self.assertIsNotNone(coordinator.last_exception)


    async def test_partial_block_failure_is_a_successful_update(self):
        coordinator, fake = await self._coordinator()
        fake.error_reads.add((100, 2))

        await coordinator.async_config_entry_first_refresh()

        self.assertTrue(coordinator.last_update_success)
        self.assertIn(Register.SUPPLY_TEMPERATURE, coordinator.data.readings)
        self.assertNotIn(Register.DEMAND_SOURCE, coordinator.data.readings)
        self.assertEqual(coordinator.data.failed_blocks, {(100, 2)})


class SensorTests(unittest.IsolatedAsyncioTestCase):
    async def _coordinator(self, options: dict[str, Any] | None = None):
        hass = FakeHass()
        entry = ConfigEntry(
            entry_id="entry-1",
            data=dict(CONFIG_TCP),
            options=dict(options or {}),
        )
        client = AltaModbusClient(CONFIG_TCP, client=FakeTcpClient())
        coordinator = BurnhamAltaCoordinator(hass, entry, client)
        await coordinator.async_config_entry_first_refresh()
        return coordinator, entry

    def _entities(self, coordinator):
        return {
            description.key: sensor_module.BurnhamAltaSensor(
                coordinator, register, description
            )
            for register, description in sensor_module.SENSOR_SPECS
        }

    async def test_only_supported_entities_are_created(self):
        coordinator, _ = await self._coordinator()
        entities = self._entities(coordinator)
        self.assertEqual(set(entities), EXPECTED_SENSOR_KEYS)

    async def test_demand_sensors_use_consumer_labels(self):
        coordinator, _ = await self._coordinator(
            {
                CONF_CH1_NAME: "  Ground floor  ",
                CONF_CH2_NAME: "Loft",
                CONF_DHW_NAME: "Cylinder",
            }
        )
        entities = self._entities(coordinator)
        expected = {
            "ch1_heat_demand": "Ground floor",
            "ch2_heat_demand": "Loft",
            "dhw_heat_demand": "Cylinder",
        }

        for key, label in expected.items():
            with self.subTest(key=key):
                self.assertEqual(entities[key].name, label)
                self.assertEqual(
                    entities[key].extra_state_attributes["consumer_name"],
                    label,
                )

        for key in EXPECTED_SENSOR_KEYS - set(expected):
            with self.subTest(key=key):
                self.assertIsNone(entities[key].name)
                self.assertNotIn(
                    "consumer_name", entities[key].extra_state_attributes
                )

    async def test_demand_labels_change_without_changing_unique_ids(self):
        coordinator, entry = await self._coordinator()
        before = self._entities(coordinator)
        self.assertEqual(
            before["ch1_heat_demand"].name, DEFAULT_CH1_NAME
        )
        self.assertEqual(
            before["dhw_heat_demand"].name, DEFAULT_DHW_NAME
        )

        # Reloading an entry constructs new entities from the same entry with
        # updated options; unique IDs, and therefore entity IDs, must not move.
        entry.options.update(
            {
                CONF_CH1_NAME: "Ground floor",
                CONF_DHW_NAME: "Cylinder",
            }
        )
        after = self._entities(coordinator)

        self.assertEqual(
            before["ch1_heat_demand"].unique_id,
            after["ch1_heat_demand"].unique_id,
        )
        self.assertEqual(
            before["dhw_heat_demand"].unique_id,
            after["dhw_heat_demand"].unique_id,
        )
        self.assertEqual(after["ch1_heat_demand"].name, "Ground floor")
        self.assertEqual(after["dhw_heat_demand"].name, "Cylinder")

    async def test_sanitizes_stored_demand_labels(self):
        coordinator, _ = await self._coordinator(
            {
                CONF_CH1_NAME: "   ",
                CONF_CH2_NAME: "x" * (MAX_CONSUMER_NAME_LENGTH + 5),
            }
        )
        entities = self._entities(coordinator)

        self.assertEqual(
            entities["ch1_heat_demand"].name, DEFAULT_CH1_NAME
        )
        self.assertEqual(
            len(entities["ch2_heat_demand"].name),
            MAX_CONSUMER_NAME_LENGTH,
        )

    async def test_consumer_label_survives_a_missing_reading(self):
        coordinator, _ = await self._coordinator(
            {CONF_CH1_NAME: "Ground floor"}
        )
        coordinator.data = decode_registers(13, CAPTURE_RAW[:2])
        entity = self._entities(coordinator)["ch1_heat_demand"]

        self.assertFalse(entity.available)
        self.assertEqual(
            entity.extra_state_attributes, {"consumer_name": "Ground floor"}
        )

    async def test_values_and_raw_attributes(self):
        coordinator, entry = await self._coordinator()
        entities = self._entities(coordinator)

        self.assertEqual(entities["burner_control_state"].native_value, 1)
        self.assertEqual(
            entities["burner_control_state"].extra_state_attributes,
            {"register": 13, "raw_value": "0x0001"},
        )
        self.assertEqual(entities["target_temperature"].native_value, 140)
        self.assertEqual(entities["supply_temperature"].native_value, 121)
        self.assertEqual(entities["return_temperature"].native_value, 120)
        self.assertEqual(entities["stack_temperature"].native_value, 92)
        self.assertEqual(entities["ch1_heat_demand"].native_value, 0)
        self.assertEqual(entities["ch2_heat_demand"].native_value, 0)
        self.assertEqual(entities["dhw_heat_demand"].native_value, 0)
        self.assertEqual(entities["hold_code"].native_value, 0)
        self.assertEqual(entities["lockout_code"].native_value, 0)
        self.assertEqual(entities["demand_source"].native_value, 1)
        self.assertEqual(entities["flame_signal"].native_value, 0)
        self.assertEqual(entities["fan_speed_demanded"].native_value, 0)
        self.assertEqual(entities["fan_speed_feedback"].native_value, 0)
        self.assertEqual(entities["pump_a_status"].native_value, 0)
        self.assertEqual(entities["pump_b_status"].native_value, 0)
        self.assertEqual(entities["pump_c_status"].native_value, 0)
        self.assertEqual(entities["burner_run_time_hours"].native_value, 23)
        self.assertEqual(entities["burner_cycle_count"].native_value, 284)
        self.assertEqual(entities["pump_a_cycles"].native_value, 267)
        self.assertEqual(entities["pump_b_cycles"].native_value, 409)
        self.assertEqual(entities["pump_c_cycles"].native_value, 24)

        self.assertEqual(
            entities["burner_run_time_hours"].extra_state_attributes,
            {
                "register": 24,
                "raw_value": "0x00000017",
                "raw_words": ["0x0000", "0x0017"],
            },
        )

        self.assertEqual(
            entities["supply_temperature"].unique_id,
            f"{entry.entry_id}_supply_temperature",
        )
        self.assertEqual(
            entities["supply_temperature"].device_info["identifiers"],
            {(DOMAIN, entry.entry_id)},
        )

    async def test_sentinel_is_unavailable(self):
        coordinator, _ = await self._coordinator()
        entities = self._entities(coordinator)
        dhw = entities["dhw_temperature"]
        self.assertIsNone(dhw.native_value)
        self.assertFalse(dhw.available)
        self.assertEqual(
            dhw.extra_state_attributes,
            {"register": 18, "raw_value": "0x7FFF"},
        )

    async def test_missing_reading_is_unavailable(self):
        coordinator, _ = await self._coordinator()
        coordinator.data = decode_registers(13, CAPTURE_RAW[:2])
        entity = self._entities(coordinator)["supply_temperature"]
        self.assertIsNone(entity.native_value)
        self.assertFalse(entity.available)

    async def test_failed_block_only_disables_its_own_entities(self):
        coordinator, _ = await self._coordinator()
        blocks = [
            (address, values)
            for address, values in LIVE_BLOCKS
            if address != 106
        ]
        coordinator.data = decode_blocks(
            blocks, block_errors={(106, 3): "device rejected the block"}
        )
        entities = self._entities(coordinator)
        self.assertFalse(entities["pump_a_status"].available)
        self.assertIsNone(entities["pump_a_status"].native_value)
        self.assertTrue(entities["supply_temperature"].available)
        self.assertTrue(entities["pump_a_cycles"].available)

    async def test_coordinator_failure_is_unavailable(self):
        coordinator, _ = await self._coordinator()
        coordinator.last_update_success = False
        entity = self._entities(coordinator)["supply_temperature"]
        self.assertFalse(entity.available)

    async def test_setup_adds_all_entities(self):
        coordinator, entry = await self._coordinator()
        hass = FakeHass()
        hass.data[DOMAIN] = {entry.entry_id: coordinator}
        added: list[Any] = []

        await sensor_module.async_setup_entry(hass, entry, added.extend)

        self.assertEqual(len(added), len(EXPECTED_SENSOR_KEYS))
        self.assertEqual(
            {entity.entity_description.key for entity in added},
            EXPECTED_SENSOR_KEYS,
        )


class SetupTests(unittest.IsolatedAsyncioTestCase):
    class FakeAltaClient:
        instances: list["SetupTests.FakeAltaClient"] = []

        def __init__(self, data: dict[str, Any]) -> None:
            self.data = data
            self.closed = False
            self.reads = 0
            SetupTests.FakeAltaClient.instances.append(self)

        async def async_read_snapshot(self):
            self.reads += 1
            return SNAPSHOT

        def close(self) -> None:
            self.closed = True

    class FailingAltaClient:
        instances: list["SetupTests.FailingAltaClient"] = []

        def __init__(self, data: dict[str, Any]) -> None:
            self.data = data
            self.closed = False
            SetupTests.FailingAltaClient.instances.append(self)

        async def async_read_snapshot(self):
            raise AltaConnectionError("down")

        def close(self) -> None:
            self.closed = True

    async def test_setup_forwards_sensor_platform_and_unload_closes(self):
        SetupTests.FakeAltaClient.instances.clear()
        hass = FakeHass()
        entry = ConfigEntry(entry_id="entry-1", data=dict(CONFIG_TCP))

        with patch(
            "custom_components.burnham_alta.AltaModbusClient",
            SetupTests.FakeAltaClient,
        ):
            self.assertTrue(await async_setup_entry(hass, entry))

        coordinator = hass.data[DOMAIN][entry.entry_id]
        self.assertEqual(coordinator.data, SNAPSHOT)
        self.assertEqual(
            hass.config_entries.forwarded,
            [(entry.entry_id, [Platform.SENSOR])],
        )
        client = SetupTests.FakeAltaClient.instances[0]
        self.assertFalse(client.closed)

        self.assertTrue(await async_unload_entry(hass, entry))
        self.assertTrue(client.closed)
        self.assertTrue(coordinator.shutdown_called)
        self.assertNotIn(DOMAIN, hass.data)
        self.assertEqual(
            hass.config_entries.unloaded,
            [(entry.entry_id, [Platform.SENSOR])],
        )

    async def test_cancelled_first_refresh_closes_client(self):
        SetupTests.FakeAltaClient.instances.clear()
        hass = FakeHass()
        entry = ConfigEntry(entry_id="entry-1", data=dict(CONFIG_TCP))
        with patch("custom_components.burnham_alta.AltaModbusClient", SetupTests.FakeAltaClient), patch.object(
            BurnhamAltaCoordinator, "async_config_entry_first_refresh", new_callable=AsyncMock,
            side_effect=asyncio.CancelledError,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await async_setup_entry(hass, entry)
        self.assertTrue(SetupTests.FakeAltaClient.instances[0].closed)
        self.assertNotIn(DOMAIN, hass.data)

    async def test_cancelled_platform_setup_closes_client(self):
        SetupTests.FakeAltaClient.instances.clear()
        hass = FakeHass()
        entry = ConfigEntry(entry_id="entry-1", data=dict(CONFIG_TCP))
        hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=asyncio.CancelledError)
        with patch("custom_components.burnham_alta.AltaModbusClient", SetupTests.FakeAltaClient):
            with self.assertRaises(asyncio.CancelledError):
                await async_setup_entry(hass, entry)
        self.assertTrue(SetupTests.FakeAltaClient.instances[0].closed)
        self.assertNotIn(DOMAIN, hass.data)

    async def test_failed_first_refresh_closes_client(self):
        SetupTests.FailingAltaClient.instances.clear()
        hass = FakeHass()
        entry = ConfigEntry(entry_id="entry-1", data=dict(CONFIG_TCP))

        with patch(
            "custom_components.burnham_alta.AltaModbusClient",
            SetupTests.FailingAltaClient,
        ):
            with self.assertRaises(ConfigEntryNotReady):
                await async_setup_entry(hass, entry)

        self.assertTrue(SetupTests.FailingAltaClient.instances[0].closed)
        self.assertNotIn(DOMAIN, hass.data)


class ReadOnlyTests(unittest.TestCase):
    def test_no_write_calls_in_integration(self):
        integration_dir = REPO_ROOT / "custom_components" / "burnham_alta"
        for path in integration_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Attribute
                ):
                    self.assertFalse(
                        node.func.attr.startswith("write_"),
                        f"{path.name} calls {node.func.attr}",
                    )

    def test_no_forbidden_modbus_functions_are_named(self):
        integration_dir = REPO_ROOT / "custom_components" / "burnham_alta"
        forbidden = (
            "write_coil",
            "write_coils",
            "write_register",
            "write_registers",
            "mask_write_register",
            "read_coils",
            "read_discrete_inputs",
            "read_input_registers",
        )
        for path in integration_dir.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for name in forbidden:
                self.assertNotIn(name, source, f"{path.name} names {name}")

    def test_read_blocks_are_the_documented_plan(self):
        self.assertEqual(
            READ_BLOCKS,
            (
                (13, 8),
                (3, 1),
                (9, 2),
                (24, 6),
                (100, 2),
                (103, 2),
                (106, 3),
                (686, 6),
            ),
        )

    def test_read_blocks_cover_every_modelled_register(self):
        covered: set[int] = set()
        for start, count in READ_BLOCKS:
            self.assertGreater(count, 0)
            covered.update(range(start, start + count))
        # Table 4-4 marks HR 1000..1003 as write registers.  They must never
        # appear in a polling read plan.
        self.assertEqual(covered & {1000, 1001, 1002, 1003}, set())
        ordered = sorted(READ_BLOCKS)
        for block_start, block_count in zip(ordered, ordered[1:]):
            self.assertLessEqual(
                block_start[0] + block_start[1],
                block_count[0],
                "READ_BLOCKS must not overlap",
            )
        for register in Register:
            for offset in range(register.word_count):
                self.assertIn(
                    register.address + offset,
                    covered,
                    f"{register.name} is not covered by READ_BLOCKS",
                )

    def test_only_the_sensor_platform_is_loaded(self):
        import custom_components.burnham_alta as integration

        self.assertEqual(integration.PLATFORMS, [Platform.SENSOR])

    def test_manifest_and_translations(self):
        integration_dir = REPO_ROOT / "custom_components" / "burnham_alta"
        manifest = json.loads(
            (integration_dir / "manifest.json").read_text(encoding="utf-8")
        )
        strings = json.loads(
            (integration_dir / "strings.json").read_text(encoding="utf-8")
        )
        translations = json.loads(
            (integration_dir / "translations" / "en.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertTrue(manifest["config_flow"])
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(
            manifest["requirements"], ["pymodbus==3.13.1", "pyserial>=3.5"]
        )
        self.assertEqual(strings, translations)
        options = strings["config"]["options"]
        init = options["step"]["init"]
        self.assertEqual(init["data"][CONF_SCAN_INTERVAL], "Polling interval")
        self.assertEqual(
            set(init["data"]),
            {
                CONF_SCAN_INTERVAL,
                CONF_CH1_NAME,
                CONF_CH2_NAME,
                CONF_DHW_NAME,
            },
        )
        for key in (CONF_SCAN_INTERVAL, CONF_CH1_NAME, CONF_CH2_NAME, CONF_DHW_NAME):
            self.assertIn(key, init["data_description"])
        self.assertIn("invalid_scan_interval", options["error"])
        self.assertIn("invalid_consumer_name", options["error"])
        self.assertEqual(
            set(strings["entity"]["sensor"]), EXPECTED_SENSOR_KEYS
        )


if __name__ == "__main__":
    unittest.main()

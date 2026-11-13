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
        self.response = FakeResponse(CAPTURE_RAW)
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
        assert self.response is not None
        return self.response

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


_ha_const = _module(
    "homeassistant.const",
    Platform=Platform,
    UnitOfTemperature=UnitOfTemperature,
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
    TEMPERATURE = "temperature"


class SensorStateClass(str, Enum):
    MEASUREMENT = "measurement"


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
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_PARITY,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL_PORT,
    CONF_STOPBITS,
    CONNECTION_SERIAL,
    CONNECTION_TCP,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
)
from custom_components.burnham_alta.coordinator import BurnhamAltaCoordinator  # noqa: E402
from custom_components.burnham_alta.decoder import (  # noqa: E402
    Register,
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

SNAPSHOT = decode_registers(13, CAPTURE_RAW)
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

    async def test_one_read_of_hr_13_through_20(self):
        fake = FakeTcpClient()
        subject = AltaModbusClient(CONFIG_TCP, client=fake)

        snapshot = await subject.async_read_snapshot()

        self.assertEqual(fake.connect_calls, 1)
        self.assertEqual(
            fake.read_calls,
            [{"address": 13, "count": 8, "device_id": 1}],
        )
        self.assertEqual(
            snapshot.readings[Register.SUPPLY_TEMPERATURE].value, 121
        )
        self.assertEqual(snapshot.readings[Register.RETURN_TEMPERATURE].value, 120)
        self.assertIsNone(snapshot.readings[Register.DHW_TEMPERATURE].value)
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
        fake.response = FakeResponse(error=True)
        subject = AltaModbusClient(CONFIG_TCP, client=fake)

        with self.assertRaises(AltaModbusError):
            await subject.async_read_snapshot()

    async def test_short_response_is_rejected(self):
        fake = FakeTcpClient()
        fake.response = FakeResponse(CAPTURE_RAW[:3])
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
        self.assertEqual(fake.close_calls, 1)


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
        default = next(
            key.default
            for key in result["data_schema"].schema
            if key == CONF_SCAN_INTERVAL
        )
        self.assertEqual(default, DEFAULT_SCAN_INTERVAL_SECONDS)

    async def test_form_shows_the_current_option(self):
        flow, _ = self._flow({CONF_SCAN_INTERVAL: 45})
        result = await flow.async_step_init()

        default = next(
            key.default
            for key in result["data_schema"].schema
            if key == CONF_SCAN_INTERVAL
        )
        self.assertEqual(default, 45)

    async def test_valid_submission_creates_options(self):
        flow, _ = self._flow()
        result = await flow.async_step_init({CONF_SCAN_INTERVAL: 120})

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"], {CONF_SCAN_INTERVAL: 120})

    async def test_submission_preserves_unrelated_options(self):
        flow, _ = self._flow({"future_option": "keep me"})
        result = await flow.async_step_init({CONF_SCAN_INTERVAL: 20})

        self.assertEqual(
            result["data"],
            {"future_option": "keep me", CONF_SCAN_INTERVAL: 20},
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

    async def test_missing_value_falls_back_to_default(self):
        flow, _ = self._flow()
        result = await flow.async_step_init({})

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            result["data"][CONF_SCAN_INTERVAL],
            DEFAULT_SCAN_INTERVAL_SECONDS,
        )


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

    async def test_first_refresh_reads_once(self):
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
        self.assertEqual(len(fake.read_calls), 1)

    async def test_options_set_the_polling_interval(self):
        coordinator, fake = await self._coordinator({CONF_SCAN_INTERVAL: 30})
        await coordinator.async_config_entry_first_refresh()

        self.assertEqual(coordinator.update_interval, timedelta(seconds=30))
        self.assertEqual(len(fake.read_calls), 1)

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
                self.assertEqual(len(fake.read_calls), 1)

    async def test_each_refresh_is_one_read(self):
        coordinator, fake = await self._coordinator()
        await coordinator.async_config_entry_first_refresh()
        await coordinator.async_refresh()
        self.assertEqual(len(fake.read_calls), 2)

    async def test_failure_is_config_entry_not_ready(self):
        coordinator, fake = await self._coordinator()
        fake.read_exception = OSError("down")

        with self.assertRaises(ConfigEntryNotReady):
            await coordinator.async_config_entry_first_refresh()

        self.assertFalse(coordinator.last_update_success)
        self.assertIsNotNone(coordinator.last_exception)


class SensorTests(unittest.IsolatedAsyncioTestCase):
    async def _coordinator(self):
        hass = FakeHass()
        entry = ConfigEntry(entry_id="entry-1", data=dict(CONFIG_TCP))
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
        self.assertEqual(
            set(entities),
            {
                "burner_control_state",
                "target_temperature",
                "supply_temperature",
                "return_temperature",
                "dhw_temperature",
                "stack_temperature",
            },
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

    async def test_coordinator_failure_is_unavailable(self):
        coordinator, _ = await self._coordinator()
        coordinator.last_update_success = False
        entity = self._entities(coordinator)["supply_temperature"]
        self.assertFalse(entity.available)

    async def test_setup_adds_six_entities(self):
        coordinator, entry = await self._coordinator()
        hass = FakeHass()
        hass.data[DOMAIN] = {entry.entry_id: coordinator}
        added: list[Any] = []

        await sensor_module.async_setup_entry(hass, entry, added.extend)

        self.assertEqual(len(added), 6)
        self.assertEqual(
            {entity.entity_description.key for entity in added},
            {
                "burner_control_state",
                "target_temperature",
                "supply_temperature",
                "return_temperature",
                "dhw_temperature",
                "stack_temperature",
            },
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
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(
            manifest["requirements"], ["pymodbus==3.13.1", "pyserial>=3.5"]
        )
        self.assertEqual(strings, translations)
        options = strings["config"]["options"]
        self.assertEqual(
            options["step"]["init"]["data"][CONF_SCAN_INTERVAL],
            "Polling interval",
        )
        self.assertIn(CONF_SCAN_INTERVAL, options["step"]["init"]["data_description"])
        self.assertIn("invalid_scan_interval", options["error"])
        self.assertEqual(
            set(strings["entity"]["sensor"]),
            {
                "burner_control_state",
                "target_temperature",
                "supply_temperature",
                "return_temperature",
                "dhw_temperature",
                "stack_temperature",
            },
        )


if __name__ == "__main__":
    unittest.main()

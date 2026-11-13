"""Config flow for the Burnham ALTA integration.

Two connection modes are offered:

* Modbus TCP (MBAP) to a protocol-translating serial gateway.
* Native Modbus RTU on a local serial port, fixed at 8-N-1.

Both modes perform a read-only polling cycle before the entry is created.
The flow never writes to the boiler.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

# Home Assistant 2026.10 uses probatio for config schemas and selectors.
# Probatio deliberately mirrors the voluptuous API, so the same schema code is
# used with whichever library the running Home Assistant provides.
try:
    import probatio as vol
except ImportError:  # Home Assistant releases before the probatio migration
    import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_BAUDRATE,
    CONF_CH1_NAME,
    CONF_CH2_NAME,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_ID,
    CONF_DHW_NAME,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL_PORT,
    CONNECTION_SERIAL,
    CONNECTION_TCP,
    DEFAULT_BAUDRATE,
    DEFAULT_DEVICE_ID,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_SERIAL_PORT,
    DEFAULT_TCP_PORT,
    DOMAIN,
    MAX_BAUDRATE,
    MAX_DEVICE_ID,
    MAX_SCAN_INTERVAL_SECONDS,
    MAX_TCP_PORT,
    MIN_BAUDRATE,
    MIN_DEVICE_ID,
    MIN_SCAN_INTERVAL_SECONDS,
    MIN_TCP_PORT,
    consumer_name,
    scan_interval_seconds,
    validate_consumer_name,
    validate_scan_interval,
)
from .modbus import (
    AltaConnectionError,
    AltaModbusError,
    AltaResponseError,
    entry_title_for,
    normalize_config,
    unique_id_for,
    validate_connection,
)

__all__ = ["BurnhamAltaConfigFlow", "BurnhamAltaOptionsFlow"]

_LOGGER = logging.getLogger(__name__)


def _default(user_input: Mapping[str, Any] | None, key: str, fallback: Any) -> Any:
    if user_input is not None and key in user_input:
        return user_input[key]
    return fallback


def _tcp_schema(user_input: Mapping[str, Any] | None) -> Any:
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST,
                default=_default(user_input, CONF_HOST, ""),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Required(
                CONF_PORT,
                default=_default(user_input, CONF_PORT, DEFAULT_TCP_PORT),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_TCP_PORT,
                    max=MAX_TCP_PORT,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_ID,
                default=_default(user_input, CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_DEVICE_ID,
                    max=MAX_DEVICE_ID,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _serial_schema(user_input: Mapping[str, Any] | None) -> Any:
    return vol.Schema(
        {
            vol.Required(
                CONF_SERIAL_PORT,
                default=_default(
                    user_input, CONF_SERIAL_PORT, DEFAULT_SERIAL_PORT
                ),
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Required(
                CONF_BAUDRATE,
                default=_default(user_input, CONF_BAUDRATE, DEFAULT_BAUDRATE),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_BAUDRATE,
                    max=MAX_BAUDRATE,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="bit/s",
                )
            ),
            vol.Required(
                CONF_DEVICE_ID,
                default=_default(user_input, CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_DEVICE_ID,
                    max=MAX_DEVICE_ID,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


_CONSUMER_NAME_KEYS = (CONF_CH1_NAME, CONF_CH2_NAME, CONF_DHW_NAME)


def _options_schema(
    options: Mapping[str, Any],
    user_input: Mapping[str, Any] | None = None,
) -> Any:
    """Build the options form, showing safe current values."""
    current = scan_interval_seconds(
        user_input if user_input is not None else options
    )
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_SCAN_INTERVAL,
            default=current,
        ): NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL_SECONDS,
                max=MAX_SCAN_INTERVAL_SECONDS,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
    }
    for key in _CONSUMER_NAME_KEYS:
        source = (
            user_input
            if user_input is not None and key in user_input
            else options
        )
        schema[
            vol.Required(key, default=consumer_name(source, key))
        ] = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
    return vol.Schema(schema)


class BurnhamAltaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Burnham ALTA boiler."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> BurnhamAltaOptionsFlow:
        """Return the options flow for the Configure dialog."""
        return BurnhamAltaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Choose between a TCP gateway and a direct serial connection."""
        return self.async_show_menu(
            step_id="user",
            menu_options=[CONNECTION_TCP, CONNECTION_SERIAL],
        )

    async def async_step_tcp(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Configure a Modbus TCP gateway."""
        return await self._async_validate_and_create(
            user_input,
            connection_type=CONNECTION_TCP,
            step_id=CONNECTION_TCP,
            schema_builder=_tcp_schema,
        )

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Configure direct serial RTU."""
        return await self._async_validate_and_create(
            user_input,
            connection_type=CONNECTION_SERIAL,
            step_id=CONNECTION_SERIAL,
            schema_builder=_serial_schema,
        )

    async def _async_validate_and_create(
        self,
        user_input: dict[str, Any] | None,
        *,
        connection_type: str,
        step_id: str,
        schema_builder: Any,
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                data = normalize_config(
                    {**user_input, CONF_CONNECTION_TYPE: connection_type}
                )
            except (TypeError, ValueError) as err:
                _LOGGER.debug("Invalid Burnham ALTA configuration: %s", err)
                errors["base"] = "invalid_config"
            else:
                await self.async_set_unique_id(unique_id_for(data))
                self._abort_if_unique_id_configured()

                try:
                    await validate_connection(data)
                except (AltaConnectionError, OSError, TimeoutError) as err:
                    _LOGGER.debug("Cannot connect to Burnham ALTA: %s", err)
                    errors["base"] = "cannot_connect"
                except AltaModbusError as err:
                    _LOGGER.debug("Burnham ALTA returned a Modbus error: %s", err)
                    errors["base"] = "modbus_error"
                except AltaResponseError as err:
                    _LOGGER.debug("Invalid response from Burnham ALTA: %s", err)
                    errors["base"] = "invalid_response"
                except Exception:
                    _LOGGER.exception("Unexpected error validating Burnham ALTA")
                    errors["base"] = "unknown"
                else:
                    return self.async_create_entry(
                        title=entry_title_for(data),
                        data=data,
                    )

        return self.async_show_form(
            step_id=step_id,
            data_schema=schema_builder(user_input),
            errors=errors,
        )


class BurnhamAltaOptionsFlow(OptionsFlowWithReload):
    """Configure the polling interval and the demand-sensor labels.

    ``OptionsFlowWithReload`` makes Home Assistant reload the entry after the
    options change, so the coordinator picks up the new interval without an
    integration-specific update listener.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the polling interval and consumer labels."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                seconds = validate_scan_interval(
                    user_input.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS
                    )
                )
            except ValueError:
                errors[CONF_SCAN_INTERVAL] = "invalid_scan_interval"

            labels: dict[str, str] = {}
            for key in _CONSUMER_NAME_KEYS:
                try:
                    labels[key] = validate_consumer_name(
                        key, user_input.get(key, "")
                    )
                except ValueError:
                    errors[key] = "invalid_consumer_name"

            if not errors:
                return self.async_create_entry(
                    data={
                        **self.config_entry.options,
                        CONF_SCAN_INTERVAL: seconds,
                        **labels,
                    }
                )

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.config_entry.options, user_input),
            errors=errors,
        )

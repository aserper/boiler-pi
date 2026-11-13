"""Read-only Modbus access for the Burnham ALTA integration.

There are intentionally no write helpers in this module.  The boiler accepts
unauthenticated Modbus writes, so the integration only ever calls
``read_holding_registers``.  Each polling cycle reads the documented blocks in
``const.READ_BLOCKS`` with function 03; there is no other function code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient

from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_PARITY,
    CONF_PORT,
    CONF_SERIAL_PORT,
    CONF_STOPBITS,
    CONNECTION_SERIAL,
    CONNECTION_TCP,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_DEVICE_ID,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
    DEFAULT_TCP_PORT,
    DOMAIN,
    MAX_BAUDRATE,
    MAX_DEVICE_ID,
    MAX_TCP_PORT,
    MIN_BAUDRATE,
    MIN_DEVICE_ID,
    MIN_TCP_PORT,
    MODBUS_RETRIES,
    MODBUS_TIMEOUT,
    NAME,
    READ_BLOCKS,
)
from .decoder import Snapshot, decode_blocks

__all__ = [
    "AltaConnectionError",
    "AltaError",
    "AltaModbusClient",
    "AltaModbusError",
    "AltaResponseError",
    "create_client",
    "entry_title_for",
    "normalize_config",
    "unique_id_for",
    "validate_connection",
]

_LOGGER = logging.getLogger(__name__)


class AltaError(Exception):
    """Base class for errors raised by this integration."""


class AltaConnectionError(AltaError):
    """The gateway or serial port could not be reached or read."""


class AltaModbusError(AltaError):
    """The Modbus device returned a protocol-level error response."""


class AltaResponseError(AltaError):
    """The response did not contain the expected register block."""


def _int_value(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"{key} must be an integer") from err


def _validate_range(value: int, key: str, minimum: int, maximum: int) -> int:
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def normalize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a config-entry data mapping.

    The same function is used by the config flow and by the runtime client so
    a config entry that was edited or imported cannot bypass range checks.
    Integer values are coerced here; nothing is ever written to the device.
    """
    connection_type = str(config.get(CONF_CONNECTION_TYPE, "")).strip().lower()

    if connection_type == CONNECTION_TCP:
        host = str(config.get(CONF_HOST, "")).strip()
        if not host:
            raise ValueError("host is required for a TCP gateway")
        port = _validate_range(
            _int_value(config, CONF_PORT, DEFAULT_TCP_PORT),
            CONF_PORT,
            MIN_TCP_PORT,
            MAX_TCP_PORT,
        )
        device_id = _validate_range(
            _int_value(config, CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
            CONF_DEVICE_ID,
            MIN_DEVICE_ID,
            MAX_DEVICE_ID,
        )
        return {
            CONF_CONNECTION_TYPE: CONNECTION_TCP,
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_DEVICE_ID: device_id,
        }

    if connection_type == CONNECTION_SERIAL:
        serial_port = str(config.get(CONF_SERIAL_PORT, "")).strip()
        if not serial_port:
            raise ValueError("serial_port is required for direct serial RTU")
        baudrate = _validate_range(
            _int_value(config, CONF_BAUDRATE, DEFAULT_BAUDRATE),
            CONF_BAUDRATE,
            MIN_BAUDRATE,
            MAX_BAUDRATE,
        )
        device_id = _validate_range(
            _int_value(config, CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
            CONF_DEVICE_ID,
            MIN_DEVICE_ID,
            MAX_DEVICE_ID,
        )
        # 8-N-1 worked in the field capture and the manufacturer does not state
        # a stop-bit count, so framing is fixed rather than user-selectable.
        return {
            CONF_CONNECTION_TYPE: CONNECTION_SERIAL,
            CONF_SERIAL_PORT: serial_port,
            CONF_BAUDRATE: baudrate,
            CONF_BYTESIZE: DEFAULT_BYTESIZE,
            CONF_PARITY: DEFAULT_PARITY,
            CONF_STOPBITS: DEFAULT_STOPBITS,
            CONF_DEVICE_ID: device_id,
        }

    raise ValueError(
        f"unsupported connection_type {connection_type!r}; "
        f"expected {CONNECTION_TCP!r} or {CONNECTION_SERIAL!r}"
    )


def unique_id_for(config: Mapping[str, Any]) -> str:
    """Return a stable config-entry unique ID for a normalized connection."""
    normalized = normalize_config(config)
    device_id = normalized[CONF_DEVICE_ID]

    if normalized[CONF_CONNECTION_TYPE] == CONNECTION_TCP:
        return (
            f"{DOMAIN}:{CONNECTION_TCP}:"
            f"{normalized[CONF_HOST].lower()}:"
            f"{normalized[CONF_PORT]}:{device_id}"
        )

    return (
        f"{DOMAIN}:{CONNECTION_SERIAL}:"
        f"{normalized[CONF_SERIAL_PORT]}:"
        f"{normalized[CONF_BAUDRATE]}:"
        f"{normalized[CONF_BYTESIZE]}{normalized[CONF_PARITY]}"
        f"{normalized[CONF_STOPBITS]}:{device_id}"
    )


def entry_title_for(config: Mapping[str, Any]) -> str:
    """Return a short, human-readable title for a config entry."""
    normalized = normalize_config(config)
    device_id = normalized[CONF_DEVICE_ID]

    if normalized[CONF_CONNECTION_TYPE] == CONNECTION_TCP:
        return (
            f"{NAME} {normalized[CONF_HOST]}:"
            f"{normalized[CONF_PORT]} (unit {device_id})"
        )

    port_name = Path(normalized[CONF_SERIAL_PORT]).name
    return f"{NAME} {port_name or normalized[CONF_SERIAL_PORT]} (unit {device_id})"


def create_client(
    config: Mapping[str, Any],
) -> AsyncModbusTcpClient | AsyncModbusSerialClient:
    """Create the appropriate async pymodbus client for *config*.

    ``AsyncModbusTcpClient`` uses Modbus TCP (MBAP) framing, which is what a
    protocol-translating serial gateway needs.  It is not RTU-over-TCP.
    ``AsyncModbusSerialClient`` uses native RTU framing on a local serial port.

    Pymodbus 3.13.1 creates its transport with ``asyncio.get_running_loop()``,
    so this factory must be called from async code.  The integration only calls
    it from :meth:`AltaModbusClient.async_read_snapshot`.
    """
    normalized = normalize_config(config)
    name = f"{DOMAIN}-{normalized[CONF_CONNECTION_TYPE]}"

    if normalized[CONF_CONNECTION_TYPE] == CONNECTION_TCP:
        return AsyncModbusTcpClient(
            host=normalized[CONF_HOST],
            port=normalized[CONF_PORT],
            timeout=MODBUS_TIMEOUT,
            retries=MODBUS_RETRIES,
            name=name,
        )

    return AsyncModbusSerialClient(
        port=normalized[CONF_SERIAL_PORT],
        baudrate=normalized[CONF_BAUDRATE],
        bytesize=normalized[CONF_BYTESIZE],
        parity=normalized[CONF_PARITY],
        stopbits=normalized[CONF_STOPBITS],
        timeout=MODBUS_TIMEOUT,
        retries=MODBUS_RETRIES,
        name=name,
    )


class AltaModbusClient:
    """A serialized, lazily connected, read-only Modbus client.

    The coordinator calls :meth:`async_read_snapshot` once per polling cycle.
    Every request is a function-03 holding-register read.  If the transport or
    a frame fails, the client is closed so the next cycle reconnects cleanly.
    A lock prevents overlapping polling cycles if a refresh is requested while
    one is still in flight.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        client: Any | None = None,
    ) -> None:
        self._config = normalize_config(config)
        self._client = client
        self._lock = asyncio.Lock()

    @property
    def config(self) -> Mapping[str, Any]:
        """The normalized connection settings."""
        return self._config

    @property
    def client(self) -> Any:
        """The underlying pymodbus client, or ``None`` before first use."""
        return self._client

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                self._client = create_client(self._config)
            except Exception as err:
                raise AltaConnectionError(
                    f"Could not create the Modbus client: {err}"
                ) from err
        return self._client

    def close(self) -> None:
        """Close the transport.  Safe to call more than once."""
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.close()
        except Exception:  # pragma: no cover - close must never mask a read error
            _LOGGER.debug("Error while closing Modbus client", exc_info=True)

    async def async_read_snapshot(self) -> Snapshot:
        """Perform the configured function-03 reads for one polling cycle.

        Blocks are read sequentially.  A Modbus exception response fails only
        that block; the remaining blocks are still attempted.  A transport
        failure or malformed frame closes the client and stops the cycle,
        because continuing would send requests into an unknown stream state.
        The next cycle reconnects cleanly.

        If at least one block succeeded, a partial snapshot is returned rather
        than discarding good readings; missing readings make only their own
        entities unavailable.  If no block succeeded, the first error is raised
        as :class:`AltaConnectionError`, :class:`AltaModbusError` or
        :class:`AltaResponseError`.
        """
        async with self._lock:
            client = self._ensure_client()

            if not client.connected:
                try:
                    connected = await client.connect()
                except asyncio.CancelledError:
                    self.close()
                    raise
                except Exception as err:
                    self.close()
                    raise AltaConnectionError(
                        f"Could not connect to the Modbus device: {err}"
                    ) from err
                if not connected:
                    self.close()
                    raise AltaConnectionError(
                        "Could not connect to the Modbus device"
                    )

            blocks: list[tuple[int, tuple[int, ...]]] = []
            block_errors: dict[tuple[int, int], str] = {}
            first_error: AltaError | None = None
            device_id = self._config[CONF_DEVICE_ID]

            for start, count in READ_BLOCKS:
                key = (start, count)
                try:
                    response = await client.read_holding_registers(
                        start,
                        count=count,
                        device_id=device_id,
                    )
                except asyncio.CancelledError:
                    self.close()
                    raise
                except Exception as err:
                    # The stream may now be desynchronised.  Stop this cycle
                    # and close so the next poll reconnects from scratch.
                    self.close()
                    error = AltaConnectionError(
                        f"Modbus read of HR {start} ({count} registers) "
                        f"failed: {err}"
                    )
                    block_errors[key] = str(error)
                    if first_error is None:
                        first_error = error
                    break

                is_error = getattr(response, "isError", None)
                if callable(is_error) and is_error():
                    # The device answered, so the transport is usable.  Mark
                    # only this block failed and keep reading the others.
                    error = AltaModbusError(
                        f"Modbus device returned an error response for "
                        f"HR {start} ({count} registers): {response}"
                    )
                    block_errors[key] = str(error)
                    if first_error is None:
                        first_error = error
                    continue

                registers = getattr(response, "registers", None)
                if registers is None:
                    self.close()
                    error = AltaResponseError(
                        f"Modbus response for HR {start} did not contain a "
                        "register block"
                    )
                    block_errors[key] = str(error)
                    if first_error is None:
                        first_error = error
                    break

                values = tuple(registers)
                if len(values) != count:
                    self.close()
                    error = AltaResponseError(
                        f"expected {count} registers from address {start}, "
                        f"got {len(values)}"
                    )
                    block_errors[key] = str(error)
                    if first_error is None:
                        first_error = error
                    break

                blocks.append((start, values))

            if not blocks:
                if first_error is not None:
                    raise first_error
                raise AltaResponseError(
                    "Modbus device returned no readable register blocks"
                )

            if block_errors:
                _LOGGER.debug(
                    "Burnham ALTA returned a partial snapshot; failed blocks: %s",
                    ", ".join(
                        f"{start}+{count}" for start, count in block_errors
                    ),
                )

            try:
                return decode_blocks(blocks, block_errors=block_errors)
            except (TypeError, ValueError) as err:
                self.close()
                raise AltaResponseError(
                    f"could not decode the register blocks: {err}"
                ) from err


async def validate_connection(config: Mapping[str, Any]) -> Snapshot:
    """Open a temporary connection and perform one read-only polling cycle.

    Used by the config flow so an entry is only created for a device that
    answers at least one documented block.  A device whose diagnostics block
    fails but whose primary block answers still validates; the temporary
    client is always closed.
    """
    client = AltaModbusClient(config)
    try:
        return await client.async_read_snapshot()
    finally:
        client.close()

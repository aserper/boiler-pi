"""Constants for the Burnham ALTA integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

DOMAIN = "burnham_alta"
NAME = "Burnham ALTA"
MANUFACTURER = "Burnham"
MODEL = "ALTA"

# One coordinated read covers HR 13..20, the range observed in the field.
READ_START_ADDRESS = 13
READ_REGISTER_COUNT = 8

# Polling is configurable from the integration's Configure dialog.  The
# default matches the original fixed interval; the bounds keep a typo or a
# hand-edited storage value from hammering the boiler or stopping updates
# entirely.
DEFAULT_SCAN_INTERVAL_SECONDS = 10
MIN_SCAN_INTERVAL_SECONDS = 5
MAX_SCAN_INTERVAL_SECONDS = 3600
DEFAULT_SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS)

# Connection types exposed by the config flow.
CONNECTION_TCP = "tcp"
CONNECTION_SERIAL = "serial"

# Configuration keys.  These are integration-local strings so the config entry
# stays self-describing and does not depend on private Home Assistant constants.
CONF_CONNECTION_TYPE = "connection_type"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SERIAL_PORT = "serial_port"
CONF_BAUDRATE = "baudrate"
CONF_BYTESIZE = "bytesize"
CONF_PARITY = "parity"
CONF_STOPBITS = "stopbits"
CONF_DEVICE_ID = "device_id"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_TCP_PORT = 502
DEFAULT_DEVICE_ID = 1
DEFAULT_BAUDRATE = 38400
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"

# The field capture worked at 38400 8-N-1.  These are fixed rather than exposed
# so a user cannot accidentally configure a framing the boiler does not use.
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_STOPBITS = 1

# A single Modbus attempt times out after 3 seconds.  The coordinator starts
# the next cycle only after the current one finishes, so a slow device or a
# retry cannot overlap polls even at the shortest supported interval.
MODBUS_TIMEOUT = 3.0
MODBUS_RETRIES = 1

MIN_DEVICE_ID = 1
MAX_DEVICE_ID = 247
MIN_TCP_PORT = 1
MAX_TCP_PORT = 65535
MIN_BAUDRATE = 1200
MAX_BAUDRATE = 115200


def scan_interval_seconds(options: Mapping[str, Any] | None) -> int:
    """Return a safe polling interval in seconds from config-entry options.

    The options flow rejects out-of-range values, but a config entry can also be
    hand-edited or imported.  Runtime therefore re-validates the stored value:
    non-integer values fall back to the default (10 seconds), and numeric
    values are clamped to the supported range so polling can never be faster
    than the minimum.  This function never raises.
    """
    value: Any = DEFAULT_SCAN_INTERVAL_SECONDS
    if options is not None:
        value = options.get(CONF_SCAN_INTERVAL, value)

    if isinstance(value, bool):
        return DEFAULT_SCAN_INTERVAL_SECONDS

    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_SCAN_INTERVAL_SECONDS

    return max(MIN_SCAN_INTERVAL_SECONDS, min(MAX_SCAN_INTERVAL_SECONDS, seconds))


def validate_scan_interval(value: Any) -> int:
    """Validate an interval entered in the options flow.

    Raises ``ValueError`` for anything that is not a whole number of seconds in
    the supported range.  Runtime uses :func:`scan_interval_seconds` instead so
    a bad stored value cannot make setup fail.
    """
    if isinstance(value, bool):
        raise ValueError("scan_interval must be an integer")
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise ValueError("scan_interval must be an integer") from err
    if not MIN_SCAN_INTERVAL_SECONDS <= seconds <= MAX_SCAN_INTERVAL_SECONDS:
        raise ValueError(
            f"scan_interval must be between {MIN_SCAN_INTERVAL_SECONDS} and "
            f"{MAX_SCAN_INTERVAL_SECONDS} seconds"
        )
    return seconds

"""Constants for the Burnham ALTA integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

DOMAIN = "burnham_alta"
NAME = "Burnham ALTA"
MANUFACTURER = "Burnham"
MODEL = "ALTA"

# Read-only function-03 blocks, as (start address, register count).  The
# manual's HR number is the literal zero-based wire address on the observed
# boiler.  The first block preserves the original HR 13..20 read; the other
# blocks add the documented diagnostics.  Contiguous documented ranges are
# grouped; the only undocumented addresses read are HR 15 and 19, which sit
# inside the original HR 13..20 block and are not decoded.  A poll therefore
# makes len(READ_BLOCKS) short FC03 transactions rather than one.
READ_BLOCKS: tuple[tuple[int, int], ...] = (
    (13, 8),   # burner state 13, setpoint 14, supply/return/DHW/stack 16..20
    (3, 1),    # DHW heat demand
    (9, 2),    # CH1 and CH2 heat demand
    (24, 6),   # burner run time 24-25, cycles 26-27, hold 28, lockout 29
    (100, 2),  # demand source, flame signal
    (103, 2),  # fan speed demanded and feedback
    (106, 3),  # pump A/B/C status
    (686, 6),  # pump A/B/C cycle counts
)

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
CONF_CH1_NAME = "ch1_name"
CONF_CH2_NAME = "ch2_name"
CONF_DHW_NAME = "dhw_name"

# The heat-demand registers are raw 0/1 values, but which zone or load each
# one serves depends on the installation.  The Configure dialog therefore
# stores consumer labels; these generic defaults apply until the installer
# maps CH1, CH2 and DHW to actual floors or loads.
DEFAULT_CH1_NAME = "Central heat 1"
DEFAULT_CH2_NAME = "Central heat 2"
DEFAULT_DHW_NAME = "Domestic hot water"

CONSUMER_NAME_DEFAULTS: dict[str, str] = {
    CONF_CH1_NAME: DEFAULT_CH1_NAME,
    CONF_CH2_NAME: DEFAULT_CH2_NAME,
    CONF_DHW_NAME: DEFAULT_DHW_NAME,
}

# A label is a display name, not a description.  The bound keeps a hand-edited
# options file or a pasted paragraph from producing an unusable entity name.
MAX_CONSUMER_NAME_LENGTH = 80

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


def consumer_name(options: Mapping[str, Any] | None, key: str) -> str:
    """Return the safe display label for one heat-demand consumer.

    The options flow validates input, but a config entry can also be
    hand-edited or imported.  Runtime therefore never raises: a non-text,
    blank, or missing stored value falls back to the generic default, and an
    over-long value is truncated to the supported maximum.  Surrounding
    whitespace is always removed so the entity name stays clean.
    """
    default = CONSUMER_NAME_DEFAULTS[key]
    value: Any = None if options is None else options.get(key)
    if not isinstance(value, str):
        return default
    label = value.strip()
    if not label:
        return default
    return label[:MAX_CONSUMER_NAME_LENGTH].rstrip()


def validate_consumer_name(key: str, value: Any) -> str:
    """Validate a consumer label entered in the options flow.

    Raises ``ValueError`` for anything that is not text or is longer than
    :data:`MAX_CONSUMER_NAME_LENGTH`.  A blank label is accepted and returns
    the generic default, matching how a stored blank value is interpreted at
    runtime.
    """
    default = CONSUMER_NAME_DEFAULTS[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    label = value.strip()
    if len(label) > MAX_CONSUMER_NAME_LENGTH:
        raise ValueError(
            f"{key} must be at most {MAX_CONSUMER_NAME_LENGTH} characters"
        )
    return label or default

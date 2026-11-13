"""Read-only decoder for ALTA boiler Modbus RTU function-03 responses.

Scope
-----
Pure Python, standard library only.  This module contains no serial or network
I/O, builds no requests, and knows nothing about write functions.  It turns an
already-received response frame into numbers, and nothing else.

Every 16-bit register value is kept exactly as it appeared on the wire
(``Reading.raw``); decoding only adds an interpretation beside it.  Registers
that are not modelled are still retained by ``Snapshot.raw_at``.

Address convention
------------------
On the observed unit, a read of PDU address 13 (count 8) returned eight values
that line up one-for-one with the manual's HR 13..20.  The manual HR number is
therefore treated as the literal zero-based PDU address (``address == HR``),
not ``HR - 1``.  That is an observation on one boiler and one firmware; the
mapping lives only in ``Register`` so it is cheap to revise.

Verified points only
--------------------
``Register`` models the six points that the capture and manual table 4-4 agree
on: burner control state, active setpoint, supply, return, DHW and stack
temperature.  Burner state is reported as a plain integer: table 4-4 does
enumerate 0..14, but the numbering is not validated here (only state 1 was
observed, and the manufacturer's ProtoAir point list disagrees with the manual
for some enums), so no state labels are attached.

``0x7FFF`` is reported as unavailable for temperatures, as ``None``.  This is
*provisional*: the capture showed it for the two sensors that appeared to be
absent, but its meaning is not confirmed by the manufacturer.  It is never
special-cased for the burner state, which is only validated as a 16-bit value.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping

__all__ = [
    "AltaDecodeError",
    "CrcMismatchError",
    "ModbusExceptionResponse",
    "Register",
    "Reading",
    "Response",
    "Snapshot",
    "UNAVAILABLE_RAW",
    "crc16_modbus",
    "decode_burner_control_state",
    "decode_response",
    "decode_temperature",
    "parse_response",
    "to_int16",
]

FUNCTION_READ_HOLDING_REGISTERS = 0x03
MAX_REGISTERS_PER_READ = 125
UNAVAILABLE_RAW = 0x7FFF
"""Raw value observed for unconnected sensors; meaning is not yet confirmed."""

_CRC_POLYNOMIAL = 0xA001


class Register(IntEnum):
    """Modelled holding registers, named as the manual names them (HR n).

    ``int(register)`` is the zero-based PDU address to read, because on the
    observed boiler the manual HR number is the literal wire address.
    """

    BURNER_CONTROL_STATE = 13
    ACTIVE_SETPOINT = 14
    SUPPLY_TEMPERATURE = 16
    RETURN_TEMPERATURE = 17
    DHW_TEMPERATURE = 18
    STACK_TEMPERATURE = 20

    @property
    def address(self) -> int:
        """Zero-based PDU address for this register."""
        return int(self)


_TEMPERATURE_REGISTERS = frozenset(
    {
        Register.ACTIVE_SETPOINT,
        Register.SUPPLY_TEMPERATURE,
        Register.RETURN_TEMPERATURE,
        Register.DHW_TEMPERATURE,
        Register.STACK_TEMPERATURE,
    }
)


class AltaDecodeError(ValueError):
    """A frame or register value that cannot be interpreted safely."""


class CrcMismatchError(AltaDecodeError):
    """The frame's CRC-16/Modbus does not match its contents."""

    def __init__(self, expected: int, received: int) -> None:
        self.expected = expected
        self.received = received
        super().__init__(
            f"CRC mismatch: computed {expected:#06x}, frame carries {received:#06x}"
        )


class ModbusExceptionResponse(AltaDecodeError):
    """A slave answered with a Modbus exception instead of register data."""

    def __init__(self, slave: int, function_code: int, exception_code: int) -> None:
        self.slave = slave
        self.function_code = function_code
        self.exception_code = exception_code
        super().__init__(
            f"slave {slave} returned Modbus exception {exception_code} "
            f"for function {function_code:#04x}"
        )


@dataclass(frozen=True)
class Response:
    """A parsed function-03 response: raw register values in wire order."""

    slave: int
    registers: tuple[int, ...]

    @property
    def byte_count(self) -> int:
        return 2 * len(self.registers)


@dataclass(frozen=True)
class Reading:
    """One modelled register: the raw wire value plus its interpretation."""

    register: Register
    raw: int
    value: int | None
    """Decoded value, or ``None`` when the register reports unavailable."""

    @property
    def address(self) -> int:
        return self.register.address

    @property
    def available(self) -> bool:
        return self.value is not None

    @property
    def unit(self) -> str | None:
        """``"°F"`` for temperature registers, ``None`` for the state."""
        return "°F" if self.register in _TEMPERATURE_REGISTERS else None


@dataclass(frozen=True)
class Snapshot:
    """All raw registers from a response plus the modelled readings it covers."""

    slave: int
    start_address: int
    registers: tuple[int, ...]
    readings: Mapping[Register, Reading]

    def raw_at(self, address: int) -> int:
        """Raw 16-bit value at *address*, including registers not modelled."""
        offset = int(address) - self.start_address
        if not 0 <= offset < len(self.registers):
            last = self.start_address + len(self.registers) - 1
            raise KeyError(
                f"address {address} is not covered by this response "
                f"({self.start_address}..{last})"
            )
        return self.registers[offset]


def _as_bytes(frame: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(frame, (bytes, bytearray, memoryview)):
        raise TypeError(f"frame must be bytes-like, not {type(frame).__name__}")
    return bytes(frame)


def _validate_raw(raw: int) -> int:
    if not isinstance(raw, int):
        raise TypeError(f"raw register value must be an int, not {type(raw).__name__}")
    if not 0 <= raw <= 0xFFFF:
        raise ValueError(f"raw value {raw} is outside the 16-bit range 0..0xFFFF")
    return raw


def crc16_modbus(data: bytes | bytearray | memoryview) -> int:
    """Return the CRC-16/Modbus of *data* as an integer in ``0..0xFFFF``."""
    crc = 0xFFFF
    for byte in _as_bytes(data):
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ _CRC_POLYNOMIAL
            else:
                crc >>= 1
    return crc


def _check_crc(body: bytes, crc_bytes: bytes) -> None:
    received = crc_bytes[0] | (crc_bytes[1] << 8)
    computed = crc16_modbus(body)
    if received != computed:
        raise CrcMismatchError(computed, received)


def to_int16(raw: int) -> int:
    """Interpret a 16-bit register value as a signed two's-complement integer."""
    raw = _validate_raw(raw)
    return raw - 0x10000 if raw & 0x8000 else raw


def parse_response(
    frame: bytes | bytearray | memoryview,
    *,
    expected_slave: int | None = None,
) -> Response:
    """Validate an RTU function-03 response and return its raw registers.

    The whole frame is checked: length against the declared byte count, byte
    count against the 16-bit register width, function code, and CRC.  Modbus
    exception responses raise ``ModbusExceptionResponse``; anything else that
    does not decode raises ``AltaDecodeError``.
    """
    data = _as_bytes(frame)
    if len(data) < 5:
        raise AltaDecodeError(
            f"frame is {len(data)} bytes; a function-03 response needs at least 5"
        )

    slave, function = data[0], data[1]
    if expected_slave is not None:
        if not 0 <= expected_slave <= 0xFF:
            raise ValueError(f"expected_slave {expected_slave} is not a byte")
        if slave != expected_slave:
            raise AltaDecodeError(
                f"response is from slave {slave}, expected {expected_slave}"
            )

    if function & 0x80:
        if len(data) != 5:
            raise AltaDecodeError(
                f"exception response must be 5 bytes, got {len(data)}"
            )
        _check_crc(data[:3], data[3:5])
        raise ModbusExceptionResponse(slave, function & 0x7F, data[2])

    if function != FUNCTION_READ_HOLDING_REGISTERS:
        raise AltaDecodeError(
            f"unsupported function code {function:#04x}; "
            f"only {FUNCTION_READ_HOLDING_REGISTERS:#04x} responses are decoded"
        )

    byte_count = data[2]
    if byte_count == 0:
        raise AltaDecodeError("byte count is 0; a response must carry a register")
    if byte_count % 2:
        raise AltaDecodeError(
            f"byte count {byte_count} is odd; holding registers are 16-bit"
        )
    if byte_count > 2 * MAX_REGISTERS_PER_READ:
        raise AltaDecodeError(
            f"byte count {byte_count} exceeds the {MAX_REGISTERS_PER_READ}-register "
            "maximum for function 03"
        )

    expected_length = 3 + byte_count + 2
    if len(data) != expected_length:
        raise AltaDecodeError(
            f"frame is {len(data)} bytes but byte count {byte_count} "
            f"implies {expected_length}"
        )

    _check_crc(data[:-2], data[-2:])
    registers = tuple(
        (data[3 + i] << 8) | data[4 + i] for i in range(0, byte_count, 2)
    )
    return Response(slave=slave, registers=registers)


def decode_temperature(raw: int) -> int | None:
    """Decode a temperature register to signed degrees Fahrenheit.

    Values are whole degrees Fahrenheit (the manual gives -40..320 °F, so the
    wire value must be signed 16-bit).  ``0x7FFF`` returns ``None``, meaning
    unavailable, on the provisional evidence of the capture.  Other values are
    returned as-is; this function does not range-check them against the
    manual's span, so callers can see out-of-range wire noise for what it is.
    """
    raw = _validate_raw(raw)
    if raw == UNAVAILABLE_RAW:
        return None
    return to_int16(raw)


def decode_burner_control_state(raw: int) -> int:
    """Return the burner control state exactly as reported.

    Manual table 4-4 enumerates states 0..14, but no labels are attached here:
    the numbering has not been validated against the boiler's display (and the
    manufacturer's ProtoAir list disagrees with the manual for some enums).
    Only state 1 has been observed.  Treat the number as provisional.
    """
    return _validate_raw(raw)


def _decode_value(register: Register, raw: int) -> int | None:
    if register in _TEMPERATURE_REGISTERS:
        return decode_temperature(raw)
    return decode_burner_control_state(raw)


def decode_response(
    frame: bytes | bytearray | memoryview,
    *,
    start_address: int = Register.BURNER_CONTROL_STATE,
    expected_slave: int | None = None,
) -> Snapshot:
    """Parse a function-03 response and decode the modelled points it covers.

    *start_address* is the address that was requested, because the response
    frame itself does not carry it; it defaults to 13, the start of this
    project's only capture, and must be supplied for any other read.  Unknown
    registers inside the range are retained via ``Snapshot.raw_at``.
    """
    start = int(start_address)
    if not 0 <= start <= 0xFFFF:
        raise ValueError(f"start_address {start} is not a 16-bit address")

    response = parse_response(frame, expected_slave=expected_slave)
    if start + len(response.registers) > 0x10000:
        raise AltaDecodeError(
            f"read of {len(response.registers)} registers from {start} "
            "runs past address 0xFFFF"
        )

    readings: dict[Register, Reading] = {}
    for register in Register:
        offset = register.address - start
        if 0 <= offset < len(response.registers):
            raw = response.registers[offset]
            readings[register] = Reading(
                register=register,
                raw=raw,
                value=_decode_value(register, raw),
            )

    return Snapshot(
        slave=response.slave,
        start_address=start,
        registers=response.registers,
        readings=readings,
    )

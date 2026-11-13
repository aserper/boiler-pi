"""Pure, read-only decoding for Burnham ALTA holding registers.

This module deliberately contains no I/O.  It takes the raw 16-bit register
values returned by one Modbus function-03 read and adds an interpretation
beside each value.  ``Reading.raw`` always retains exactly what came off the
wire.

Address convention
------------------
On the observed unit, reading PDU address 13 with a count of 8 returned values
that line up with the manual's HR 13..20.  The manual HR number is therefore
used as the literal zero-based PDU address on this boiler, not ``HR - 1``.
That is an observation on one boiler and one firmware, so the mapping lives
only in :class:`Register` and is cheap to revise.

Only the points supported by the capture in ``docs/field-notes.md`` and the
manual's table 4-4 are modelled.  Burner control state is returned as a plain
integer: state 1 was observed, but the enum labels have not been validated
against the boiler display, so none are attached here.

``0x7FFF`` is treated as unavailable for temperatures.  That meaning is
provisional: it appeared for the two sensors that were apparently absent, but
the manufacturer has not confirmed it.  It is never applied to the burner
state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping

__all__ = [
    "Register",
    "Reading",
    "Snapshot",
    "UNAVAILABLE_RAW",
    "decode_burner_control_state",
    "decode_registers",
    "decode_temperature",
    "to_int16",
]

UNAVAILABLE_RAW = 0x7FFF
"""Raw value observed for unconnected sensors; meaning is not yet confirmed."""

_MIN_RAW = 0x0000
_MAX_RAW = 0xFFFF


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


TEMPERATURE_REGISTERS = frozenset(
    {
        Register.ACTIVE_SETPOINT,
        Register.SUPPLY_TEMPERATURE,
        Register.RETURN_TEMPERATURE,
        Register.DHW_TEMPERATURE,
        Register.STACK_TEMPERATURE,
    }
)


@dataclass(frozen=True)
class Reading:
    """One modelled register: the raw wire value plus its interpretation."""

    register: Register
    raw: int
    value: int | None
    """Decoded value, or ``None`` when the register reports unavailable."""

    @property
    def address(self) -> int:
        """Zero-based PDU address of the register."""
        return self.register.address

    @property
    def available(self) -> bool:
        """Whether the register produced a usable value."""
        return self.value is not None

    @property
    def unit(self) -> str | None:
        """``"°F"`` for temperature registers, ``None`` for the state."""
        return "°F" if self.register in TEMPERATURE_REGISTERS else None


@dataclass(frozen=True)
class Snapshot:
    """All registers from one read plus the modelled readings they cover."""

    start_address: int
    registers: tuple[int, ...]
    readings: Mapping[Register, Reading]

    def raw_at(self, address: int) -> int:
        """Return the raw 16-bit value at *address*.

        Raises ``KeyError`` when the address is outside this read.
        """
        offset = int(address) - self.start_address
        if not 0 <= offset < len(self.registers):
            last = self.start_address + len(self.registers) - 1
            raise KeyError(
                f"address {address} is not covered by this read "
                f"({self.start_address}..{last})"
            )
        return self.registers[offset]


def _validate_raw(raw: int) -> int:
    if not isinstance(raw, int):
        raise TypeError(f"raw register value must be an int, not {type(raw).__name__}")
    if not _MIN_RAW <= raw <= _MAX_RAW:
        raise ValueError(f"raw value {raw} is outside the 16-bit range 0..0xFFFF")
    return raw


def to_int16(raw: int) -> int:
    """Interpret a 16-bit register value as a signed two's-complement integer."""
    raw = _validate_raw(raw)
    return raw - 0x10000 if raw & 0x8000 else raw


def decode_temperature(raw: int) -> int | None:
    """Decode a temperature register to signed whole degrees Fahrenheit.

    The manual gives a -40..320 °F span, so values are signed 16-bit.
    ``0x7FFF`` returns ``None`` (unavailable) on the provisional evidence of
    the capture.  Other values are returned as-is; this function does not
    range-check them against the manual's span, so callers can see out-of-range
    wire noise for what it is.
    """
    raw = _validate_raw(raw)
    if raw == UNAVAILABLE_RAW:
        return None
    return to_int16(raw)


def decode_burner_control_state(raw: int) -> int:
    """Return the burner control state exactly as reported.

    No labels are attached: only state 1 has been observed, and the
    manufacturer's ProtoAir point list disagrees with the manual for some
    enums.  Treat the number as provisional.
    """
    return _validate_raw(raw)


def _decode_value(register: Register, raw: int) -> int | None:
    if register in TEMPERATURE_REGISTERS:
        return decode_temperature(raw)
    return decode_burner_control_state(raw)


def decode_registers(
    start_address: int,
    registers: tuple[int, ...] | list[int],
) -> Snapshot:
    """Decode the registers from one function-03 read.

    *start_address* is the address that was requested, because a Modbus
    response does not carry it.  Registers that are not modelled are retained
    in :attr:`Snapshot.registers` and can be read with
    :meth:`Snapshot.raw_at`.
    """
    start = int(start_address)
    if not 0 <= start <= _MAX_RAW:
        raise ValueError(f"start_address {start} is not a 16-bit address")

    values = tuple(_validate_raw(raw) for raw in registers)
    if not values:
        raise ValueError("a read must contain at least one register")
    if start + len(values) > 0x10000:
        raise ValueError(
            f"read of {len(values)} registers from {start} runs past address 0xFFFF"
        )

    readings: dict[Register, Reading] = {}
    for register in Register:
        offset = register.address - start
        if 0 <= offset < len(values):
            raw = values[offset]
            readings[register] = Reading(
                register=register,
                raw=raw,
                value=_decode_value(register, raw),
            )

    return Snapshot(
        start_address=start,
        registers=values,
        readings=readings,
    )

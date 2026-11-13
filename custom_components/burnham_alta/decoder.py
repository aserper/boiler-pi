"""Pure, read-only decoding for Burnham ALTA holding registers.

This module deliberately contains no I/O.  It takes the raw 16-bit register
values returned by Modbus function-03 reads and adds an interpretation beside
each value.  ``Reading.raw`` always retains exactly what came off the wire.

Address convention
------------------
On the observed unit, reading PDU address 13 with a count of 8 returned values
that line up with the manual's HR 13..20.  The manual HR number is therefore
used as the literal zero-based PDU address on this boiler, not ``HR - 1``.
That is an observation on one boiler and one firmware, so the mapping lives
only in :class:`Register` and is cheap to revise.

Value interpretation
--------------------
Only points documented in the manufacturer's table 4-4 are modelled.  Several
are exposed as **raw codes** rather than translated labels:

* burner control state, hold code, lockout code and demand source are plain
  integers.  The manual and the manufacturer's ProtoAir point list disagree
  about some enum numbering, so attaching labels would pretend to a certainty
  the sources do not provide.
* demand and pump states are plain integers (the manual documents 0 = off and
  1 = on, but an unexpected value is passed through instead of being coerced).
* flame signal and fan speeds are plain integers with no range clamping.

``0x7FFF`` is treated as unavailable for temperature registers only.  That
meaning is provisional: it appeared for the two temperature sensors that were
apparently absent, but the manufacturer has not confirmed it.  It is not
special-cased for raw codes, flags, fan speeds or counters.

Word order for the 32-bit statistics
------------------------------------
Live read-only samples returned burner run time and cycle pairs as
``(0, 23)`` and ``(0, 284)``.  High-word-first makes those 23 hours and 284
cycles, which is plausible; low-word-first would make them 1.5 million hours
and 18 million cycles.  :func:`decode_u32` therefore combines the first
register as the high word.  This is strong evidence from one boiler, not a
manufacturer statement, so it remains **provisional**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable, Mapping, Sequence

__all__ = [
    "DEMAND_REGISTERS",
    "FLAG_REGISTERS",
    "PERCENT_REGISTERS",
    "PUMP_STATUS_REGISTERS",
    "RAW_CODE_REGISTERS",
    "RPM_REGISTERS",
    "Register",
    "RegisterBlock",
    "Reading",
    "Snapshot",
    "TEMPERATURE_REGISTERS",
    "U32_COUNTER_REGISTERS",
    "UNAVAILABLE_RAW",
    "decode_blocks",
    "decode_burner_control_state",
    "decode_registers",
    "decode_temperature",
    "decode_u32",
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

    For the 32-bit statistics the enum value is the address of the **high**
    word: HR 24 for HR 24-25, HR 26 for HR 26-27, and so on.  The low word is
    at ``address + 1``.
    """

    DHW_HEAT_DEMAND = 3
    CH1_HEAT_DEMAND = 9
    CH2_HEAT_DEMAND = 10

    BURNER_CONTROL_STATE = 13
    ACTIVE_SETPOINT = 14
    SUPPLY_TEMPERATURE = 16
    RETURN_TEMPERATURE = 17
    DHW_TEMPERATURE = 18
    STACK_TEMPERATURE = 20

    BURNER_RUN_TIME_HOURS = 24
    BURNER_CYCLE_COUNT = 26
    HOLD_CODE = 28
    LOCKOUT_CODE = 29

    DEMAND_SOURCE = 100
    FLAME_SIGNAL = 101
    FAN_SPEED_DEMANDED = 103
    FAN_SPEED_FEEDBACK = 104

    PUMP_A_STATUS = 106
    PUMP_B_STATUS = 107
    PUMP_C_STATUS = 108

    PUMP_A_CYCLES = 686
    PUMP_B_CYCLES = 688
    PUMP_C_CYCLES = 690

    @property
    def address(self) -> int:
        """Zero-based PDU address for this register (the high word for U32)."""
        return int(self)

    @property
    def word_count(self) -> int:
        """How many 16-bit words this register occupies (1 or 2)."""
        return 2 if self in U32_COUNTER_REGISTERS else 1


TEMPERATURE_REGISTERS = frozenset(
    {
        Register.ACTIVE_SETPOINT,
        Register.SUPPLY_TEMPERATURE,
        Register.RETURN_TEMPERATURE,
        Register.DHW_TEMPERATURE,
        Register.STACK_TEMPERATURE,
    }
)

DEMAND_REGISTERS = frozenset(
    {
        Register.DHW_HEAT_DEMAND,
        Register.CH1_HEAT_DEMAND,
        Register.CH2_HEAT_DEMAND,
    }
)

PUMP_STATUS_REGISTERS = frozenset(
    {
        Register.PUMP_A_STATUS,
        Register.PUMP_B_STATUS,
        Register.PUMP_C_STATUS,
    }
)

FLAG_REGISTERS = DEMAND_REGISTERS | PUMP_STATUS_REGISTERS

PERCENT_REGISTERS = frozenset({Register.FLAME_SIGNAL})

RPM_REGISTERS = frozenset(
    {
        Register.FAN_SPEED_DEMANDED,
        Register.FAN_SPEED_FEEDBACK,
    }
)

RAW_CODE_REGISTERS = frozenset(
    {
        Register.BURNER_CONTROL_STATE,
        Register.DEMAND_SOURCE,
        Register.HOLD_CODE,
        Register.LOCKOUT_CODE,
    }
)

U32_COUNTER_REGISTERS = frozenset(
    {
        Register.BURNER_RUN_TIME_HOURS,
        Register.BURNER_CYCLE_COUNT,
        Register.PUMP_A_CYCLES,
        Register.PUMP_B_CYCLES,
        Register.PUMP_C_CYCLES,
    }
)

# Every modelled register must have exactly one interpretation.  A future
# edit that adds a Register without deciding how to decode it fails here at
# import instead of producing a mysterious attribute error in a poll.
_CLASSIFIED_REGISTERS = (
    TEMPERATURE_REGISTERS
    | FLAG_REGISTERS
    | PERCENT_REGISTERS
    | RPM_REGISTERS
    | RAW_CODE_REGISTERS
    | U32_COUNTER_REGISTERS
)
if _CLASSIFIED_REGISTERS != frozenset(Register):
    _unclassified = sorted(set(Register) - _CLASSIFIED_REGISTERS)
    _duplicated = sorted(
        register
        for register in Register
        if sum(
            register in group
            for group in (
                TEMPERATURE_REGISTERS,
                FLAG_REGISTERS,
                PERCENT_REGISTERS,
                RPM_REGISTERS,
                RAW_CODE_REGISTERS,
                U32_COUNTER_REGISTERS,
            )
        )
        != 1
    )
    raise RuntimeError(
        "register classification is inconsistent: "
        f"unclassified={_unclassified}, duplicated={_duplicated}"
    )


@dataclass(frozen=True)
class Reading:
    """One modelled register: the raw wire value plus its interpretation."""

    register: Register
    raw: int
    """Raw value.  For a 32-bit counter this is the combined unsigned value."""
    value: int | None
    """Decoded value, or ``None`` when the register reports unavailable."""
    raw_words: tuple[int, ...]
    """The raw 16-bit word or words exactly as received, high word first."""

    @property
    def address(self) -> int:
        """Zero-based PDU address of the register."""
        return self.register.address

    @property
    def width(self) -> int:
        """Width of the raw value in bits (16 or 32)."""
        return 16 * len(self.raw_words)

    @property
    def available(self) -> bool:
        """Whether the register produced a usable value."""
        return self.value is not None

    @property
    def unit(self) -> str | None:
        """Wire unit for known measurement kinds, ``None`` for codes."""
        if self.register in TEMPERATURE_REGISTERS:
            return "°F"
        if self.register in PERCENT_REGISTERS:
            return "%"
        if self.register in RPM_REGISTERS:
            return "rpm"
        return None


@dataclass(frozen=True)
class RegisterBlock:
    """One contiguous function-03 response: values in wire order."""

    start_address: int
    registers: tuple[int, ...]

    @property
    def count(self) -> int:
        """Number of registers in the block."""
        return len(self.registers)

    @property
    def end_address(self) -> int:
        """Last address covered by the block."""
        return self.start_address + len(self.registers) - 1

    def raw_at(self, address: int) -> int:
        """Return the raw 16-bit value at *address*.

        Raises ``KeyError`` when the address is outside this block.
        """
        offset = int(address) - self.start_address
        if not 0 <= offset < len(self.registers):
            raise KeyError(
                f"address {address} is not covered by this block "
                f"({self.start_address}..{self.end_address})"
            )
        return self.registers[offset]


@dataclass(frozen=True)
class Snapshot:
    """All blocks from one polling cycle plus the modelled readings.

    A poll performs several non-contiguous reads, so the snapshot is a tuple of
    :class:`RegisterBlock` objects rather than one range.  Blocks that failed
    are described in :attr:`block_errors`; their readings are simply absent,
    which makes those entities unavailable without inventing a value.
    """

    blocks: tuple[RegisterBlock, ...]
    readings: Mapping[Register, Reading]
    block_errors: Mapping[tuple[int, int], str] = field(default_factory=dict)
    """Requested ``(start_address, count)`` to error text for failed blocks."""

    @property
    def start_address(self) -> int:
        """Start of the first block (compatibility convenience)."""
        if not self.blocks:
            raise ValueError("snapshot contains no register blocks")
        return self.blocks[0].start_address

    @property
    def registers(self) -> tuple[int, ...]:
        """Registers from the first block (compatibility convenience)."""
        return self.blocks[0].registers if self.blocks else ()

    @property
    def failed_blocks(self) -> frozenset[tuple[int, int]]:
        """Requested ``(start_address, count)`` pairs that failed."""
        return frozenset(self.block_errors)

    def raw_at(self, address: int) -> int:
        """Return the raw 16-bit value at *address* from any block.

        Raises ``KeyError`` when no successful block covers the address.
        """
        for block in self.blocks:
            try:
                return block.raw_at(address)
            except KeyError:
                continue
        raise KeyError(f"address {address} is not covered by any successful read")


def _validate_raw(raw: int) -> int:
    if not isinstance(raw, int):
        raise TypeError(f"raw register value must be an int, not {type(raw).__name__}")
    if not _MIN_RAW <= raw <= _MAX_RAW:
        raise ValueError(f"raw value {raw} is outside the 16-bit range 0..0xFFFF")
    return raw


def _validate_start(start_address: int) -> int:
    start = int(start_address)
    if not 0 <= start <= _MAX_RAW:
        raise ValueError(f"start_address {start} is not a 16-bit address")
    return start


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


def decode_u32(high: int, low: int) -> int:
    """Combine two 16-bit words into an unsigned 32-bit value, high first.

    The high-word-first order is inferred from live samples and remains
    provisional.  No sentinel or range check is applied: the manual's
    0..999,999 cycle limit is documentation, not a wire guarantee.
    """
    return (_validate_raw(high) << 16) | _validate_raw(low)


def decode_burner_control_state(raw: int) -> int:
    """Return the burner control state exactly as reported.

    No labels are attached: only state 1 has been observed, and the
    manufacturer's ProtoAir point list disagrees with the manual for some
    enums.  Treat the number as provisional.
    """
    return _validate_raw(raw)


def decode_flag(raw: int) -> int:
    """Return a documented 0/1 flag exactly as reported.

    Unexpected values are passed through rather than coerced, so a diagnostic
    entity never claims a state the boiler did not send.
    """
    return _validate_raw(raw)


def _decode_value(register: Register, raw: int) -> int | None:
    if register in TEMPERATURE_REGISTERS:
        return decode_temperature(raw)
    if register in FLAG_REGISTERS:
        return decode_flag(raw)
    if register in PERCENT_REGISTERS or register in RPM_REGISTERS:
        # Deliberately no sentinel or range handling: 0x7FFF is only
        # provisionally understood for temperatures.
        return _validate_raw(raw)
    if register in RAW_CODE_REGISTERS:
        return decode_burner_control_state(raw)
    raise AssertionError(f"no scalar decoder for {register.name}")


def _coerce_block(
    start_address: int, registers: Sequence[int] | Iterable[int]
) -> RegisterBlock:
    start = _validate_start(start_address)
    values = tuple(_validate_raw(raw) for raw in registers)
    if not values:
        raise ValueError("a read must contain at least one register")
    if start + len(values) > 0x10000:
        raise ValueError(
            f"read of {len(values)} registers from {start} runs past address 0xFFFF"
        )
    return RegisterBlock(start_address=start, registers=values)


def decode_blocks(
    blocks: Iterable[tuple[int, Sequence[int] | Iterable[int]]],
    *,
    block_errors: Mapping[tuple[int, int], str] | None = None,
) -> Snapshot:
    """Decode the registers from several function-03 reads.

    Each item is ``(start_address, registers)``.  Overlapping ranges are
    rejected because two different values for one address would make the
    snapshot ambiguous.  ``block_errors`` is carried through for diagnostics;
    failed blocks contribute no readings.
    """
    decoded: list[RegisterBlock] = []
    for start_address, registers in blocks:
        decoded.append(_coerce_block(start_address, registers))

    if not decoded:
        raise ValueError("a snapshot must contain at least one register block")

    ordered = tuple(sorted(decoded, key=lambda block: block.start_address))
    for previous, current in zip(ordered, ordered[1:]):
        if current.start_address <= previous.end_address:
            raise ValueError(
                "register blocks overlap: "
                f"{previous.start_address}..{previous.end_address} and "
                f"{current.start_address}..{current.end_address}"
            )

    raw_by_address: dict[int, int] = {}
    for block in ordered:
        for offset, raw in enumerate(block.registers):
            raw_by_address[block.start_address + offset] = raw

    readings: dict[Register, Reading] = {}
    for register in Register:
        address = register.address
        if register in U32_COUNTER_REGISTERS:
            high = raw_by_address.get(address)
            low = raw_by_address.get(address + 1)
            if high is None or low is None:
                continue
            combined = decode_u32(high, low)
            readings[register] = Reading(
                register=register,
                raw=combined,
                value=combined,
                raw_words=(high, low),
            )
            continue

        raw = raw_by_address.get(address)
        if raw is None:
            continue
        readings[register] = Reading(
            register=register,
            raw=raw,
            value=_decode_value(register, raw),
            raw_words=(raw,),
        )

    return Snapshot(
        blocks=tuple(decoded),
        readings=readings,
        block_errors=dict(block_errors or {}),
    )


def decode_registers(
    start_address: int,
    registers: tuple[int, ...] | list[int],
) -> Snapshot:
    """Decode the registers from one function-03 read.

    *start_address* is the address that was requested, because a Modbus
    response does not carry it.  Registers that are not modelled are retained
    in :attr:`Snapshot.blocks` and can be read with :meth:`Snapshot.raw_at`.
    """
    return decode_blocks(((start_address, registers),))

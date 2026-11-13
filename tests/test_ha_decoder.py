"""Tests for the integration-local pure decoder. No Home Assistant required."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_DECODER_PATH = REPO_ROOT / "custom_components" / "burnham_alta" / "decoder.py"
_SPEC = importlib.util.spec_from_file_location("burnham_alta_decoder_test", _DECODER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
decoder = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = decoder
_SPEC.loader.exec_module(decoder)

UNAVAILABLE_RAW = decoder.UNAVAILABLE_RAW
Register = decoder.Register
decode_blocks = decoder.decode_blocks
decode_burner_control_state = decoder.decode_burner_control_state
decode_registers = decoder.decode_registers
decode_temperature = decoder.decode_temperature
decode_u32 = decoder.decode_u32
to_int16 = decoder.to_int16

TEMPERATURE_REGISTERS = decoder.TEMPERATURE_REGISTERS
FLAG_REGISTERS = decoder.FLAG_REGISTERS
PERCENT_REGISTERS = decoder.PERCENT_REGISTERS
RPM_REGISTERS = decoder.RPM_REGISTERS
RAW_CODE_REGISTERS = decoder.RAW_CODE_REGISTERS
U32_COUNTER_REGISTERS = decoder.U32_COUNTER_REGISTERS

# Live read-only samples, 2026-11-13, unit 1, FC03.  Blocks are written in the
# order the client requests them.  The 24 block combines the captured 24..27
# and 28..29 reads.
LIVE_BLOCKS = (
    (13, (0x0001, 0x0090, 0x7FFF, 0x0083, 0x0082, 0x7FFF, 0x0000, 0x0063)),
    (3, (0x0000,)),
    (9, (0x0000, 0x0000)),
    (24, (0x0000, 0x0017, 0x0000, 0x011C, 0x0000, 0x0000)),
    (100, (0x0001, 0x0000)),
    (103, (0x0000, 0x0000)),
    (106, (0x0000, 0x0000, 0x0000)),
    (686, (0x0000, 0x010B, 0x0000, 0x0199, 0x0000, 0x0018)),
)

# Live read-only capture, 2026-11-13, unit 1, FC03, address 13, count 8.
CAPTURE_RAW = (0x0001, 0x008C, 0x7FFF, 0x0079, 0x0078, 0x7FFF, 0x0000, 0x005C)


class CaptureDecodeTests(unittest.TestCase):
    def test_capture_values(self):
        snapshot = decode_registers(13, CAPTURE_RAW)
        self.assertEqual(snapshot.start_address, 13)
        self.assertEqual(snapshot.registers, CAPTURE_RAW)
        self.assertEqual(
            snapshot.readings[Register.BURNER_CONTROL_STATE].value, 1
        )
        self.assertEqual(snapshot.readings[Register.ACTIVE_SETPOINT].value, 140)
        self.assertEqual(snapshot.readings[Register.SUPPLY_TEMPERATURE].value, 121)
        self.assertEqual(snapshot.readings[Register.RETURN_TEMPERATURE].value, 120)
        self.assertIsNone(snapshot.readings[Register.DHW_TEMPERATURE].value)
        self.assertEqual(snapshot.readings[Register.STACK_TEMPERATURE].value, 92)

    def test_unmodelled_registers_are_retained(self):
        snapshot = decode_registers(13, CAPTURE_RAW)
        self.assertEqual(snapshot.raw_at(15), UNAVAILABLE_RAW)
        self.assertEqual(snapshot.raw_at(19), 0x0000)
        self.assertNotIn(15, [int(register) for register in snapshot.readings])
        self.assertNotIn(19, [int(register) for register in snapshot.readings])

    def test_sentinel_is_unavailable_only_for_temperatures(self):
        snapshot = decode_registers(13, CAPTURE_RAW)
        self.assertFalse(snapshot.readings[Register.DHW_TEMPERATURE].available)
        self.assertEqual(
            snapshot.readings[Register.BURNER_CONTROL_STATE].value, 1
        )
        self.assertEqual(decode_burner_control_state(UNAVAILABLE_RAW), UNAVAILABLE_RAW)

    def test_start_address_aligns_offsets(self):
        snapshot = decode_registers(15, (UNAVAILABLE_RAW, 0x0079))
        self.assertEqual(snapshot.raw_at(15), UNAVAILABLE_RAW)
        self.assertEqual(
            snapshot.readings[Register.SUPPLY_TEMPERATURE].value, 121
        )
        self.assertNotIn(Register.ACTIVE_SETPOINT, snapshot.readings)

    def test_raw_at_outside_read(self):
        snapshot = decode_registers(13, CAPTURE_RAW)
        for address in (12, 21):
            with self.subTest(address=address):
                with self.assertRaises(KeyError):
                    snapshot.raw_at(address)


class TemperatureTests(unittest.TestCase):
    def test_signed_values(self):
        self.assertEqual(decode_temperature(0x0140), 320)
        self.assertEqual(decode_temperature(0xFFD8), -40)
        self.assertEqual(decode_temperature(0xFFF6), -10)
        self.assertEqual(to_int16(0x8000), -32768)

    def test_invalid_raw_values(self):
        for raw in (-1, 0x10000):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    decode_temperature(raw)
                with self.assertRaises(ValueError):
                    to_int16(raw)

    def test_empty_read_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_registers(13, ())

    def test_non_integer_values_are_rejected(self):
        with self.assertRaises(TypeError):
            decode_registers(13, ("1",))  # type: ignore[arg-type]


class MultiBlockTests(unittest.TestCase):
    def test_live_capture_decodes_every_documented_point(self):
        snapshot = decode_blocks(LIVE_BLOCKS)

        expected = {
            Register.BURNER_CONTROL_STATE: 1,
            Register.ACTIVE_SETPOINT: 144,
            Register.SUPPLY_TEMPERATURE: 131,
            Register.RETURN_TEMPERATURE: 130,
            Register.DHW_TEMPERATURE: None,
            Register.STACK_TEMPERATURE: 99,
            Register.DHW_HEAT_DEMAND: 0,
            Register.CH1_HEAT_DEMAND: 0,
            Register.CH2_HEAT_DEMAND: 0,
            Register.HOLD_CODE: 0,
            Register.LOCKOUT_CODE: 0,
            Register.DEMAND_SOURCE: 1,
            Register.FLAME_SIGNAL: 0,
            Register.FAN_SPEED_DEMANDED: 0,
            Register.FAN_SPEED_FEEDBACK: 0,
            Register.PUMP_A_STATUS: 0,
            Register.PUMP_B_STATUS: 0,
            Register.PUMP_C_STATUS: 0,
            Register.BURNER_RUN_TIME_HOURS: 23,
            Register.BURNER_CYCLE_COUNT: 284,
            Register.PUMP_A_CYCLES: 267,
            Register.PUMP_B_CYCLES: 409,
            Register.PUMP_C_CYCLES: 24,
        }
        self.assertEqual(
            {register: reading.value for register, reading in snapshot.readings.items()},
            expected,
        )

    def test_raw_at_spans_every_block(self):
        snapshot = decode_blocks(LIVE_BLOCKS)
        self.assertEqual(snapshot.raw_at(13), 0x0001)
        self.assertEqual(snapshot.raw_at(3), 0x0000)
        self.assertEqual(snapshot.raw_at(9), 0x0000)
        self.assertEqual(snapshot.raw_at(24), 0x0000)
        self.assertEqual(snapshot.raw_at(29), 0x0000)
        self.assertEqual(snapshot.raw_at(100), 0x0001)
        self.assertEqual(snapshot.raw_at(103), 0x0000)
        self.assertEqual(snapshot.raw_at(108), 0x0000)
        self.assertEqual(snapshot.raw_at(686), 0x0000)
        self.assertEqual(snapshot.raw_at(691), 0x0018)
        for address in (2, 4, 99, 102, 109, 685, 692):
            with self.subTest(address=address):
                with self.assertRaises(KeyError):
                    snapshot.raw_at(address)

    def test_block_errors_are_retained_without_dropping_good_blocks(self):
        snapshot = decode_blocks(
            LIVE_BLOCKS,
            block_errors={(686, 6): "gateway rejected the block"},
        )
        self.assertEqual(
            snapshot.block_errors,
            {(686, 6): "gateway rejected the block"},
        )
        self.assertEqual(snapshot.failed_blocks, {(686, 6)})
        self.assertIn(Register.PUMP_A_CYCLES, snapshot.readings)

    def test_overlapping_blocks_are_rejected(self):
        with self.assertRaises(ValueError):
            decode_blocks(((13, (1, 2)), (14, (3,))))

    def test_empty_snapshot_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_blocks(())

    def test_u32_word_order_is_high_first(self):
        self.assertEqual(decode_u32(0x0000, 0x0017), 23)
        self.assertEqual(decode_u32(0x0001, 0x0000), 65536)
        self.assertEqual(decode_u32(0xFFFF, 0xFFFF), 0xFFFFFFFF)

        snapshot = decode_blocks(LIVE_BLOCKS)
        run_time = snapshot.readings[Register.BURNER_RUN_TIME_HOURS]
        self.assertEqual(run_time.raw, 23)
        self.assertEqual(run_time.raw_words, (0x0000, 0x0017))
        self.assertEqual(run_time.width, 32)

    def test_u32_requires_both_words(self):
        snapshot = decode_registers(24, (0x0000, 0x0017))
        self.assertEqual(
            snapshot.readings[Register.BURNER_RUN_TIME_HOURS].value, 23
        )
        self.assertNotIn(Register.BURNER_CYCLE_COUNT, snapshot.readings)

    def test_raw_codes_and_flags_are_never_relabelled(self):
        snapshot = decode_registers(
            100,
            (0x0005, 0x00FF),  # demand source and flame, both outside 0/1
        )
        self.assertEqual(snapshot.readings[Register.DEMAND_SOURCE].value, 5)
        self.assertEqual(snapshot.readings[Register.FLAME_SIGNAL].value, 255)

        snapshot = decode_registers(106, (0x0002, 0x0001, 0x0000))
        self.assertEqual(snapshot.readings[Register.PUMP_A_STATUS].value, 2)
        self.assertEqual(snapshot.readings[Register.PUMP_B_STATUS].value, 1)

    def test_sentinel_is_not_applied_outside_temperatures(self):
        for address, register in (
            (28, Register.HOLD_CODE),
            (29, Register.LOCKOUT_CODE),
            (100, Register.DEMAND_SOURCE),
            (101, Register.FLAME_SIGNAL),
            (103, Register.FAN_SPEED_DEMANDED),
            (106, Register.PUMP_A_STATUS),
        ):
            with self.subTest(register=register.name):
                snapshot = decode_registers(address, (UNAVAILABLE_RAW,))
                self.assertEqual(
                    snapshot.readings[register].value, UNAVAILABLE_RAW
                )
                self.assertTrue(snapshot.readings[register].available)

    def test_every_register_has_exactly_one_interpretation(self):
        groups = (
            TEMPERATURE_REGISTERS,
            FLAG_REGISTERS,
            PERCENT_REGISTERS,
            RPM_REGISTERS,
            RAW_CODE_REGISTERS,
            U32_COUNTER_REGISTERS,
        )
        self.assertEqual(set().union(*groups), set(Register))
        for register in Register:
            self.assertEqual(
                sum(register in group for group in groups),
                1,
                register.name,
            )



if __name__ == "__main__":
    unittest.main()

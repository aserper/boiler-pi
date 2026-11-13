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
decode_burner_control_state = decoder.decode_burner_control_state
decode_registers = decoder.decode_registers
decode_temperature = decoder.decode_temperature
to_int16 = decoder.to_int16

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


if __name__ == "__main__":
    unittest.main()

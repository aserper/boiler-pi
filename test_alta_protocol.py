"""Unit tests for the pure ALTA decoder. No hardware, no I/O.

Run from the project root:

    python3 -B -m unittest -v test_alta_protocol
"""

import unittest

from alta_protocol import (
    UNAVAILABLE_RAW,
    AltaDecodeError,
    CrcMismatchError,
    ModbusExceptionResponse,
    Register,
    Snapshot,
    crc16_modbus,
    decode_burner_control_state,
    decode_response,
    decode_temperature,
    parse_response,
    to_int16,
)

# Live read-only capture, 2026-11-13, unit 1, FC03, address 13, count 8
# (docs/registers.md). Values: 1, 140, 32767, 121, 120, 32767, 0, 92.
CAPTURE = bytes.fromhex("0103100001008c7fff007900787fff0000005c455f")
CAPTURE_RAW = (0x0001, 0x008C, 0x7FFF, 0x0079, 0x0078, 0x7FFF, 0x0000, 0x005C)

# Second live capture: address 15, count 2 -> 0x7fff (HR 15, not modelled), 0x0079.
CAPTURE_15_2 = bytes.fromhex("0103047fff00791235")


def with_crc(pdu: bytes) -> bytes:
    """Append a correct CRC-16/Modbus (low byte first) to a raw PDU."""
    return pdu + crc16_modbus(pdu).to_bytes(2, "little")


class CrcTests(unittest.TestCase):
    def test_capture_crc_matches_wire_bytes(self):
        self.assertEqual(crc16_modbus(CAPTURE[:-2]), 0x5F45)
        self.assertEqual(CAPTURE[-2:], bytes([0x45, 0x5F]))

    def test_second_capture_crc_matches_wire_bytes(self):
        self.assertEqual(crc16_modbus(CAPTURE_15_2[:-2]), 0x3512)
        self.assertEqual(CAPTURE_15_2[-2:], bytes([0x12, 0x35]))

    def test_standard_check_value(self):
        # CRC-16/MODBUS of the ASCII string "123456789" is 0x4B37.
        self.assertEqual(crc16_modbus(b"123456789"), 0x4B37)


class SampleCaptureTests(unittest.TestCase):
    def test_parse_returns_slave_and_every_raw_register(self):
        response = parse_response(CAPTURE, expected_slave=1)
        self.assertEqual(response.slave, 1)
        self.assertEqual(response.registers, CAPTURE_RAW)
        self.assertEqual(response.byte_count, 16)

    def test_readings_of_the_verified_points(self):
        snapshot = decode_response(CAPTURE, start_address=13)
        expected = {
            Register.BURNER_CONTROL_STATE: (0x0001, 1),
            Register.ACTIVE_SETPOINT: (0x008C, 140),
            Register.SUPPLY_TEMPERATURE: (0x0079, 121),
            Register.RETURN_TEMPERATURE: (0x0078, 120),
            Register.DHW_TEMPERATURE: (0x7FFF, None),
            Register.STACK_TEMPERATURE: (0x005C, 92),
        }
        self.assertEqual(set(snapshot.readings), set(expected))
        for register, (raw, value) in expected.items():
            with self.subTest(register=register.name):
                reading = snapshot.readings[register]
                self.assertEqual(reading.raw, raw)
                self.assertEqual(reading.value, value)
                self.assertEqual(reading.address, int(register))
                self.assertEqual(reading.raw, snapshot.raw_at(reading.address))

    def test_unavailable_sentinel_keeps_raw_value(self):
        dhw = decode_response(CAPTURE).readings[Register.DHW_TEMPERATURE]
        self.assertFalse(dhw.available)
        self.assertIsNone(dhw.value)
        self.assertEqual(dhw.raw, UNAVAILABLE_RAW)

    def test_unmodelled_registers_are_retained(self):
        snapshot = decode_response(CAPTURE, start_address=13)
        self.assertIsInstance(snapshot, Snapshot)
        self.assertEqual(snapshot.start_address, 13)
        self.assertEqual(snapshot.raw_at(15), 0x7FFF)
        self.assertEqual(snapshot.raw_at(19), 0x0000)
        self.assertNotIn(15, [int(register) for register in snapshot.readings])
        self.assertNotIn(19, [int(register) for register in snapshot.readings])

    def test_start_address_aligns_offset(self):
        snapshot = decode_response(CAPTURE_15_2, start_address=15)
        self.assertEqual(snapshot.raw_at(15), 0x7FFF)
        self.assertEqual(
            snapshot.readings[Register.SUPPLY_TEMPERATURE].raw, 0x0079
        )
        self.assertEqual(
            snapshot.readings[Register.SUPPLY_TEMPERATURE].value, 121
        )
        # HR 15 is not modelled, so it must not leak into a neighbouring reading.
        self.assertEqual(set(snapshot.readings), {Register.SUPPLY_TEMPERATURE})
        self.assertNotIn(Register.ACTIVE_SETPOINT, snapshot.readings)

    def test_default_start_address_is_the_captured_read(self):
        self.assertEqual(
            decode_response(CAPTURE).readings[Register.SUPPLY_TEMPERATURE].value,
            121,
        )


class RegisterTests(unittest.TestCase):
    def test_enum_values_are_the_manual_hr_numbers(self):
        self.assertEqual([int(register) for register in Register], [13, 14, 16, 17, 18, 20])
        self.assertEqual([register.address for register in Register], [13, 14, 16, 17, 18, 20])

    def test_temperatures_report_fahrenheit_and_state_does_not(self):
        readings = decode_response(CAPTURE).readings
        self.assertEqual(readings[Register.SUPPLY_TEMPERATURE].unit, "°F")
        self.assertEqual(readings[Register.ACTIVE_SETPOINT].unit, "°F")
        self.assertIsNone(readings[Register.BURNER_CONTROL_STATE].unit)


class TemperatureDecodingTests(unittest.TestCase):
    def test_documented_span_edges(self):
        self.assertEqual(decode_temperature(0x0140), 320)
        self.assertEqual(decode_temperature(0xFFD8), -40)

    def test_signed_values(self):
        self.assertEqual(decode_temperature(0x0000), 0)
        self.assertEqual(decode_temperature(0xFFF6), -10)
        self.assertEqual(decode_temperature(0x8000), -32768)
        self.assertEqual(to_int16(0x7FFF), 32767)
        self.assertEqual(to_int16(0xFFFF), -1)

    def test_sentinel_is_unavailable(self):
        self.assertIsNone(decode_temperature(UNAVAILABLE_RAW))

    def test_rejects_values_outside_16_bits(self):
        for raw in (-1, 0x10000):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    decode_temperature(raw)
                with self.assertRaises(ValueError):
                    to_int16(raw)
                with self.assertRaises(ValueError):
                    decode_burner_control_state(raw)

    def test_rejects_non_integer(self):
        with self.assertRaises(TypeError):
            decode_temperature("140")  # pyright: ignore[reportArgumentType]
        with self.assertRaises(TypeError):
            to_int16(None)  # pyright: ignore[reportArgumentType]


class BurnerStateTests(unittest.TestCase):
    def test_state_is_a_plain_integer_with_raw_retained(self):
        reading = decode_response(CAPTURE).readings[Register.BURNER_CONTROL_STATE]
        self.assertEqual(reading.raw, 1)
        self.assertEqual(reading.value, 1)
        self.assertIsInstance(reading.value, int)
        self.assertTrue(reading.available)

    def test_state_is_not_translated_or_sentinel_checked(self):
        self.assertEqual(decode_burner_control_state(1), 1)
        self.assertEqual(decode_burner_control_state(0x0000), 0)
        self.assertEqual(decode_burner_control_state(14), 14)
        # 0x7FFF is only treated as "unavailable" for temperatures; the state
        # decoder must hand back the value it was given, not None.
        self.assertEqual(
            decode_burner_control_state(UNAVAILABLE_RAW), UNAVAILABLE_RAW
        )


class MalformedFrameTests(unittest.TestCase):
    def assert_rejected(self, frame, message_part=None):
        with self.assertRaises(AltaDecodeError) as context:
            parse_response(frame)
        if message_part is not None:
            self.assertIn(message_part, str(context.exception))
        return context.exception

    def test_empty_frame(self):
        self.assert_rejected(b"", "at least 5")

    def test_truncated_frame(self):
        self.assert_rejected(bytes.fromhex("0103"), "at least 5")
        self.assert_rejected(bytes.fromhex("010310"), "at least 5")

    def test_header_without_declared_data(self):
        self.assert_rejected(with_crc(bytes([0x01, 0x03, 0x10])), "byte count 16")

    def test_unsupported_function_code(self):
        self.assert_rejected(
            with_crc(bytes([0x01, 0x04, 0x02, 0x00, 0x01])), "unsupported function"
        )
        self.assert_rejected(with_crc(bytes([0x01, 0x06, 0x02, 0x00, 0x01])), "unsupported function")

    def test_modbus_exception_response(self):
        frame = with_crc(bytes([0x01, 0x83, 0x02]))
        with self.assertRaises(ModbusExceptionResponse) as context:
            parse_response(frame)
        error = context.exception
        self.assertEqual(error.slave, 1)
        self.assertEqual(error.function_code, 0x03)
        self.assertEqual(error.exception_code, 0x02)
        self.assertIsInstance(error, AltaDecodeError)

    def test_exception_response_length_is_checked(self):
        self.assert_rejected(
            with_crc(bytes([0x01, 0x83, 0x02, 0x00])), "exception response must be 5"
        )

    def test_exception_response_crc_is_checked(self):
        with self.assertRaises(CrcMismatchError):
            parse_response(bytes([0x01, 0x83, 0x02, 0x00, 0x00]))

    def test_odd_byte_count(self):
        self.assert_rejected(with_crc(bytes([0x01, 0x03, 0x03, 0x00, 0x01, 0x00])), "odd")

    def test_zero_byte_count(self):
        self.assert_rejected(with_crc(bytes([0x01, 0x03, 0x00])), "byte count is 0")

    def test_byte_count_larger_than_payload(self):
        self.assert_rejected(with_crc(bytes([0x01, 0x03, 0x04, 0x00, 0x01])), "byte count 4")

    def test_byte_count_smaller_than_payload(self):
        self.assert_rejected(
            with_crc(bytes([0x01, 0x03, 0x02, 0x00, 0x01, 0x00, 0x02])),
            "byte count 2",
        )

    def test_oversized_byte_count(self):
        pdu = bytes([0x01, 0x03, 252]) + bytes(252)
        self.assert_rejected(with_crc(pdu), "125-register")

    def test_corrupted_crc(self):
        corrupted = CAPTURE[:-1] + bytes([CAPTURE[-1] ^ 0xFF])
        with self.assertRaises(CrcMismatchError) as context:
            parse_response(corrupted)
        self.assertEqual(context.exception.expected, 0x5F45)
        # Modbus CRCs travel low byte first.
        self.assertEqual(
            context.exception.received,
            CAPTURE[-2] | ((CAPTURE[-1] ^ 0xFF) << 8),
        )

    def test_trailing_bytes_rejected(self):
        self.assert_rejected(CAPTURE + b"\x00", "frame is 22 bytes")

    def test_slave_mismatch(self):
        with self.assertRaises(AltaDecodeError) as context:
            parse_response(CAPTURE, expected_slave=2)
        self.assertIn("expected 2", str(context.exception))

    def test_slave_check_accepts_expected_slave(self):
        self.assertEqual(parse_response(CAPTURE, expected_slave=1).slave, 1)

    def test_non_bytes_input(self):
        with self.assertRaises(TypeError):
            parse_response("0103100001008c7fff007900787fff0000005c455f")  # pyright: ignore[reportArgumentType]
        with self.assertRaises(TypeError):
            parse_response(None)  # pyright: ignore[reportArgumentType]

    def test_bytes_like_input_accepted(self):
        for frame in (bytearray(CAPTURE), memoryview(CAPTURE)):
            with self.subTest(kind=type(frame).__name__):
                self.assertEqual(
                    parse_response(frame).registers, CAPTURE_RAW
                )

    def test_decode_response_rejects_bad_frame(self):
        with self.assertRaises(CrcMismatchError):
            decode_response(CAPTURE[:-1] + b"\x00")

    def test_decode_response_rejects_bad_start_address(self):
        for start in (-1, 0x10000):
            with self.subTest(start=start):
                with self.assertRaises(ValueError):
                    decode_response(CAPTURE, start_address=start)

    def test_raw_at_outside_frame(self):
        snapshot = decode_response(CAPTURE, start_address=13)
        for address in (12, 21, -1):
            with self.subTest(address=address):
                with self.assertRaises(KeyError):
                    snapshot.raw_at(address)


if __name__ == "__main__":
    unittest.main()

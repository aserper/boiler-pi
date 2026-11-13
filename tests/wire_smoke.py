"""Real pymodbus wire test against a loopback simulator, never the boiler.

Run separately from fake-HA unit tests:
uv run --no-project --with pymodbus==3.13.1 --with pyserial==3.5 python -B tests/wire_smoke.py
"""
import asyncio
import pathlib
import struct
import sys
import types

package = types.ModuleType("alta_wire_test")
package.__path__ = [str(pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "burnham_alta")]
sys.modules[package.__name__] = package
from alta_wire_test.modbus import AltaModbusClient
from alta_wire_test.decoder import Register

# Independent expected plan and captured values, not imported from production.
BLOCKS = {
    (13, 8): (1, 140, 32767, 121, 120, 32767, 0, 92),
    (3, 1): (0,),
    (9, 2): (0, 0),
    (24, 6): (0, 23, 0, 284, 0, 0),
    (100, 2): (1, 0),
    (103, 2): (0, 0),
    (106, 3): (0, 0, 0),
    (686, 6): (0, 267, 0, 409, 0, 24),
}


async def main():
    requests = []
    errors = []
    mode = "ok"

    async def simulate(reader, writer):
        try:
            while True:
                header = await asyncio.wait_for(reader.readexactly(7), timeout=3)
                transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                pdu = await asyncio.wait_for(reader.readexactly(length - 1), timeout=3)
                function, start, count = struct.unpack(">BHH", pdu)
                requests.append((protocol, unit, function, start, count))
                assert protocol == 0 and unit == 1 and function == 3
                assert (start, count) in BLOCKS
                if mode == "partial" and start == 106:
                    response = bytes([0x83, 2])
                else:
                    values = BLOCKS[(start, count)]
                    response = bytes([3, 2 * count]) + struct.pack(f">{count}H", *values)
                writer.write(struct.pack(">HHHB", transaction, 0, len(response) + 1, unit) + response)
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        except Exception as error:
            errors.append(error)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(simulate, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = AltaModbusClient({"connection_type": "tcp", "host": "127.0.0.1", "port": port, "device_id": 1})
    try:
        snapshot = await asyncio.wait_for(client.async_read_snapshot(), timeout=5)
        assert requests == [(0, 1, 3, start, count) for start, count in BLOCKS], requests
        assert snapshot.readings[Register.SUPPLY_TEMPERATURE].value == 121
        assert snapshot.readings[Register.RETURN_TEMPERATURE].value == 120
        assert snapshot.readings[Register.DHW_TEMPERATURE].value is None
        assert snapshot.readings[Register.BURNER_RUN_TIME_HOURS].value == 23
        assert snapshot.readings[Register.BURNER_CYCLE_COUNT].value == 284
        assert len(snapshot.readings) == 23
        mode = "partial"
        requests.clear()
        partial = await asyncio.wait_for(client.async_read_snapshot(), timeout=5)
        assert len(requests) == 8
        assert Register.PUMP_A_STATUS not in partial.readings
        assert partial.readings[Register.BURNER_CYCLE_COUNT].value == 284
        assert (106, 3) in partial.block_errors
        assert not errors, errors
        print("PASS: eight FC03 wire requests, 23 readings, counters, partial exception isolation")
    finally:
        client.close()
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())

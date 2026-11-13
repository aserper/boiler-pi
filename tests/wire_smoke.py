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


async def main():
    requests = []

    async def simulate(reader, writer):
        try:
            header = await asyncio.wait_for(reader.readexactly(7), timeout=2)
            transaction, protocol, length, unit = struct.unpack(">HHHB", header)
            pdu = await asyncio.wait_for(reader.readexactly(length - 1), timeout=2)
            requests.append((protocol, unit, pdu))
            values = (1, 140, 32767, 121, 120, 32767, 0, 92)
            response = bytes([3, 16]) + struct.pack(">8H", *values)
            writer.write(struct.pack(">HHHB", transaction, 0, len(response) + 1, unit) + response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(simulate, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = AltaModbusClient({"connection_type": "tcp", "host": "127.0.0.1", "port": port, "device_id": 1})
    try:
        snapshot = await asyncio.wait_for(client.async_read_snapshot(), timeout=5)
        assert requests == [(0, 1, bytes.fromhex("03 00 0d 00 08"))], requests
        assert snapshot.readings[Register.SUPPLY_TEMPERATURE].value == 121
        assert snapshot.readings[Register.RETURN_TEMPERATURE].value == 120
        assert snapshot.readings[Register.DHW_TEMPERATURE].value is None
        print("PASS: actual pymodbus sent only FC03 address 13 count 8; captured values decoded")
    finally:
        client.close()
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())

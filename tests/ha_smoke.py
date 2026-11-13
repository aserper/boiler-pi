"""Actual Home Assistant + pymodbus against a loopback boiler simulator.

Run separately from the fake-module unit tests:
uv run --no-project --python 3.14 --with homeassistant==2026.10.1 \
  --with pymodbus==3.13.1 --with pyserial==3.5 python -B tests/ha_smoke.py

No connection to physical devices, no HA household configuration changes.
"""
import asyncio
from datetime import timedelta
import pathlib
import struct
import sys
import tempfile

from homeassistant import loader
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry, device_registry, entity_registry, floor_registry, frame,
    label_registry,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BLOCKS = {
    (13, 8): (1, 140, 32767, 121, 120, 32767, 0, 92),
    (3, 1): (0,), (9, 2): (1, 0),
    (24, 6): (0, 23, 0, 284, 0, 0), (100, 2): (2, 0),
    (103, 2): (0, 0), (106, 3): (1, 1, 0),
    (686, 6): (0, 267, 0, 409, 0, 24),
}


async def main():
    requests = []
    failures = []
    reject = False
    writers = set()

    async def simulate(reader, writer):
        writers.add(writer)
        try:
            while True:
                header = await reader.readexactly(7)
                transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                fc, address, count = struct.unpack(">BHH", pdu)
                assert protocol == 0 and unit == 1 and fc == 3
                assert (address, count) in BLOCKS
                requests.append((address, count))
                if reject:
                    reply = bytes([0x83, 2])
                else:
                    reply = bytes([3, count * 2]) + struct.pack(f">{count}H", *BLOCKS[(address, count)])
                writer.write(struct.pack(">HHHB", transaction, 0, len(reply) + 1, unit) + reply)
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        except Exception as error:
            failures.append(error)
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)

    server = await asyncio.start_server(simulate, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with tempfile.TemporaryDirectory(prefix="alta-ha-smoke-") as tmp:
            pathlib.Path(tmp, "custom_components").symlink_to(ROOT / "custom_components", target_is_directory=True)
            hass = HomeAssistant(tmp)
            hass.config.skip_pip = True
            hass.config.language = "en"
            loader.async_setup(hass)
            frame.async_setup(hass)
            device_registry.async_setup(hass)
            hass.config_entries = ConfigEntries(hass, {})
            await hass.config_entries.async_initialize()
            for registry in (floor_registry, area_registry, label_registry, device_registry, entity_registry):
                await registry.async_load(hass)
            try:
                menu = await hass.config_entries.flow.async_init("burnham_alta", context={"source": "user"})
                form = await hass.config_entries.flow.async_configure(menu["flow_id"], user_input={"next_step_id": "tcp"})
                result = await hass.config_entries.flow.async_configure(form["flow_id"], user_input={"host": "127.0.0.1", "port": port, "device_id": 1})
                await hass.async_block_till_done()
                assert result["type"] == "create_entry", result
                entry = result["result"]
                coordinator = hass.data["burnham_alta"][entry.entry_id]
                assert coordinator.update_interval == timedelta(seconds=10)
                states = hass.states.async_all("sensor")
                assert len(states) == 23, len(states)
                entity_ids_before = {s.entity_id for s in states}
                assert any(s.entity_id.endswith("burner_cycles") and s.state == "284" for s in states)
                assert any(s.entity_id.endswith("dhw_temperature") and s.state == "unavailable" for s in states)
                assert requests[:8] == list(BLOCKS)
                # Drive the real scheduled timer, without changing its interval.
                old_count = len(requests)
                deadline = asyncio.get_running_loop().time() + 14
                while len(requests) < old_count + 8:
                    assert asyncio.get_running_loop().time() < deadline, "scheduled poll did not run"
                    await asyncio.sleep(0.1)
                reject = True
                await coordinator.async_refresh()
                assert all(s.state == "unavailable" for s in hass.states.async_all("sensor"))
                reject = False
                await coordinator.async_refresh()
                assert any(s.entity_id.endswith("burner_cycles") and s.state == "284" for s in hass.states.async_all("sensor"))
                options_form = await hass.config_entries.options.async_init(entry.entry_id)
                await hass.config_entries.options.async_configure(options_form["flow_id"], user_input={"scan_interval": 15, "ch1_name": "First floor / basement", "ch2_name": "Second floor", "dhw_name": "Hot water"})
                await hass.async_block_till_done()
                coordinator = hass.data["burnham_alta"][entry.entry_id]
                assert coordinator.update_interval == timedelta(seconds=15)
                after = hass.states.async_all("sensor")
                assert {s.entity_id for s in after} == entity_ids_before
                aliases = {s.attributes.get("consumer_name") for s in after if "consumer_name" in s.attributes}
                assert aliases == {"First floor / basement", "Second floor", "Hot water"}, aliases
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert coordinator.client.client is None
                assert not failures, failures
                print("PASS: actual HA config flow, 23 entities, 10s scheduled polling, failure/recovery, options reload, unload; FC03 only")
            finally:
                await hass.async_stop()
    finally:
        server.close()
        await server.wait_closed()
        for writer in list(writers):
            writer.close()
            await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=40))

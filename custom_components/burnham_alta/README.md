# Burnham ALTA (Home Assistant custom integration)

Read-only Home Assistant integration for a Burnham ALTA boiler over Modbus. It
performs one Modbus function-03 read of holding registers 13..20 per polling
cycle and exposes the supported readings as sensors. It never sends a write,
reset, or configuration command, and it creates no writable entities or
services.

## Connection modes

- **Modbus TCP gateway** — Modbus TCP (MBAP) to a protocol-translating serial
  gateway. RTU-over-TCP framing is deliberately not offered; it is a different
  wire format.
- **Direct serial RTU** — a local serial adapter at 38400 8-N-1, unit 1 by
  default. Enter the device path in the config flow; prefer the stable
  `/dev/serial/by-id/...` path when one exists.

Both modes validate by reading the boiler once before the config entry is
created. Polling defaults to every 10 seconds. Use the entry's **Configure**
dialog to change it to a whole number of seconds between 5 and 3600; the
default applies to existing entries until changed, and the entry reloads when
the option is saved. Values outside the range are rejected in the dialog, and a
stored value that cannot be used falls back to the default at runtime.

The pinned dependency is `pymodbus==3.13.1` plus `pyserial>=3.5`, matching Home Assistant
2026.10.1's built-in Modbus integration. Pymodbus 3.13.1 constructs its async
transport with a running event loop, so the client is created lazily inside
the coordinator's async refresh rather than during config-entry setup.

## Entities

Only readings supported by the field capture and the manufacturer's table 4-4
are created:

| Entity key | Source |
| --- | --- |
| `burner_control_state` | HR 13, raw integer; enum labels are not validated |
| `target_temperature` | HR 14, active setpoint (target), °F |
| `supply_temperature` | HR 16, °F; supply/return assignment is not independently confirmed |
| `return_temperature` | HR 17, °F; supply/return assignment is not independently confirmed |
| `dhw_temperature` | HR 18, °F |
| `stack_temperature` | HR 20, °F |

Home Assistant generates entity IDs using the device name. Find the actual IDs
on the device page; the keys above are not guaranteed entity IDs.

`0x7FFF` is exposed as unavailable for temperature readings. That meaning is
provisional. The burner state is never converted to a label; state 1 is the
only observed value.

The following target-dashboard values are **not implemented** because no
register mapping has been validated: demand source, flame signal, fan RPM,
hold/lockout, pump status, demand status, runtime, cycle count, and history.
No placeholder entities or reserved entity IDs are created for them.

`supply_return_delta` is also reserved until the supply/return labels are
confirmed; a dashboard can derive it from the two temperature entities in the
meantime. It is deliberately not published as a register reading.

## Tests

From the repository root:

```
python3 -B -m unittest discover -s tests -p 'test_ha_*.py' -v
```

The tests use fake Home Assistant and pymodbus layers; they do not touch a
boiler or a gateway.

## HACS / repository metadata

Repository metadata and installation instructions live in the repository root.

## Limitations

- An isolated Home Assistant 2026.10.1 instance successfully set up the
  integration and read the real boiler through a TCP gateway. The integration
  has not yet been installed in the household Home Assistant instance; most
  automated unit tests use fake Modbus/HA layers.
- The supply/return assignment, the `0x7FFF` sentinel meaning, and burner enum
  labels remain provisional.
- The serial port must be visible to the Home Assistant process/container.
- The integration does not authenticate Modbus. Restrict network or serial
  access and enforce read-only behavior at the gateway if one is used.

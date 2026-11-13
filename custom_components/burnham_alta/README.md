# Burnham ALTA (Home Assistant custom integration)

Read-only Home Assistant integration for a Burnham ALTA boiler over Modbus. It
sends only function-03 holding-register reads and exposes the documented
readings as sensors. It never sends a write, reset, or configuration command,
and it creates no writable entities or services.

## Connection modes

- **Modbus TCP gateway** — Modbus TCP (MBAP) to a protocol-translating serial
  gateway. RTU-over-TCP framing is deliberately not offered; it is a different
  wire format.
- **Direct serial RTU** — a local serial adapter at 38400 8-N-1, unit 1 by
  default. Enter the device path in the config flow; prefer the stable
  `/dev/serial/by-id/...` path when one exists.

Both modes validate by performing one read-only polling cycle before the
config entry is created. Polling defaults to every 10 seconds. Use the entry's
**Configure** dialog to change it to a whole number of seconds between 5 and
3600; the default applies to existing entries until changed, and the entry
reloads when the option is saved. Values outside the range are rejected in the
dialog, and a stored value that cannot be used falls back to the default at
runtime.

The pinned dependency is `pymodbus==3.13.1` plus `pyserial>=3.5`, matching Home Assistant
2026.10.1's built-in Modbus integration. Pymodbus 3.13.1 constructs its async
transport with a running event loop, so the client is created lazily inside
the coordinator's async refresh rather than during config-entry setup.

## Reads per polling cycle

The manual's HR number is used as the literal zero-based wire address on the
observed boiler. Each cycle reads these contiguous documented blocks in order:

| Start | Count | Covers |
| --- | --- | --- |
| HR 13 | 8 | burner control state, active setpoint, supply/return/DHW/stack temperature |
| HR 3 | 1 | DHW heat demand |
| HR 9 | 2 | CH1 and CH2 heat demand |
| HR 24 | 6 | burner run time 24-25, burner cycles 26-27, hold 28, lockout 29 |
| HR 100 | 2 | demand source, flame signal |
| HR 103 | 2 | fan speed demanded and feedback |
| HR 106 | 3 | pump A/B/C status |
| HR 686 | 6 | pump A/B/C cycle counts |

That is eight short function-03 transactions per cycle, measured at roughly
17-37 ms each on the field gateway. The coordinator starts the next cycle only
after the current one finishes, so polls never overlap even at the 5 second
minimum. The original HR 13..20 block also reads the undocumented addresses
HR 15 and HR 19; they are not decoded. No other undocumented gap is read, and
no write-register address from table 4-4 is ever accessed.

If the device returns a Modbus exception for one block, only that block fails;
the remaining blocks are still read and the snapshot is returned with the
available readings. A transport failure or a malformed frame closes the
connection and stops that cycle, because continuing could send requests into a
desynchronised stream; the next cycle reconnects from scratch and performs the
full sequence again. If no block succeeds, the cycle fails as a normal
Home Assistant update failure. Sensors whose block did not produce a reading
are unavailable rather than showing a fabricated value.

## Entities

Only readings supported by the manufacturer's table 4-4 are created. All are
read-only sensors.

| Entity key | Source | Interpretation |
| --- | --- | --- |
| `burner_control_state` | HR 13 | raw integer; enum labels are not validated |
| `target_temperature` | HR 14, active setpoint | °F |
| `supply_temperature` | HR 16 | °F; supply/return assignment is not independently confirmed |
| `return_temperature` | HR 17 | °F; supply/return assignment is not independently confirmed |
| `dhw_temperature` | HR 18 | °F |
| `stack_temperature` | HR 20 | °F |
| `dhw_heat_demand` | HR 3 | raw 0/1 |
| `ch1_heat_demand` | HR 9 | raw 0/1 |
| `ch2_heat_demand` | HR 10 | raw 0/1 |
| `hold_code` | HR 28 | raw code |
| `lockout_code` | HR 29 | raw code |
| `demand_source` | HR 100 | raw code |
| `flame_signal` | HR 101 | % |
| `fan_speed_demanded` | HR 103 | rpm |
| `fan_speed_feedback` | HR 104 | rpm |
| `pump_a_status` | HR 106 | raw 0/1 (system pump) |
| `pump_b_status` | HR 107 | raw 0/1 (boiler pump) |
| `pump_c_status` | HR 108 | raw 0/1 (DHW pump) |
| `burner_run_time_hours` | HR 24-25 | hours, U32 |
| `burner_cycle_count` | HR 26-27 | cycles, U32 |
| `pump_a_cycles` | HR 686-687 | cycles, U32 |
| `pump_b_cycles` | HR 688-689 | cycles, U32 |
| `pump_c_cycles` | HR 690-691 | cycles, U32 |

Home Assistant generates entity IDs using the device name. Find the actual IDs
on the device page; the keys above are not guaranteed entity IDs.

Every sensor exposes `register` and `raw_value` attributes for diagnosis.
32-bit counters additionally expose `raw_words`, both received words in wire
order, so the provisional word order can be checked against the boiler.

## Interpretation and uncertainties

- **Raw codes are not translated.** The manual and the manufacturer's ProtoAir
  point list disagree about some enum numbering. Burner state, hold, lockout,
  demand source, heat-demand and pump-status values are shown exactly as
  received. No label implies a certainty the sources do not provide.
- **`0x7FFF` is treated as unavailable for temperature registers only.** That
  meaning is provisional: it appeared for sensors that were apparently absent,
  but the manufacturer has not confirmed it. It is never applied to codes,
  flags, fan speeds or counters.
- **32-bit word order is provisional.** Live samples returned `(0, 23)` for
  burner run time and `(0, 284)` for burner cycles. High-word-first makes those
  23 hours and 284 cycles, while low-word-first would produce implausible
  values around 1.5 million hours and 18 million cycles. The integration
  combines the first register as the high word. This is strong evidence from
  one boiler, not a manufacturer statement.
- **Temperature assignment is provisional.** The supply/return labels have not
  been independently confirmed against the boiler display, so no derived
  supply-return delta is published.

The integration is not a safety or limit control and does not replace the
boiler's controls. Do not use these measurements for safety-critical decisions.

## Tests

From the repository root:

```
python3 -B -m unittest discover -s tests -p 'test_ha_*.py' -v
```

The tests use fake Home Assistant and pymodbus layers; they do not touch a
boiler or a gateway. They assert the exact requested addresses and quantities,
the 32-bit counter values and word order, partial and failed-block behaviour,
and the absence of write calls.

## HACS / repository metadata

Repository metadata and installation instructions live in the repository root.

## Limitations

- An isolated Home Assistant 2026.10.1 instance successfully set up the
  integration and read the real boiler through a TCP gateway. The expanded
  diagnostic entities are implemented from the manual and live read-only
  captures, but most automated unit tests use fake Modbus/HA layers.
- The supply/return assignment, the `0x7FFF` sentinel meaning, 32-bit word
  order, and burner/status enum labels remain provisional.
- The serial port must be visible to the Home Assistant process/container.
- The integration does not authenticate Modbus. Restrict network or serial
  access and enforce read-only behavior at the gateway if one is used.

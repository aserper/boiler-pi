# Verification record — 2026-11-13

## Automated checks

- 76 integration/decoder unit tests and 39 pure RTU parser tests pass.
- `tests/wire_smoke.py` uses actual PyModbus 3.13.1 with a loopback MBAP simulator. It verifies all eight function-03 request ranges, 23 decoded readings, high-word-first counters and isolation of a failed pump-status block. It never contacts hardware.
- `tests/ha_smoke.py` runs actual Home Assistant 2026.10.1 and PyModbus 3.13.1 against a loopback simulator in a temporary config directory. It verifies config flow, 23 sensor entities, a genuinely scheduled 10-second poll, failed-read unavailability, recovery, options-triggered reload to 15 seconds and unloading/connection cleanup. The entire run is bounded at 40 seconds. Run it separately from the fake-module unit tests.

```sh
python3 -B -m unittest test_alta_protocol
python3 -B -m unittest discover -s tests -p 'test_ha_*.py'
uv run --no-project --with pymodbus==3.13.1 --with pyserial==3.5 python -B tests/wire_smoke.py
uv run --no-project --python 3.14 --with homeassistant==2026.10.1 --with pymodbus==3.13.1 --with pyserial==3.5 python -B tests/ha_smoke.py
```

## Real boiler and installed Home Assistant

An isolated actual Home Assistant 2026.10.1 instance completed config flow, created 23 entities from real boiler reads through the Pi gateway, changed polling options and unloaded successfully. Version 0.2.0 was then installed through HACS in the household instance and all 23 entities were confirmed present.

One active-heating observation showed: burner code 8, demand source 2, CH1 demand 1, CH2 and DHW demand 0, flame signal 60%, fan demanded/feedback 5057/5117 rpm, pump A/B statuses 1 and pump C 0, hold/lockout codes 0. This agrees with the ALTA manual's central-heat-1 interpretation, but does not establish which physical floor uses CH1. No thermostat calls were changed for the test.

Observed counter words `(0, 23)` for burner hours and `(0, 284)` for burner cycles support high-word-first interpretation. Subsequent live reads showed the burner cycle count increasing to 286. Word order remains provisional pending independent confirmation. The original captures also contain pump counts A/B/C 267/409/24, later increasing to 269/411/24.

Registers follow the ALTA Extended Features Instructions 112401-01, table 4-4. HR numbers are used as literal PDU addresses based on captures. Temperature sentinel `0x7fff` remains provisional; raw scalar values and raw counter words remain visible as entity attributes. Burner and demand-source codes are deliberately left numeric in the integration because manufacturer sources disagree about some enum labels.

No boiler writes, reset commands or operating-parameter changes were performed. Negative write-rejection tests of the gateway used simulated devices only. Software permissions and network restrictions are not a hardware safety interlock.

## Dashboard

A separate `Burnham ALTA` dashboard was created through Home Assistant's Lovelace API, without replacing any existing dashboard. Its entity IDs were resolved through the config-entry/entity registry, not guessed from names. The native Sections layout was visually inspected in an authenticated, isolated browser at desktop and phone widths. It shows actual readings, demand and pump flags, raw state/fault codes, lifetime counters and recorder temperature history. On/off displays only accept documented 0/1 values; missing diagnostics are not rendered as normal. The phone layout stacks sections. Exact reference-image styling is approximated with native cards, not reproduced pixel-for-pixel.

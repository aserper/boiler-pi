# Burnham ALTA for Home Assistant

**Experimental.** This is a local, read-only Home Assistant integration for ALTA boilers using direct Modbus RTU or a Modbus TCP gateway. It has been tested against a real boiler through a TCP-to-RTU gateway in Home Assistant 2026.10.1.

## Safety

The boiler can accept commands that change its operation. This project must not transmit Modbus write or reset commands. In particular, a gateway that passes arbitrary Modbus traffic exposes the boiler to writes from other clients, even when Home Assistant is configured to use only sensors. Modbus TCP is unauthenticated; restrict access and use a gateway that demonstrably enforces read-only operation. No gateway is currently recommended or deployed by this project.

Read [`docs/field-notes.md`](docs/field-notes.md) for wiring references, captured frames, and the remaining register uncertainties. This software is not a safety or limit control and does not replace the boiler's existing controls.

## Installation (experimental)

Add `https://github.com/aserper/boiler-pi` as a custom integration repository in HACS, install **Burnham ALTA**, and restart Home Assistant. Add the integration under **Settings → Devices & services**. Choose Modbus TCP for a translating gateway or serial RTU for a directly attached adapter. The Pi gateway tested here uses unit 1, port 1502. A transparent serial TCP stream is not Modbus TCP.

The integration exposes 23 read-only sensors: target, supply, return, DHW and stack temperatures; burner and demand-source codes; DHW/CH1/CH2 demand; pump statuses; flame signal; requested and actual fan speed; hold and lockout codes; burner runtime and cycle counts; and pump cycle counts. Demand and pump flags are reported as raw 0/1 values. `0x7fff` is provisionally treated as unavailable for temperatures. Counter word order and register-to-sensor assignments remain subject to validation against the boiler. No control entities are provided.

Polling defaults to every 10 seconds. Use the integration's **Configure** dialog to choose a different interval in whole seconds, from 5 to 3600. The option is stored with the config entry; existing entries keep the 10 second default until it is changed, and Home Assistant reloads the entry when the option changes.

The reference-styled dashboard is in [`docs/dashboard-styled.json`](docs/dashboard-styled.json), with [installation notes](docs/dashboard-notes.md). It uses button-card, card-mod and apexcharts-card for the custom layout; the native-card fallback remains in `docs/dashboard-ready.yaml`. Replace the placeholder entity IDs with those assigned by your installation. The [verification record](docs/verification.md) distinguishes automated tests, live boiler checks and provisional interpretations.

The **Configure** dialog also lets you name the CH1, CH2 and DHW consumers. These are display aliases only: existing entity IDs and boiler settings are unchanged. The styled dashboard reads the names automatically. Confirm the physical CH1/CH2 zone wiring before assigning floor names.

## Development

The pure response decoder can be exercised without hardware or dependencies:

```sh
python3 -B -m unittest -v test_alta_protocol
python3 -B -m unittest discover -s tests -p 'test_ha_*.py' -v
```

Observed register mappings and unavailable-value handling are provisional until validated against further boiler data and other firmware. Do not use these measurements for safety-critical decisions.

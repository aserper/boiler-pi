# ALTA field observations

## Safety

This project is read-only. Do not send Modbus writes, reset commands, or change boiler settings. The boiler accepts unauthenticated writes; a general-purpose network gateway can expose those writes to other clients even if this integration only reads. A raw serial TCP stream is not Modbus TCP. Do not deploy a gateway without restricting access and verifying its behavior.

## Pi and adapter

On 2026-11-13, a Raspberry Pi identified an FTDI FT232R (`0403:6001`) using `ftdi_sio`, at `/dev/ttyUSB0`. A stable adapter-specific path was also available under `/dev/serial/by-id/`. The operator account had access, and `fuser` and `lsof` reported no user of the port at the time. Kernel messages showed intermittent undervoltage, including near USB attachment; investigate the power supply before depending on the Pi.

## Manufacturer documentation

The [ALTA Extended Features Instructions, 112401-01](https://www.afsupply.com/pub/media/catalog/product/matridtech/burnham/manual/ALTA1201G02_2.pdf), tables 4-1 through 4-4, specify MB2 on RJ45 pin 1 D+, pin 2 GND, pin 3 D−; 38400 baud, no parity, RTU and function 03, with default address 1. Pin 7/8 are MB3 and must not be used for the EMS interface. Verify by pin number rather than relying on cable colors or adapter A/B naming. The document does not state the stop-bit count; 8-N-1 worked in the observation below.

## Read-only captures

After the owner confirmed the wiring was checked, these individual RTU FC03 requests were sent to unit 1 at 38400 8-N-1. Both responses have valid CRC-16/Modbus checksums:

```text
TX 01 03 00 0f 00 02 f4 08
RX 01 03 04 7f ff 00 79 12 35
TX 01 03 00 0d 00 08 d5 cf
RX 01 03 10 00 01 00 8c 7f ff 00 79 00 78 7f ff 00 00 00 5c 45 5f
```

The second response maps wire addresses 13–20 to `1, 140, 32767, 121, 120, 32767, 0, 92`. These align plausibly with the manual's HR 13 burner state, HR 14 active setpoint, HR 16 supply, HR 17 return, HR 18 DHW and HR 20 stack temperature. **This suggests that manual HR numbers are the literal PDU addresses on this boiler**, not HR minus one. Plausibility alone is not conclusive: independently compare against the boiler's display, and validate other models/firmware before assuming the convention universally. `0x7fff` may mean an unavailable reading, but its semantics are not yet confirmed. Do not label the boiler temperatures as verified measurements until compared with a display.
The owner subsequently reported the boiler display showed 120, without a sensor label. This is consistent with the two observed water temperatures but does not independently identify which register is supply versus return.

The manufacturer describes these temperatures in °F. A third-party gateway's register map may differ from the boiler's native addresses. The ALTA manual and the manufacturer's ProtoAir point list disagree about some enum numbering; do not infer state labels from the latter without validation.

## Network framing

[Home Assistant Modbus documentation](https://www.home-assistant.io/integrations/modbus/) distinguishes Modbus TCP (`tcp`, MBAP framing, protocol translation) from RTU over TCP (`rtuovertcp`, transparent RTU framing with CRC). They are not interchangeable. No gateway has yet been selected, deployed, or tested against Home Assistant. Neither protocol authenticates clients; restrict network access and enforce read-only behavior at the gateway if used.

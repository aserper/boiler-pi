# Register notes

The integration uses function 03 only. Addresses below are the manual's HR numbers and are sent directly as Modbus PDU addresses. Do not subtract one. This address convention was checked against replies from the tested boiler; a commercial gateway with its own point map may use different addresses.

| Reading | HR address | Value |
| --- | --- | --- |
| DHW demand | 3 | 0 off, 1 on |
| CH1 / CH2 demand | 9 / 10 | 0 off, 1 on |
| Burner state | 13 | Code |
| Active target | 14 | Whole degrees F |
| Supply / return temperature | 16 / 17 | Whole degrees F |
| DHW temperature | 18 | Whole degrees F |
| Stack temperature | 20 | Whole degrees F, exhaust gas |
| Burner runtime | 24-25 | Unsigned 32-bit hours |
| Burner cycles | 26-27 | Unsigned 32-bit count |
| Hold / lockout | 28 / 29 | Code |
| Demand source | 100 | Code |
| Flame signal | 101 | Percent |
| Fan requested / actual speed | 103 / 104 | RPM |
| Pump A / B / C status | 106 / 107 / 108 | 0 off, 1 on |
| Pump A / B / C cycles | 686-687 / 688-689 / 690-691 | Unsigned 32-bit counts |

Each poll makes eight short reads. If a block fails, its sensors become unavailable. A transport failure closes the connection so the next poll can reconnect.

## Values that need care

- Temperatures use signed 16-bit values. The integration treats `0x7fff` as unavailable because that value appeared on unused temperature inputs. The manual does not define this marker.
- Counters use the first word as the high word. For example, `(0, 23)` becomes 23 hours. This matches the captured values, but the manual does not specify word order. Counter entities expose both words in `raw_words`.
- Supply and return labels follow the manual. They have not been individually checked against a labeled boiler display.
- State codes remain numeric on the integration's sensors. The styled dashboard uses labels from the ALTA manual, but the manufacturer's ProtoAir point list disagrees on some enum values. Check the raw code if a label looks wrong.
- CH1 and CH2 are boiler demand inputs. An external zone controller can combine several thermostats into one input.

## Reference and tests

[ALTA Extended Features Instructions, 112401-01](https://www.afsupply.com/pub/media/catalog/product/matridtech/burnham/manual/ALTA1201G02_2.pdf), tables 4-4 and 4-5.

A captured read of addresses 13 through 20 returned `1, 140, 32767, 121, 120, 32767, 0, 92`. The CRC-valid RTU frames are fixtures in `test_alta_protocol.py`. The tests also cover missing blocks, counter decoding, connection cleanup and read-only requests.

Run the unit tests from the repository root:

```sh
python3 -B -m unittest test_alta_protocol
python3 -B -m unittest discover -s tests -p 'test_ha_*.py'
```

`tests/wire_smoke.py` tests actual PyModbus framing against a local simulator. `tests/ha_smoke.py` tests actual Home Assistant 2026.10.1 setup, scheduled polling, failure recovery, options and unloading. Both use simulated devices and have bounded runtimes; their docstrings list the commands and dependencies. Neither connects to a boiler.

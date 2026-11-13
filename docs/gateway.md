# Using a Raspberry Pi as the gateway

Connect the USB RS485 adapter to the Pi. Home Assistant then connects to the Pi over the network:

```text
ALTA MB2 pins 1, 2, 3
        |
     RS485 adapter
        |
       USB
        |
    Raspberry Pi
    Modbus TCP gateway
        |
       LAN
        |
   Home Assistant
```

Use a gateway that translates Modbus TCP to Modbus RTU. `ser2net` or `socat` forwarding serial bytes does not provide this translation, and the integration's TCP mode will not understand that stream.

## Pi gateway settings

This setup has been tested with [womat/modbusgateway](https://github.com/womat/modbusgateway) 3.0.5, built with Go 1.27.4. Follow that project's installation instructions. These are the relevant sections of its configuration, not a complete config file:

```yaml
buses:
  boiler:
    type: rtu
    timeout: 1500ms
    rtu:
      port: /dev/serial/by-id/YOUR_ADAPTER
      baudRate: 38400
      dataBits: 8
      parity: N
      stopBits: 1

devices:
  boiler:
    bus: boiler
    unitId: 1
    functions: [FC3]
    cacheTTL: 0s
    gateway: {unitId: 1}

listen:
  tcp:
    host: YOUR_PI_LAN_ADDRESS
    port: 1502
```

Replace the adapter path and LAN address. Use the boiler's actual unit ID if it differs from 1. Configure the gateway's separate HTTPS management service with its own certificate and token file, and bind it to `127.0.0.1`.

Keep `functions: [FC3]` on the boiler device. Do not add another device entry or serial service that can bypass it. Modbus TCP has no authentication; restrict port 1502 to Home Assistant with a firewall where possible, and never expose it to the internet. Commercial gateways may not support function-code filtering.

After changing the gateway config, stop and start the service. A failed reload can leave the previous config running. Confirm that the running gateway reports only FC3 for the boiler. Test write rejection with a simulator, never with the boiler connected.

In Home Assistant, choose **Modbus TCP**, enter the Pi's LAN address, port `1502` and unit ID `1`.

## If it stops responding

On the Pi, check that the adapter is listed under `/dev/serial/by-id/` and that the gateway service is running. The USB device must be accessible to the service account, and no other program should open it.

Repeated undervoltage messages in `journalctl -k` point to a power-supply or cable problem. Fix the power before relying on the gateway. If the Pi reboots, the gateway should start automatically; enable its systemd service when you install it.

# Read-only gateway research

The Pi gateway is [`womat/modbusgateway`](https://github.com/womat/modbusgateway), version 3.0.5, source commit `d358c411cc67b7873f6b376b7f447a202d2ca1fb`, using a Modbus TCP listener and an RTU serial bus with a per-device `functions: [FC3]` allowlist. An independent source review found no route for write functions through that configuration, subject to the restrictions below. A live function-03 read has also succeeded through the gateway.

A preliminary isolated test with a simulated RTU device at 38400 8-N-1 observed an FC03 read reaching the simulated serial side; FC01, FC04, and FC06 were rejected. This is useful evidence, not a proof that all write paths are blocked. No write request was sent to the boiler. Further tests must remain isolated from the boiler.

Modbus TCP has no inherent authentication or encryption. An FC03-only filter is necessary but not sufficient: bind to the intended interface and restrict access to authorized hosts using an appropriate persistent firewall or equivalent network controls. Avoid exposing the gateway's management endpoint to the LAN. Do not assume a general-purpose gateway is read-only merely because the Home Assistant integration does not write.

Another candidate, `tspspi/modbusgw` version 0.0.5, should **not** be deployed relying on its `allow_write: false` setting: preliminary source inspection and simulated tests found that setting is not enforced. Its separate function-code routing filter may block writes under a carefully constrained configuration, but the setting's behavior is a serious footgun.

## Independent review and deployment constraints

The reviewed release binary used Go 1.27.3, which the review's binary vulnerability scan flagged for CVE-2026-97054; exploitability in the gateway was not established. Build the pinned source using patched Go 1.27.4 or newer, rather than deploy that release binary. The source tag was unsigned; release checksums alone do not prove publisher identity.

Use exactly one bus and one device with the literal allowlist `[FC3]`; do not create alternate paths or aliases to the same serial port. Configure a production TLS certificate and bind the management HTTPS API to loopback. Protect config, credentials and binary from modification. The owner has chosen to permit Modbus access from the local LAN initially, rather than a single Home Assistant IP; limit the listener and firewall accordingly. Modbus TCP clients on that LAN can still issue allowed reads and potentially exhaust connections.

Do not rely on SIGHUP to apply a more restrictive configuration: a failed reload can keep a previous configuration active. Stop and start the service with the reviewed configuration, and confirm the running device's effective permissions through its authenticated local API. Any negative write tests must use a simulator, never the boiler.

## Installation record

On 2026-11-13, the pinned source was cross-compiled for Linux ARM64 using Go 1.27.4, transferred to the Pi, and verified against SHA-256 `039b8010a1d784af84465fedba4d0349eab2027a70384657b525bfcda5f320c1`. Service `boiler-modbus-gateway.service` runs with a dedicated unprivileged account, an FC03-only device, a LAN-bound TCP listener, and an isolated nftables input chain restricting that port to the local subnet. Its separate HTTPS management listener is loopback-only with a locally generated certificate and a root-protected token. The certificate needs renewal after 365 days. The service's root-owned configuration is `/etc/boiler-gateway/config.yaml`; restart, rather than SIGHUP, after reviewing changes.

An authenticated local `/devices` query reported one connected RTU device with `functions: [FC3]`. A remote Modbus TCP function-03 read of addresses 13–20 returned a valid MBAP response with plausible boiler data. This verifies real network-to-serial reads, not Home Assistant integration behavior or a hardware-enforced write block. No writes were tested against the boiler. The owner accepted access from the local LAN; any host on that LAN can issue reads and could consume gateway resources.

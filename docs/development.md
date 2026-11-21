# Build and implementation notes

The target is big-endian MIPS/o32, MIPS I instructions, soft-float, statically linked against NetBSD 4.0 sgimips libraries. Apple's firmware is NetBSD-based; Linux is used only as the Docker build environment. The resulting executables are **not Linux programs**.

`prepare` uses a digest-pinned Debian base image, Debian's MIPS cross-compiler, and checksum-pinned NetBSD headers/libraries. Package updates can change the compiled executable bytes; the native binaries are not claimed to be bit-reproducible across compiler versions. The small assembled patch and complete firmware output **are** checked against the exact reviewed hashes. GNU gzip is required for the byte-identical firmware roundtrip.

The native MQTT topic and discovery ID are compiled from the validated `--device-id`; there is no shared installation-specific MAC address or MQTT identity in the source. MQTT credentials are supplied at install/configure time and live only in the device's private configuration file. A second AirPort must have a different stable device ID.

| Component | Purpose |
|---|---|
| `airport_bose.py`, `lib/` | Host-side build, prompts, checks, backups, installation and recovery |
| `native/airport-pl2303-match.S` | 88-byte matcher, padded to the existing 120-byte kernel function region |
| `native/airport-pl2303-configure.c` | Descriptor-checked PL2303 control transfers, 1200/8N1, raw bulk I/O |
| `native/airport-bose-command.c` | Exclusive serial access, bounded command exchanges, acknowledgements |
| `native/airport-bose-mqtt.c` | Outbound MQTT connection, HA discovery, serial worker and estimates |
| `native/airport-adapter-check.c` | Descriptor-only pre-install check; no serial output |
| `native/airport-verify-pl2303-kernel.c` | Read-only verification of the running function bytes |
| `vendor/` | MIT-licensed ACP and UFS reader from the pinned upstream revision |

The patch uses the existing `ulpt` driver as a narrow bulk transport. It does not provide `/dev/ttyU0`, termios or general-purpose FTDI/CP210x/CH340 support. USB printer matching is replaced. The application's raw USB control requests configure this adapter directly.

Image preparation verifies the original firmware SHA, the inflated bank SHA, a byte-identical unmodified roundtrip, the matcher SHA, all 1,664 filesystem entries, all changed byte ranges, both container checksums, gzip integrity and the final image SHA. The final candidate is:

```text
be4f5d4b67f4fce3a1c789c61fe794b8a12a7ea161e4a7cb0f969fe251f9a164
```

Host-side unit tests use fake transports and synthetic flash banks. They do not flash a device. Native tests use a socket pair to simulate the Bose and inject ACKs, NAKs, silence and missing replies. The automated build can reproduce the firmware locally, but no CI job controls live hardware.

```sh
python3 -m unittest discover -s tests -p 'test_host.py' -v
python3 tests/test_native.py
python3 airport_bose.py prepare --device-id test_fixture
```

Keep all downloaded firmware, private device backups, credentials and build outputs under `.local/`. Never add them with `git add -f`. Do not use the native loopback or old-exit diagnostic modes with a Bose connected; they are retained as explicit SSH diagnostics, not MQTT commands.

# Serial protocol and observed quirks

The reference is [Bose CD20 Serial Control Rev. 4](https://id.scribd.com/document/58620095/CD20-Serial-Control-Rev4). The tested music centre is a Lifestyle 25; its VFD display is dead, so audible checks and its working physical remote were essential.

The adapter uses **1200 baud, 8N1**, with hardware/software flow control disabled. Its configuration is read back. The three-wire cable does not use DTR or RTS. The software sets those controls low and never uses the adapter's power pin to power the Bose.

A command session:

1. Send `Bose` to synchronise into Idle and wait for the half-duplex response to finish.
2. Send `TAP\r` to enter Test Access Port mode.
3. Send `a`, wait for `X`; send `A 01 40` to select Zone 1, wait for `X`. The zone byte is binary `01`, not ASCII `1`.
4. Send `a`, wait for `X`; send the three-byte remote-simulation command, wait for `X`. `W` means rejection.
5. For volume only, perform five individually acknowledged remote steps. Stop on any failure; never retry an uncertain step.
6. Send `Bose`, allow its response to finish, and close USB.

Examples: power toggle `E@@`; AUX `EII`; Radio `EKK`; volume up `EA@`; volume down `EE@`.

Two hardware findings matter:

- The documented `axxx` TAP exit caused audio to cut out on this unit. Closing USB without that exit did not. The documented `Bose` return-to-Idle sequence preserved stable AUX/Radio audio, and is used in normal operation.
- Five `EEE` volume-down commands received ACKs but were not audibly effective in testing. Changing the documented unused filler byte to `@` (`EE@`) was audibly effective. Both final HA volume buttons were confirmed working. The internal reason for the filler-sensitive behaviour is not known.

During `Bose` cleanup the device may emit three `W` characters before recognising the complete escape and printing its banner. These do not invalidate a command acknowledgement already received; parsing is scoped to the command phase.

## What we can report

`bose_acknowledged: true` means the serial command interface accepted the command. It is not a measured power state or a guarantee that the expected audio was heard. No documented query for actual power, volume or mute was found. A passive test during front-panel actions produced no serial feedback. `*` is documented for reset/power-up, not normal standby.

The MQTT `state` object therefore marks `estimated: true` and `feedback_supported: false`:

- An acknowledged source command estimates power on and the selected source.
- Power toggle flips a known estimate. Toggling from unknown stays unknown.
- `fm` is recorded as `radio`, since the same key can toggle AM and FM.
- Reconnect, adapter loss, unconfirmed command, startup or 20 minutes without an estimate update can set it to unknown.
- The physical remote and front-panel controls are outside this estimate. No numerical volume or mute state is invented.

## MQTT delivery and access

The bridge connects outbound to an RFC1918 IPv4 address using plain MQTT 3.1.1. Use a trusted LAN and a broker account permitted to publish this device's discovery/state/result topics and subscribe to its command topic and `homeassistant/status`. There is no TLS implementation in this release.

Commands use QoS 0 and must be **non-retained**. The bridge rejects messages marked retained or duplicate, clears its bounded queue on disconnection, and never retries serial commands. MQTT 3.1.1 can clear the retained flag when forwarding a newly published retained message to an existing subscriber, so publishers must still use `retain: false`.

Availability reports the broker connection and USB adapter, not Bose power or cable health. Fourteen buttons are advertised; power/source/volume were the focus of audible testing. Do not infer that every CD transport or other-input function has been separately exercised.

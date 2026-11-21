# Recovery and what installation changes

## Before installing

The supported firmware is specifically the A1392/model 115 version 7.6.2 image whose SHA-256 is:

```text
995c630b774c0d2f1eabe50f13188068bfe7eb73bd2d1af0411ec07b72ed3e57
```

The installer accepts a primary bank containing that stock image or this project's exact patched image. It requires a secondary bank containing the supported stock payload. Unknown versions or modifications stop installation. Do not edit the hash checks to force another version through.

All five raw flash partitions are saved under `.local/recovery/<timestamp>/` before installation. These include device configuration and secrets. Keep an offline copy; **never publish the backups or attach them to an issue**. The installer records hashes for your own device, rather than reusing another owner's backup hashes.

The patch changes the primary firmware bank only, through ACP command 3. There are no raw flash writes and no writes to the bootloader, configuration partitions or secondary firmware bank. A normal installation writes native programs and private settings to `/mnt/Flash`, the persistent filesystem, as well as enabling the bridge there. Enabling SSH separately changes `dbug` and saves its previous value.

## A reset button is not an unbricking guarantee

The original device successfully rebooted after installation, with USB disconnected. That is useful evidence, not a guarantee for every unit. A checksum-valid image that hangs may not trigger automatic fallback. Internal UART recovery has not been established for this project. A factory reset resets configuration; it does not necessarily replace a broken firmware image.

Leave power connected during upload and verification. After any interruption or uncertain upload, inspect the private `flash-result.json` and read both banks before attempting another write or reboot. The tool never retries a flash or automatically reboots after an error. A failed install may leave the bridge disabled (`bose.off`) to avoid starting a partly installed program set.

## Disable or repair the MQTT bridge

The firmware checks these files:

| File | Meaning |
|---|---|
| `/mnt/Flash/bose.enable` | Enables this project's printer-service workaround and startup hook |
| `/mnt/Flash/bose.off` | Prevents bridge startup |
| `/mnt/Flash/bose.try` | Startup marker, cleared after MQTT authentication |

If a startup marker survives to the next boot, it is renamed to `bose.off` and the bridge is not started. A broker outage across reboots can trigger this too. `configure-mqtt` replaces the credentials, clears `bose.off`/`bose.try`, restarts the service and checks authentication.

To prevent startup manually over an existing root SSH session:

```sh
touch /mnt/Flash/bose.off
```

This affects the next launch; it doesn't kill a currently running process. The bridge runs as `/mnt/Flash/bose-mqtt`. Its diagnostic log is `/mnt/Memory/bose-mqtt.log`. Do not share its private configuration file. Removing `bose.off` and `bose.try` re-enables launch, provided `bose.enable` remains present.

USB printer sharing is deliberately unavailable while using the patch. The startup workaround unlinks the RAM filesystem's printer-service directory entries and replaces them with no-op symlinks. It **never overwrites** the original hardlinked Apple executable.

## Restore stock firmware while SSH still works

Use the same checkout, private work directory and device ID used during installation:

```sh
python3 airport_bose.py restore-stock --host 192.168.1.50 --device-id living_room
```

This verifies the current profile, requires USB to be disconnected, makes fresh private backups, disables the bridge, writes the pinned stock image to the primary bank, verifies the complete payload and unchanged secondary bank, and asks before rebooting. It preserves Wi-Fi settings and leaves the native files available in `/mnt/Flash`; the stock firmware has no hook to start them.

This path depends on SSH and ACP still being reachable. It is not a recovery method for an already unbootable AirPort. The restore workflow is tested with simulated transports and offline images; it has not been exercised as a live stock-restoration cycle on the working unit.

SSH remains enabled after stock restoration because its setting lives separately. The `ssh-*/before.json` file records the original `dbug` value and an SSH-off value. The firmware's `0x200` bit disables SSH; restoring an originally unset property by setting a value is not an exact deletion. Keep access local and consult the upstream AirPort tools if restoring that setting.

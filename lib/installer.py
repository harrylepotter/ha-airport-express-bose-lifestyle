"""Interactive installation. Device writes only follow explicit, concrete review."""
from pathlib import Path
import time

from . import build, config
from .common import ROOT, confirm, report, require, sha
from .device import SSH, backup, connection_identity, flash_verified
from .firmware import check_bank, payload, SOURCE_SHA, FINAL_SHA


def check_profile(primary, secondary, stock, candidate):
    require(sha(stock) == SOURCE_SHA and sha(candidate) == FINAL_SHA, 'Unreviewed firmware input')
    check_bank(primary); check_bank(secondary, payload(stock))
    length = check_bank(primary)['image_bytes']
    require(primary[:length] in (payload(stock), payload(candidate)),
            'Primary bank is neither the supported stock image nor this project’s patch. No write permitted.')
    return primary[:length] == payload(candidate)


def processes(ssh):
    result = []
    for line in ssh.run('/bin/ps -ax').stdout.decode(errors='replace').splitlines():
        fields = line.split()
        if fields and fields[0].isdigit() and '/mnt/Flash/bose-mqtt' in fields:
            result.append(int(fields[0]))
    return result


def stop_service(ssh):
    pids = processes(ssh)
    for pid in pids:
        ssh.run(f'/bin/kill -TERM {pid}')
    deadline = time.monotonic() + 25
    while processes(ssh):
        require(time.monotonic() < deadline, 'MQTT daemon has not exited; do not replace configuration while it is active')
        time.sleep(1)


def start_service(ssh):
    ssh.run('/bin/rm -f /mnt/Flash/bose.off /mnt/Flash/bose.try; '
            '/bin/sh /mnt/Flash/bose.start </dev/null >/mnt/Memory/bose-mqtt.log 2>&1 &')


def verify_service(ssh):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        log = ssh.run('/bin/cat /mnt/Memory/bose-mqtt.log', check=False).stdout
        if processes(ssh) and b'MQTT authenticated; discovery and commands enabled' in log:
            print('MQTT daemon is running and authenticated. Discovery published; no Bose command sent.')
            return
        time.sleep(2)
    raise RuntimeError('MQTT startup not verified. Inspect the private device log; no serial command was sent by setup.')


def install(args, password):
    identifier = args.device_id
    output = args.work_dir / 'build' / identifier
    if not (output / 'manifest.json').exists():
        build.prepare(args.work_dir, identifier, args.docker_context)
    manifest = build.verify_build(output)
    stock = (args.work_dir / 'downloads/stock-7.6.2.basebinary').read_bytes()
    candidate = (output / 'patched.basebinary').read_bytes()
    with SSH(args.host, args.work_dir) as ssh:
        client, identity = connection_identity(ssh, password)
        primary, secondary = ssh.read('/dev/rflash0.raw'), ssh.read('/dev/rflash1.raw')
        already = check_profile(primary, secondary, stock, candidate)
        mqtt = config.prompt()
        config.authenticate(ssh, mqtt)
        ssh.upload('/mnt/Memory/bose-adapter-check', (output / 'adapter-check').read_bytes(), True)
        confirm('Disconnect the Bose serial cable. Connect ONLY the USB adapter to the AirPort for descriptor inspection.\n'
                'This test sends no serial bytes. The required adapter is detailed in docs/hardware.md.', 'ADAPTER READY')
        inspected = ssh.run('/mnt/Memory/bose-adapter-check --compatible')
        print(inspected.stdout.decode().strip())
        confirm('Unplug the USB adapter from the AirPort. Leave the AirPort powered on.\n'
                'The first patched boot must be performed with USB disconnected.', 'USB UNPLUGGED')
        ssh.run('/mnt/Memory/bose-adapter-check --absent')
        recovery = args.work_dir / 'recovery' / time.strftime('%Y%m%d-%H%M%S')
        backup(ssh, recovery, identity)
        # A second read prevents a firmware/configuration tool racing the initial checks.
        require((recovery / 'flash0.raw').read_bytes() == primary and
                (recovery / 'flash1.raw').read_bytes() == secondary,
                'A firmware bank changed during preflight; no installation attempted')
        check_profile((recovery / 'flash0.raw').read_bytes(), (recovery / 'flash1.raw').read_bytes(), stock, candidate)
        print(f'\nTarget: A1392, 7.6.2, {args.host}\nMQTT: {mqtt["server"]}:{mqtt["port"]}\n'
              f'Topic: bose/{identifier}\nPrivate backup: {recovery}\nImage SHA-256: {sha(candidate)}')
        confirm('Install the native controller and private MQTT configuration, and patch ONLY the primary firmware bank.\n'
                'USB printer sharing will be disabled. The stock secondary bank stays untouched.\n'
                'A bad flash can make the AirPort unbootable; a reset button is NOT a guaranteed firmware recovery.\n'
                'See docs/recovery.md. Keep power connected throughout writing and verification.', 'INSTALL')
        # Flash is small: stage each file separately; do not make duplicate flash backups here.
        free = ssh.run('/bin/df -k /mnt/Flash').stdout.decode().splitlines()[-1].split()
        minimum_free = 200 if already else 400
        require(len(free) >= 4 and int(free[3]) >= minimum_free,
                f'At least {minimum_free} KiB free on /mnt/Flash is required; do not delete unrelated files')
        stop_service(ssh)
        ssh.run('umask 077; : > /mnt/Flash/bose.off')
        for name in ('bose-command', 'bose-mqtt'):
            ssh.upload('/mnt/Flash/' + name, (output / name).read_bytes(), True)
        for remote, local in (('bose-prestart', 'airport-bose-prestart.sh'),
                              ('bose-printer-stub', 'airport-bose-printer-stub.sh'),
                              ('bose.start', 'airport-bose-start.sh')):
            ssh.upload('/mnt/Flash/' + remote, (ROOT / 'native' / local).read_bytes(), True)
        ssh.upload('/mnt/Flash/bose-mqtt.conf', config.encode(mqtt))
        ssh.run('umask 077; : > /mnt/Flash/bose.enable; /bin/sync')
        if not already:
            flash_verified(ssh, client, candidate, recovery)
        else:
            print('Primary already contains the exact patch; no firmware write needed.')
        ssh.run('/bin/rm -f /mnt/Flash/bose.off /mnt/Flash/bose.try; /bin/sync')
        report(recovery / 'installation.json', {'host': args.host, 'device_id': identifier,
               'firmware_sha256': sha(candidate), 'build': manifest, 'status': 'Installed and read back; reboot pending'})
        confirm('Installation verified. USB must remain unplugged. Reboot the AirPort now?', 'REBOOT')
        ssh.run('/mnt/Memory/bose-adapter-check --absent')
        client.reboot()
    print('Reboot requested. Allow up to five minutes. Then run:\n'
          f'  python3 airport_bose.py status --host {args.host} --device-id {identifier}\n'
          'After status passes, connect the adapter and follow docs/hardware.md before connecting the Bose.')


def configure(args, password):
    with SSH(args.host, args.work_dir) as ssh:
        connection_identity(ssh, password)
        require(ssh.run('[ -x /mnt/Flash/bose-mqtt ] && [ -f /mnt/Flash/bose.enable ]', check=False).returncode == 0,
                'Install this project before configuring MQTT')
        mqtt = config.prompt()
        config.authenticate(ssh, mqtt)
        confirm(f'Replace the private MQTT configuration with broker {mqtt["server"]}:{mqtt["port"]} and restart only the bridge?', 'CONFIGURE')
        stop_service(ssh)
        ssh.upload('/mnt/Flash/bose-mqtt.conf', config.encode(mqtt))
        start_service(ssh)
        verify_service(ssh)


def status(args, password):
    output = args.work_dir / 'build' / args.device_id
    build.verify_build(output)
    with SSH(args.host, args.work_dir, args.ssh_control) as ssh:
        if password:
            _, identity = connection_identity(ssh, password)
            print(f'Model {identity["syAP"]}, firmware {identity["syVs"]}')
        # RAM upload for a strictly read-only kernel check; no USB or Bose output.
        ssh.upload('/mnt/Memory/bose-kernel-check', (output / 'kernel-check').read_bytes(), True)
        print(ssh.run('/mnt/Memory/bose-kernel-check').stdout.decode().strip())
        verify_service(ssh)
        print(ssh.run('/bin/df -k /mnt/Flash').stdout.decode().strip())


def restore(args, password):
    stock = build.download(build.STOCK_URL, args.work_dir / 'downloads/stock-7.6.2.basebinary', SOURCE_SHA).read_bytes()
    with SSH(args.host, args.work_dir) as ssh:
        client, identity = connection_identity(ssh, password)
        # Only restore over a recognized project image, preserving the stock recovery bank.
        primary, secondary = ssh.read('/dev/rflash0.raw'), ssh.read('/dev/rflash1.raw')
        check_bank(secondary, payload(stock))
        # Candidate must have been prepared and verified locally before recovery writes.
        output = args.work_dir / 'build' / args.device_id
        build.verify_build(output)
        check_profile(primary, secondary, stock, (output / 'patched.basebinary').read_bytes())
        confirm('Unplug USB. Restore the pinned stock 7.6.2 image to the primary bank and disable this bridge?\n'
                'Wi-Fi/settings and private native files are kept; the secondary bank stays untouched.', 'RESTORE STOCK')
        ssh.upload('/mnt/Memory/bose-adapter-check', (output / 'adapter-check').read_bytes(), True)
        ssh.run('/mnt/Memory/bose-adapter-check --absent')
        recovery = args.work_dir / 'recovery' / ('restore-' + time.strftime('%Y%m%d-%H%M%S'))
        backup(ssh, recovery, identity)
        stop_service(ssh)
        ssh.run('umask 077; : > /mnt/Flash/bose.off; /bin/sync')
        flash_verified(ssh, client, stock, recovery)
        confirm('Stock primary verified. Reboot with USB unplugged?', 'REBOOT')
        ssh.run('/mnt/Memory/bose-adapter-check --absent')
        client.reboot()

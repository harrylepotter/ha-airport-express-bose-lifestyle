#!/usr/bin/env python3
"""Set up a Bose MQTT controller on the narrowly supported AirPort Express A1392."""
import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys

if sys.version_info < (3, 12):
    raise SystemExit('Python 3.12 or newer is required.')

from lib.common import ROOT, device_id, lan_address


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'enable-ssh', 'install', 'status', 'configure-mqtt', 'restore-stock'])
    parser.add_argument('--host', type=lan_address, help='AirPort LAN IPv4 address')
    parser.add_argument('--device-id', type=device_id, default='living_room', help='Stable unique name, e.g. living_room (default)')
    parser.add_argument('--work-dir', type=Path, default=ROOT / '.local', help='Private downloads/builds/backups directory')
    parser.add_argument('--docker-context', help='Optional Docker context; defaults to the active context')
    parser.add_argument('--ssh-control', type=Path, help='Existing OpenSSH control socket (status only; avoids password prompt)')
    args = parser.parse_args()
    args.work_dir = args.work_dir.resolve()
    args.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.ssh_control and args.action != 'status':
        parser.error('--ssh-control is available only for status')
    if args.action == 'prepare':
        from lib.build import prepare
        prepare(args.work_dir, args.device_id, args.docker_context)
        return
    if not args.host:
        args.host = lan_address(input('AirPort LAN IPv4 address: ').strip())
    if args.action == 'enable-ssh':
        from lib.device import enable_ssh
        enable_ssh(args.host, args.work_dir)
        return
    from lib import installer
    if args.action == 'install':
        print('SSH must already be enabled. If needed, first run the enable-ssh action.')
    password = None if args.ssh_control else getpass.getpass('AirPort administrator password for device verification (hidden): ')
    if password == '':
        parser.error('An administrator password is required')
    {'install': installer.install, 'status': installer.status,
     'configure-mqtt': installer.configure, 'restore-stock': installer.restore}[args.action](args, password)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped. A completed change may remain applied; inspect the recovery journal before retrying.', file=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, EOFError) as error:
        print(f'STOPPED: {error}\nNo automatic flash retry or reboot. If a write began, inspect the private recovery journal.', file=sys.stderr)
        sys.exit(1)

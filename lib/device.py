"""OpenSSH transport, read-only inventory, private backups and guarded ACP flash."""
import getpass
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib

from .common import ROOT, confirm, lan_address, private_write, report, require, sha
from .firmware import FINAL_SHA, SOURCE_SHA, check_bank, payload, split

sys.path.insert(0, str(ROOT / 'vendor'))
from airportctl import acp

FLASH_SIZES = (7340032, 7340032, 1310720, 262144, 524288)
SSH_OPTIONS = ['-o', 'ConnectTimeout=10', '-o', 'HostKeyAlgorithms=+ssh-rsa',
    '-o', 'KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1,diffie-hellman-group-exchange-sha1',
    '-o', 'Ciphers=+aes128-cbc,aes256-cbc,3des-cbc', '-o', 'MACs=+hmac-sha1,hmac-md5',
    '-o', 'PubkeyAuthentication=no', '-o', 'PreferredAuthentications=password,keyboard-interactive']


class SSH:
    def __init__(self, host, work, existing=None):
        self.host = lan_address(host)
        self.work = Path(work).resolve()
        self.temp = None
        self.forwards = []
        self.existing = existing
        self.socket = str(existing) if existing else None

    def __enter__(self):
        if self.existing:
            self.control('check')
        else:
            self.temp = tempfile.TemporaryDirectory(prefix='airport-bose-')
            self.socket = str(Path(self.temp.name) / 'ssh')
            self.work.mkdir(parents=True, exist_ok=True, mode=0o700)
            known = self.work / 'known_hosts'
            print('SSH will ask you to confirm the host key and enter the AirPort administrator password.')
            subprocess.run(['ssh', '-F', '/dev/null', '-M', '-N', '-f', '-S', self.socket,
                '-o', 'ControlPersist=600', '-o', 'StrictHostKeyChecking=ask',
                '-o', f'UserKnownHostsFile={known}', *SSH_OPTIONS, f'root@{self.host}'], check=True)
        return self

    def control(self, operation, extra=()):
        return subprocess.run(['ssh', '-F', '/dev/null', '-S', self.socket, '-o', 'BatchMode=yes',
                               '-O', operation, *extra, f'root@{self.host}'], check=True, capture_output=True)

    def forward(self, target, port):
        target = lan_address(target)
        require(1 <= port <= 65535, 'Invalid port')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); local = sock.getsockname()[1]
        spec = f'127.0.0.1:{local}:{target}:{port}'
        self.control('forward', ('-L', spec))
        self.forwards.append(spec)
        return local

    def run(self, command, data=None, timeout=90, check=True):
        # Every command is a fixed script or uses validated numeric IDs/paths.
        result = subprocess.run(['ssh', '-F', '/dev/null', '-T', '-S', self.socket,
            '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', f'root@{self.host}', command],
            input=data, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f'AirPort command failed ({result.returncode}): {result.stderr.decode(errors="replace").strip()}')
        return result

    def read(self, path):
        require(re.fullmatch(r'/[A-Za-z0-9_./-]+', path), 'Unsafe remote path')
        return self.run('/bin/cat ' + path).stdout

    def upload(self, path, data, executable=False):
        require(re.fullmatch(r'/mnt/(Memory|Flash)/[A-Za-z0-9_.-]+', path), 'Unsafe upload destination')
        self.run(f'umask 077; /bin/cat > {path}.new', data)
        require(self.read(path + '.new') == data, f'Upload readback failed: {path}')
        mode = '700' if executable else '600'
        self.run(f'/bin/chmod {mode} {path}.new && /bin/mv {path}.new {path} && /bin/sync')
        require(self.read(path) == data, f'Installed readback failed: {path}')

    def __exit__(self, *_):
        for spec in self.forwards:
            try:
                self.control('cancel', ('-L', spec))
            except subprocess.CalledProcessError:
                pass
        if self.temp:
            try:
                self.control('exit')
            except subprocess.CalledProcessError:
                pass
            self.temp.cleanup()


class ACP:
    def __init__(self, host, password, port=5009):
        self.host, self.password = host, password
        acp.ACP_PORT = port

    def identity(self):
        device = acp.read_device(self.host, self.password)
        require(device.get('syAP') == 115 and device.get('syVs') == '7.6.2',
                f'Unsupported device: model {device.get("syAP")}, firmware {device.get("syVs")}. Only A1392/7.6.2 is supported; no automatic downgrade.')
        return device

    def reboot(self):
        failures = acp.reboot(self.host, self.password)
        require(not failures, 'AirPort rejected reboot')

    def flash(self, image):
        require(sha(image) in (SOURCE_SHA, FINAL_SHA), 'Refusing an unreviewed firmware image')
        with socket.create_connection((self.host, acp.ACP_PORT), timeout=10) as sock:
            sock.settimeout(180)
            sock.sendall(acp._compose(3, 0, self.password, image))
            header = acp._recv_exact(sock, acp.HDR.size)
            fields = acp.HDR.unpack(header)
            require(fields[0] == b'acpp' and fields[1] in (1, 0x30001) and fields[7] == 3, 'Unexpected flash reply')
            require(zlib.adler32(header[:8] + bytes(4) + header[12:]) == fields[2] & 0xffffffff,
                    'Invalid ACP header checksum')
            require(fields[8] == 0 and 0 <= fields[4] <= 4096, 'ACP flash rejected or invalid reply size')
            body = acp._recv_exact(sock, fields[4])
            require(zlib.adler32(body) == fields[3] & 0xffffffff, 'Invalid ACP body checksum')


def connection_identity(ssh, password):
    client = ACP('127.0.0.1', password, ssh.forward(ssh.host, 5009))
    identity = client.identity()
    version = ssh.run('/usr/bin/uname -a', check=False)
    if version.returncode:
        version = ssh.run('/bin/uname -a')
    require(b'AirPortFW-76200.16' in version.stdout, 'Unexpected running kernel version')
    return client, identity


def backup(ssh, directory, identity):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    manifest = {'host': ssh.host, 'device': identity, 'files': {}}
    for number, size in enumerate(FLASH_SIZES):
        print(f'Backing up flash partition {number}/4...', flush=True)
        data = ssh.read(f'/dev/rflash{number}.raw')
        require(len(data) == size, f'Incomplete flash{number} backup')
        if number < 2:
            check_bank(data)
        name = f'flash{number}.raw'
        private_write(directory / name, data)
        require((directory / name).read_bytes() == data, 'Local backup readback failed')
        manifest['files'][name] = {'bytes': size, 'sha256': sha(data)}
    report(directory / 'manifest.json', manifest)
    return directory


def verify_backup(directory, host):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    require(manifest['host'] == host, 'Backup belongs to a different target address')
    for i, size in enumerate(FLASH_SIZES):
        name = f'flash{i}.raw'; data = (directory / name).read_bytes()
        require(len(data) == size and sha(data) == manifest['files'][name]['sha256'], f'Backup changed: {name}')


def flash_verified(ssh, client, image, recovery):
    verify_backup(recovery, ssh.host)
    before = (Path(recovery) / 'flash0.raw').read_bytes()
    secondary = (Path(recovery) / 'flash1.raw').read_bytes()
    require(ssh.read('/dev/rflash0.raw') == before and ssh.read('/dev/rflash1.raw') == secondary,
            'A bank changed since backup; no upload attempted')
    split(image)
    journal = Path(recovery) / 'flash-result.json'
    record = {'image_sha256': sha(image), 'status': 'UPLOAD STARTING; do not reboot without verified readback'}
    report(journal, record)
    print('Writing the primary bank once. Keep power connected. No automatic retry.', flush=True)
    client.flash(image)
    after = ssh.read('/dev/rflash0.raw')
    private_write(Path(recovery) / 'flash0-after.raw', after)
    record['after'] = check_bank(after, payload(image))
    require(ssh.read('/dev/rflash1.raw') == secondary, 'Secondary bank changed; DO NOT REBOOT')
    record.update(status='PRIMARY VERIFIED; secondary unchanged; reboot not requested', secondary_unchanged=True)
    report(journal, record)
    print('Full primary payload/checksum verified; secondary bank is unchanged.')


def enable_ssh(host, work):
    host = lan_address(host)
    password = getpass.getpass('AirPort administrator password: ')
    require(password, 'Administrator password required')
    client = ACP(host, password)
    identity = client.identity()
    err, props = acp.get_props(host, password, ['dbug'])
    require(not err and len(props) == 1 and props[0][0] == 'dbug' and len(props[0][2]) == 4, 'Cannot read dbug')
    _, flags, raw = props[0]
    old = struct.unpack('>I', raw)[0]
    if flags & 1:
        require(old == 0xfffffff6, 'Unexpected dbug property error')
        old = None
    target = ((old or 0) | 0x3000) & ~0x200
    output = Path(work) / ('ssh-' + time.strftime('%Y%m%d-%H%M%S')) / 'before.json'
    report(output, {'host': host, 'device': identity, 'original_dbug': old,
                    'requested_dbug': target, 'ssh_off_value': old if old is not None else 0x3200})
    confirm('This enables root SSH using the administrator password and reboots the AirPort.\n'
            f'Original setting saved privately: {output}\nKeep this device on a trusted LAN.', 'ENABLE SSH')
    require(not acp.set_props(host, password, [('dbug', struct.pack('>I', target))]), 'dbug write rejected')
    require(acp.get_raw(host, password, 'dbug') == struct.pack('>I', target), 'dbug readback failed; no reboot')
    client.reboot()
    print('Reboot requested. Allow up to five minutes; then run install again. No firmware was changed.')

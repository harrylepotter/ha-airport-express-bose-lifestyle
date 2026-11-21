"""Download checksum-pinned build inputs and compile native MIPS/NetBSD programs."""
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import urllib.request

from .common import ROOT, checked, device_id, private_write, report, require, sha

STOCK_URL = 'https://apsu.apple.com/data/115/041-0311.20130207.aaWs/7.6.2.basebinary'
STOCK_SHA = '995c630b774c0d2f1eabe50f13188068bfe7eb73bd2d1af0411ec07b72ed3e57'
COMP_URL = 'https://archive.netbsd.org/pub/NetBSD-archive/NetBSD-4.0/sgimips/binary/sets/comp.tgz'
COMP_SHA = '7d9f7e877131fa0842976862ad570014683ceb7c8a9b44ec657825be18356dd9'
USB_URL = 'https://raw.githubusercontent.com/NetBSD/src/netbsd-4/sys/dev/usb/usb.h'
USB_SHA = '46c7c3e74c1989a8b801b0d42c88b8e47fe67e359ccb645d10488b2aa57257b1'
IMAGE = 'ha-airport-bose-cross:local'


def download(url, path, digest):
    path = Path(path)
    if path.exists():
        require(sha(path.read_bytes()) == digest, f'Cached input checksum mismatch: {path}; remove it and retry preparation')
        return path
    print(f'Downloading {url}', flush=True)
    # HTTPS certificate validation is left enabled; every input is also hash-pinned.
    with urllib.request.urlopen(url, timeout=90) as response:
        data = response.read(100 * 1024 * 1024 + 1)
    require(sha(data) == digest, f'Download checksum mismatch: {url}')
    private_write(path, data)
    return path


def docker(context=None):
    return ['docker'] + (['--context', context] if context else [])


def prepare(work, identifier, context=None):
    identifier = device_id(identifier)
    work = Path(work).resolve()
    cache, sysroot, output = work / 'downloads', work / 'sysroot', work / 'build' / identifier
    for directory in (cache, sysroot, output):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stock = download(STOCK_URL, cache / 'stock-7.6.2.basebinary', STOCK_SHA)
    archive = download(COMP_URL, cache / 'netbsd4-sgimips-comp.tgz', COMP_SHA)
    # Re-extract verified headers/libraries rather than trust a stale sysroot marker.
    with tarfile.open(archive, 'r:gz') as bundle:
        members = [m for m in bundle.getmembers()
                   if PurePosixPath(m.name).parts[:2] in (('usr', 'include'), ('usr', 'lib'))]
        require(members, 'NetBSD archive lacks the expected headers/libraries')
        bundle.extractall(sysroot, members=members, filter='data')
    machine = sysroot / 'usr/include/machine'
    if not machine.exists():
        machine.symlink_to('sgimips')
    usb = download(USB_URL, cache / 'usb.h', USB_SHA)
    private_write(sysroot / 'usr/include/dev/usb/usb.h', usb.read_bytes())
    checked(docker(context) + ['build', '-t', IMAGE, '-f', str(ROOT / 'native/Dockerfile'), str(ROOT / 'native')])
    common = docker(context) + ['run', '--rm', '--network', 'none', '--read-only',
        '--tmpfs', '/tmp:rw,nosuid,nodev',
        '--mount', f'type=bind,src={ROOT / "native"},dst=/source,readonly',
        '--mount', f'type=bind,src={sysroot},dst=/sysroot,readonly',
        '--mount', f'type=bind,src={output},dst=/build', IMAGE]
    targets = {
        'bose-command': ['airport-bose-command.c', '-DAIRPORT_PL2303_LIBRARY', '/source/airport-pl2303-configure.c'],
        'bose-mqtt': ['airport-bose-mqtt.c', '-I/source', '-I/source/vendor/mqtt-c',
                     '-DMQTTC_PAL_FILE=airport-mqtt-pal.h', '-DMQTT_NO_RESPONSE_TIME_STATS',
                     f'-DID="bose_{identifier}"', f'-DROOT="bose/{identifier}"', '/source/vendor/mqtt-c/mqtt.c'],
        'adapter-check': ['airport-adapter-check.c'],
        'kernel-check': ['airport-verify-pl2303-kernel.c', '/source/airport-pl2303-match.S'],
    }
    for name, sources in targets.items():
        print(f'Building {name}', flush=True)
        checked(common + ['sh', '/source/build-airport-usb-probe.sh', sources[0], name, *sources[1:]])
    checked(common + ['sh', '-c',
        'mips-linux-gnu-as -EB -mips1 -o /build/match.o /source/airport-pl2303-match.S && '
        'mips-linux-gnu-objcopy -O binary -j .text /build/match.o /build/match.bin'])
    # GNU assembler aligns .text to 16 bytes; the reviewed routine is 88 bytes.
    match = (output / 'match.bin').read_bytes()
    require(len(match) >= 88 and not any(match[88:]), 'Unexpected assembly padding')
    private_write(output / 'match.bin', match[:88])
    from .firmware import prepare_image
    image = prepare_image(stock.read_bytes(), match[:88], context)
    private_write(output / 'patched.basebinary', image)
    names = [*targets, 'match.bin', 'patched.basebinary']
    report(output / 'manifest.json', {'device_id': identifier, 'model': 115, 'firmware': '7.6.2',
           'files': {name: {'sha256': sha((output / name).read_bytes()),
                            'bytes': (output / name).stat().st_size} for name in names}})
    print(f'Prepared offline: {output}\nNo AirPort has been modified.')
    return output


def verify_build(output):
    output = Path(output)
    manifest = json.loads((output / 'manifest.json').read_text())
    device_id(manifest['device_id'])
    for name in ('bose-command', 'bose-mqtt', 'adapter-check', 'kernel-check', 'match.bin', 'patched.basebinary'):
        data = (output / name).read_bytes()
        require(sha(data) == manifest['files'][name]['sha256'] and len(data) == manifest['files'][name]['bytes'],
                f'Build output changed: {name}; prepare it again')
    from .firmware import FINAL_SHA, PATCH_SHA
    require(sha((output / 'patched.basebinary').read_bytes()) == FINAL_SHA, 'Unreviewed firmware candidate')
    require(sha((output / 'match.bin').read_bytes()) == PATCH_SHA, 'Unreviewed USB match routine')
    return manifest

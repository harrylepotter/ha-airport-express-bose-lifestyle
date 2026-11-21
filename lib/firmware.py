"""Exact-input, offline firmware transformation. Never writes to a device.

Container format follows TDSOJohn/airport-express-tools (MIT); see NOTICE.md.
"""
from .common import ROOT, require, sha
from .build import docker, IMAGE
import sys
sys.path.insert(0, str(ROOT / "vendor"))
from ufs1 import UFS1, IFMT, IFDIR

import struct
import subprocess
import zlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SOURCE_SHA = "995c630b774c0d2f1eabe50f13188068bfe7eb73bd2d1af0411ec07b72ed3e57"
BANK_SHA = "155d45a19e2e6e8de49978de9e72b384e2ef301127c6331a83214e44e21cd992"
PATCH_SHA = "b43b81fb2ecf9d90af91bfb0ae81655d2ba4bc67d21b30507fbec444a99e42e3"
MAGIC = b"APPLE-FIRMWARE\0"
HEADER = struct.Struct(">15sB2I4BI")
PUBLIC_SEED = bytes.fromhex("1075e806f4770cd4763bd285a64e9174")
KEY = bytes(value ^ (index + 0x19) for index, value in enumerate(PUBLIC_SEED))
PATCH_OFFSET = 0x802423f4 - 0x80060000
FUNCTION_SIZE = 120
FINAL_SHA = 'be4f5d4b67f4fce3a1c789c61fe794b8a12a7ea161e4a7cb0f969fe251f9a164'


def crypt(payload, iv, encrypt):
    result = bytearray()
    for offset in range(0, len(payload), 0x8000):
        chunk = payload[offset:offset + 0x8000]
        count = len(chunk) & ~15
        cipher = Cipher(algorithms.AES(KEY), modes.CBC(iv))
        context = cipher.encryptor() if encrypt else cipher.decryptor()
        result.extend(context.update(chunk[:count]) + context.finalize())
        result.extend(chunk[count:])
    return bytes(result)


def unpack_container(data):
    if len(data) < HEADER.size + 4:
        raise ValueError("Truncated container")
    header, payload = data[:HEADER.size], data[HEADER.size:-4]
    fields = HEADER.unpack(header)
    if fields[0] != MAGIC or fields[2] != 115:
        raise ValueError("Wrong container magic/model")
    if fields[7] & 2:
        payload = crypt(payload, header[:16], False)
    if struct.unpack(">I", data[-4:])[0] != zlib.adler32(header + payload) & 0xffffffff:
        raise ValueError("Container checksum mismatch")
    return header, payload


def wrap(header, payload):
    checksum = struct.pack(">I", zlib.adler32(header + payload) & 0xffffffff)
    if HEADER.unpack(header)[7] & 2:
        payload = crypt(payload, header[:16], True)
    return header + payload + checksum


def split(data):
    outer, inner_data = unpack_container(data)
    inner, plain = unpack_container(inner_data)
    gzip_start = plain.find(b"\x1f\x8b\x08")
    if gzip_start != 22144 or plain[gzip_start + 3] != 8:
        raise ValueError("Unexpected 7.6.2 loader/gzip layout")
    header_end = plain.index(0, gzip_start + 10) + 1
    stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
    bank = stream.decompress(plain[gzip_start:]) + stream.flush()
    if not stream.eof or stream.unused_data.strip(b"\0") or len(stream.unused_data) > 3:
        raise ValueError("Incomplete gzip stream or unexpected trailer")
    return outer, inner, plain[:gzip_start], plain[gzip_start:header_end], bank


def join(outer, inner, loader, gzip_header, bank, context=None):
    # Apple 7.6.2 used GNU gzip's deflater; zlib's output is different. This
    # isolated local compressor reproduces the original compressed bytes.
    result = subprocess.run(docker(context) + [
        "run", "--rm", "--network", "none",
        "--read-only", "-i", IMAGE, "gzip", "-n", "-9", "-c",
    ], input=bank, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60)
    compressed = result.stdout
    if compressed[:4] != b"\x1f\x8b\x08\0" or zlib.decompress(compressed, 31) != bank:
        raise ValueError("Compressor returned an unexpected gzip stream")
    gzip = gzip_header + compressed[10:]
    plain = loader + gzip
    plain += bytes(-len(plain) % 4)
    return wrap(outer, wrap(inner, plain))


def replace_file(image, fs, ino, content):
    node = fs.inode(ino)
    room = struct.unpack_from('>i', node['raw'], 0x68)[0] * 512
    require(node['mode'] & 0o170000 == 0o100000 and node['nlink'] == 1, 'Unexpected boot-script inode')
    require(not any(node['ib']) and 0 < len(content) <= room <= fs.bsize, 'Boot script does not fit existing allocation')
    require(node['size'] <= fs.bsize and node['db'][0], 'Unsupported boot-script allocation')
    result = bytearray(image)
    offset = fs._byte(node['db'][0])
    result[offset:offset + room] = content + bytes(room - len(content))
    cg, inc = ino // fs.ipg, ino % fs.ipg
    cgimin = fs.fpg * cg + fs.cgoffset * (cg & ~fs.cgmask) + fs.iblkno
    inode_offset = fs._byte(cgimin + (inc // fs.inopb) * fs.frag) + (ino % fs.inopb) * fs.DINODE
    struct.pack_into('>Q', result, inode_offset + 8, len(content))
    return bytes(result), [(offset, offset + room), (inode_offset + 8, inode_offset + 16)]


def startup(bank):
    fs = UFS1(bank)
    entries = {p: (i, m, s) for p, i, m, s in fs.walk()}
    rc = fs.read_file(entries['/etc/rc'][0]).decode()
    require(rc.endswith('date\nexit 0\n'), 'Unexpected rc script')
    rc = '\n'.join(line for line in rc.splitlines()
                   if not line.lstrip().startswith('#') or line.startswith('#!')) + '\n'
    hook = '''B=/mnt/Flash/bose
if [ -f $B.enable -a ! -f $B.off ]; then
    if [ -f $B.try ]; then
        /bin/mv $B.try $B.off
    else
        : > $B.try
        /bin/sh $B.start </dev/null >/mnt/Memory/bose-mqtt.log 2>&1 &
    fi
fi
'''
    rc = rc[:-len('exit 0\n')] + hook + 'exit 0\n'
    acpd = '''#!/bin/sh
# PROVIDE: acpd
# REQUIRE: mountall NETWORKING
# BEFORE: DAEMON
$_rc_subr_loaded . /etc/rc.subr
name="acpd"
rcvar=$name
command="/sbin/ACPd"
pidfile="/var/run/ACPd.pid"
load_rc_config $name
if [ "$1" = start -a -f /mnt/Flash/bose-prestart ]; then
    /bin/sh /mnt/Flash/bose-prestart
fi
run_rc_command "$1"
'''
    replacements = {'/etc/rc': rc.encode(), '/etc/rc.d/acpd': acpd.encode()}
    changed, allowed = bank, []
    for path, content in replacements.items():
        subprocess.run(['sh', '-n'], input=content, check=True)
        changed, ranges = replace_file(changed, fs, entries[path][0], content)
        allowed.extend(ranges)
    require(len(changed) == len(bank), 'RAM filesystem size changed')
    require(all(a == b or any(start <= i < end for start, end in allowed)
                for i, (a, b) in enumerate(zip(bank, changed))), 'Change outside permitted script allocations')
    newfs = UFS1(changed)
    after = {p: (i, m, s) for p, i, m, s in newfs.walk()}
    require(entries.keys() == after.keys(), 'Filesystem entry set changed')
    for path, (ino, mode, size) in entries.items():
        require(after[path][:2] == (ino, mode), f'Inode identity changed: {path}')
        if path in replacements:
            require(newfs.read_file(ino) == replacements[path], f'Script readback failed: {path}')
        else:
            require(after[path][2] == size and newfs.inode(ino)['raw'] == fs.inode(ino)['raw'],
                    f'Unrelated inode changed: {path}')
            if mode & IFMT != IFDIR:
                require(newfs.read_file(ino) == fs.read_file(ino), f'Unrelated file changed: {path}')
    print(f'Verified all {len(entries)} filesystem entries; only two boot scripts changed.')
    return changed


def prepare_image(source, code, context=None):
    require(sha(source) == SOURCE_SHA, 'Unsupported stock firmware: only the pinned A1392 7.6.2 image is supported')
    require(len(code) == 88 and sha(code) == PATCH_SHA, 'Unreviewed USB match code')
    parts = list(split(source))
    require(sha(parts[4]) == BANK_SHA, 'Unexpected inflated firmware')
    require(join(*parts, context=context) == source, 'Unmodified firmware roundtrip is not byte-identical')
    original = parts[4]
    patched = original[:PATCH_OFFSET] + code + bytes(FUNCTION_SIZE - len(code)) + original[PATCH_OFFSET + FUNCTION_SIZE:]
    require(len(patched) == len(original), 'Kernel length changed')
    parts[4] = startup(patched)
    result = join(*parts, context=context)
    require(split(result) == tuple(parts), 'Candidate decode/readback failed')
    require(sha(result) == FINAL_SHA, 'Candidate differs from the firmware tested on hardware; stop')
    return result


def payload(image):
    _, inner = unpack_container(image)
    return unpack_container(inner)[1]


def check_bank(raw, expected=None):
    require(len(raw) == 0x700000, 'Incomplete firmware bank')
    checksum, length = struct.unpack_from('>II', raw, len(raw) - 32)
    require(3 * 1024 * 1024 <= length <= len(raw) - 32, 'Invalid bank trailer length')
    require(zlib.adler32(raw[:length]) == checksum and raw[-24:] == b'\xff' * 24,
            'Invalid bank checksum or trailer padding')
    if expected is not None:
        require(raw[:length] == expected, 'Firmware bank does not match the expected complete payload')
    return {'bytes': len(raw), 'image_bytes': length, 'sha256': sha(raw), 'payload_sha256': sha(raw[:length])}

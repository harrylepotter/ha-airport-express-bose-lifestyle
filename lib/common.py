"""Small shared validation helpers. No device-specific credentials or addresses."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LAN = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def lan_address(value):
    address = ipaddress.IPv4Address(value)
    require(any(address in network for network in LAN), 'Use an RFC1918 LAN IPv4 address')
    return str(address)


def device_id(value):
    require(re.fullmatch(r'[a-z][a-z0-9_]{0,31}', value),
            'Device ID must start with a lowercase letter and contain 1–32 lowercase letters, digits or underscores')
    return value


def private_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Refuse symlinks and create with restrictive permissions before any data is written.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)


def report(path, value):
    private_write(path, (json.dumps(value, indent=2) + '\n').encode())


def checked(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def confirm(message, word):
    print(message)
    require(input(f'Type {word} to continue: ').strip() == word, 'Cancelled; no next action taken')

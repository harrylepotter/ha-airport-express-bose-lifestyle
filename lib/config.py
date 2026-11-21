"""Interactive MQTT configuration, kept out of command lines and reports."""
import getpass
import socket
import struct
import uuid

from .common import lan_address, require


def field(value, name):
    require(value and len(value.encode()) <= 255 and not any(c in value for c in '\r\n\0'),
            f'{name} must be 1–255 UTF-8 bytes without newline or NUL')
    return value


def prompt():
    server = lan_address(input('MQTT broker LAN IPv4 address: ').strip())
    port = int(input('MQTT port [1883]: ').strip() or '1883')
    require(1 <= port <= 65535, 'Port must be 1–65535')
    username = field(input('MQTT username: '), 'Username')
    password = field(getpass.getpass('MQTT password (hidden): '), 'Password')
    require(password == getpass.getpass('Repeat MQTT password: '), 'MQTT passwords differ')
    return {'server': server, 'port': port, 'username': username, 'password': password}


def encode(config):
    server = lan_address(config['server'])
    port = int(config['port'])
    require(1 <= port <= 65535, 'Invalid MQTT port')
    username, password = field(config['username'], 'Username'), field(config['password'], 'Password')
    return f'server={server}\nport={port}\nusername={username}\npassword={password}\n'.encode()


def mqtt_string(value):
    value = value.encode()
    return struct.pack('>H', len(value)) + value


def packet(kind, body):
    result = bytearray([kind]); remaining = len(body)
    while True:
        digit = remaining % 128; remaining //= 128
        result.append(digit | (128 if remaining else 0))
        if not remaining:
            return bytes(result) + body


def authenticate(ssh, config):
    """Check broker reachability from the AirPort and CONNACK. Never publish."""
    encode(config)  # Validate before forwarding or opening any connection.
    local = ssh.forward(config['server'], config['port'])
    body = mqtt_string('MQTT') + b'\x04\xc2\x00\x1e'
    body += mqtt_string('airport-setup-' + uuid.uuid4().hex[:12])
    body += mqtt_string(config['username']) + mqtt_string(config['password'])
    with socket.create_connection(('127.0.0.1', local), 8) as sock:
        sock.settimeout(8)
        sock.sendall(packet(0x10, body))
        reply = b''
        while len(reply) < 4:
            part = sock.recv(4 - len(reply))
            require(part, 'Broker disconnected before CONNACK')
            reply += part
        require(reply == b'\x20\x02\x00\x00', 'MQTT authentication rejected (or broker requires TLS)')
        sock.sendall(b'\xe0\x00')
    print('MQTT authentication succeeded through the AirPort; no command was published.')

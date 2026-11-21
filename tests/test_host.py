"""Offline tests; no SSH connection, firmware write or Bose command is possible."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import build, config, device, firmware, installer
from lib.common import device_id, lan_address, private_write, report, sha


def bank(content=b'A'):
    image = content * (3 * 1024 * 1024)
    result = bytearray(b'\xff' * 0x700000)
    result[:len(image)] = image
    struct.pack_into('>II', result, len(result) - 32, zlib.adler32(image), len(image))
    return bytes(result), image


class InputTests(unittest.TestCase):
    def test_lan_only(self):
        for address in ('10.0.0.1', '172.16.2.3', '192.168.50.2'):
            self.assertEqual(lan_address(address), address)
        for address in ('127.0.0.1', '169.254.1.1', '8.8.8.8', '0.0.0.0', '224.0.0.1', '::1', '-oProxyCommand=x'):
            with self.assertRaises(ValueError): lan_address(address)

    def test_device_ids_cannot_inject_c_or_paths(self):
        self.assertEqual(device_id('living_room2'), 'living_room2')
        for value in ('', '../x', 'one/two', 'a"', 'A', 'a;exit', 'a\n', 'a' * 33):
            with self.assertRaises(ValueError): device_id(value)

    def test_credentials_preserve_literal_characters(self):
        data = config.encode({'server': '10.0.0.2', 'port': 1883, 'username': 'name=x',
                              'password': 'literal$(`x`)=# space'})
        self.assertIn(b'password=literal$(`x`)=# space\n', data)
        for bad in ('', 'x\nserver=8.8.8.8', 'x\r', 'x\0', 'é' * 128):
            with self.assertRaises(ValueError): config.field(bad, 'Password')

    def test_private_files_and_no_symlink_following(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'secret'
            private_write(path, b'private')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            link = Path(tmp) / 'link'; link.symlink_to(path)
            with self.assertRaises(OSError): private_write(link, b'replaced')
            self.assertEqual(path.read_bytes(), b'private')

    def test_wrong_cached_download_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input'; path.write_bytes(b'changed')
            with patch('urllib.request.urlopen') as network:
                with self.assertRaises(ValueError): build.download('https://example.invalid', path, sha(b'expected'))
                network.assert_not_called()

    def test_wrong_firmware_stops_before_decompression(self):
        with patch.object(firmware, 'split') as split:
            with self.assertRaises(ValueError): firmware.prepare_image(b'unknown', b'code')
            split.assert_not_called()

    def test_wrong_model_or_version_stops(self):
        for identity in ({'syAP': 115, 'syVs': '7.8.1'}, {'syAP': 999, 'syVs': '7.6.2'}):
            with patch.object(device.acp, 'read_device', return_value=identity):
                with self.assertRaises(ValueError): device.ACP('127.0.0.1', 'dummy').identity()


class BankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original, cls.original_payload = bank(b'A')
        cls.modified, cls.modified_payload = bank(b'B')

    def test_complete_bank_and_checksum(self):
        self.assertEqual(firmware.check_bank(self.original, self.original_payload)['image_bytes'], 3 * 1024 * 1024)
        for corrupt in (self.original[:-1], b'X' + self.original[1:], self.original[:-1] + b'\0'):
            with self.assertRaises(ValueError): firmware.check_bank(corrupt)
        with self.assertRaises(ValueError): firmware.check_bank(self.original, self.modified_payload)

    def make_backup(self, directory):
        manifest = {'host': '192.168.1.50', 'files': {}}
        data_by_path = {}
        for index, size in enumerate(device.FLASH_SIZES):
            data = self.original if index < 2 else b'Z' * size
            name = f'flash{index}.raw'
            private_write(directory / name, data)
            manifest['files'][name] = {'bytes': size, 'sha256': sha(data)}
            data_by_path[f'/dev/rflash{index}.raw'] = data
        report(directory / 'manifest.json', manifest)
        return data_by_path

    def flash_case(self, *, corrupt_payload=False, corrupt_secondary=False, corrupt_backup=False, raced=False):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state = self.make_backup(directory)
            ssh = Mock(host='192.168.1.50')
            ssh.read.side_effect = lambda path: state[path]
            client = Mock()
            def flash(_):
                state['/dev/rflash0.raw'] = self.original if corrupt_payload else self.modified
                if corrupt_secondary: state['/dev/rflash1.raw'] = self.modified
            client.flash.side_effect = flash
            if corrupt_backup: (directory / 'flash3.raw').write_bytes(b'bad')
            if raced: state['/dev/rflash0.raw'] = self.modified
            bad = corrupt_payload or corrupt_secondary or corrupt_backup or raced
            with patch.object(device, 'split'), patch.object(device, 'payload', return_value=self.modified_payload):
                if bad:
                    with self.assertRaises(ValueError): device.flash_verified(ssh, client, b'image', directory)
                else:
                    device.flash_verified(ssh, client, b'image', directory)
                    record = json.loads((directory / 'flash-result.json').read_text())
                    self.assertTrue(record['secondary_unchanged'])
                    self.assertTrue(record['status'].startswith('PRIMARY VERIFIED'))
            self.assertEqual(client.flash.call_count, 0 if corrupt_backup or raced else 1)
            client.reboot.assert_not_called()

    def test_success_writes_once_and_never_reboots(self): self.flash_case()
    def test_changed_backup_stops_before_upload(self): self.flash_case(corrupt_backup=True)
    def test_changed_live_bank_stops_before_upload(self): self.flash_case(raced=True)
    def test_payload_mismatch_has_no_retry_or_reboot(self): self.flash_case(corrupt_payload=True)
    def test_secondary_mismatch_has_no_retry_or_reboot(self): self.flash_case(corrupt_secondary=True)

    def test_profile_accepts_only_known_primary_and_stock_secondary(self):
        # Synthetic image bytes represent stock/candidate; actual bank checks stay real.
        with (patch.object(installer, 'SOURCE_SHA', sha(b'stock')),
              patch.object(installer, 'FINAL_SHA', sha(b'patched')),
              patch.object(installer, 'payload', side_effect=lambda image: self.original_payload if image == b'stock' else self.modified_payload)):
            self.assertFalse(installer.check_profile(self.original, self.original, b'stock', b'patched'))
            self.assertTrue(installer.check_profile(self.modified, self.original, b'stock', b'patched'))
            with self.assertRaises(ValueError): installer.check_profile(self.modified, self.modified, b'stock', b'patched')
            unknown, _ = bank(b'C')
            with self.assertRaises(ValueError): installer.check_profile(unknown, self.original, b'stock', b'patched')


class TransportTests(unittest.TestCase):
    def test_configuration_uses_stdin_not_arguments(self):
        ssh = device.SSH('192.168.1.50', Path('/tmp/unused'))
        ssh.run = Mock()
        ssh.read = Mock(return_value=b'password=example-secret\n')
        ssh.upload('/mnt/Flash/bose-mqtt.conf', b'password=example-secret\n')
        for call in ssh.run.call_args_list:
            self.assertNotIn('example-secret', call.args[0])
        self.assertEqual(ssh.run.call_args_list[0].args[1], b'password=example-secret\n')

    def test_upload_readback_failure_does_not_rename(self):
        ssh = device.SSH('192.168.1.50', Path('/tmp/unused'))
        ssh.run = Mock(); ssh.read = Mock(return_value=b'corrupt')
        with self.assertRaises(ValueError): ssh.upload('/mnt/Flash/bose-command', b'expected', True)
        self.assertEqual(ssh.run.call_count, 1)
        self.assertNotIn('/bin/mv', ssh.run.call_args.args[0])

    def test_mqtt_authentication_never_publishes(self):
        ssh = Mock(); ssh.forward.return_value = 12345
        sock = Mock(); sock.__enter__ = Mock(return_value=sock); sock.__exit__ = Mock(return_value=False)
        sock.recv.side_effect = [b'\x20', b'\x02\x00\x00']
        details = {'server': '10.0.0.2', 'port': 1883, 'username': 'user', 'password': 'test-password'}
        with patch('socket.create_connection', return_value=sock): config.authenticate(ssh, details)
        self.assertEqual([c.args[0][0] for c in sock.sendall.call_args_list], [0x10, 0xe0])

    def test_mqtt_authentication_failure_prevents_install_progress(self):
        ssh = Mock(); ssh.forward.return_value = 12345
        sock = Mock(); sock.__enter__ = Mock(return_value=sock); sock.__exit__ = Mock(return_value=False)
        sock.recv.return_value = b'\x20\x02\x00\x05'
        with patch('socket.create_connection', return_value=sock):
            with self.assertRaises(ValueError):
                config.authenticate(ssh, {'server': '10.0.0.2', 'port': 1883, 'username': 'u', 'password': 'p'})
        self.assertEqual(sock.sendall.call_count, 1)


if __name__ == '__main__':
    unittest.main()

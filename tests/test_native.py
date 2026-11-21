#!/usr/bin/env python3
"""Host-side fault tests using a socket pair; never connects to real hardware."""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1] / "native"
with tempfile.TemporaryDirectory(prefix="bose-protocol-test-") as tmp:
    directory = Path(tmp)
    stub = directory / "adapter.c"
    stub.write_text('#include <stdlib.h>\nint airport_pl2303_open_bulk(void) { return atoi(getenv("BOSE_TEST_FD")); }\n')
    exe = directory / "command"
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror",
                    f'-DAIRPORT_SERIAL_LOCK="{directory / "lock"}"',
                    str(ROOT / "airport-bose-command.c"), str(stub), "-o", str(exe)], check=True)
    for scenario, expected in [("legacy", "sent_unconfirmed"), ("stay_tap", "command_acknowledged"), ("ack", "command_acknowledged"),
                               ("nak", "command_rejected"),
                               ("zone_nak", "handshake_failed_not_sent"),
                               ("zone_silent", "handshake_failed_not_sent"),
                               ("silent", "handshake_failed_not_sent"),
                               ("no_command_ack", "sent_unconfirmed")]:
        host, device = socket.socketpair()
        sent = bytearray()
        def emulate():
            try:
                while byte := device.recv(1):
                    sent.extend(byte)
                    n = len(sent)
                    if scenario in ("silent", "legacy"):
                        continue
                    if n <= 8:
                        device.sendall(byte)  # idle echo through TAP<CR>
                    elif n in (9, 13):
                        device.sendall(b"X")  # preamble acknowledgement
                    elif n == 12 and scenario != "zone_silent":
                        device.sendall(b"W" if scenario == "zone_nak" else b"X")
                    elif n == 16 and scenario != "no_command_ack":
                        device.sendall(b"IW" if scenario == "nak" else b"IX")
                    elif 17 <= n <= 19:
                        # Real device NAKs the first three escape characters,
                        # then recognizes the complete documented "Bose" word.
                        device.sendall(b"W")
                    elif n == 20:
                        device.sendall(b"\r\nBose")  # explicit Idle escape
            except (BrokenPipeError, ConnectionResetError):
                pass
        thread = threading.Thread(target=emulate)
        thread.start()
        import os
        env = dict(os.environ, BOSE_TEST_FD=str(host.fileno()))
        flags=["--legacy"] if scenario=="legacy" else ["--stay-tap"] if scenario=="stay_tap" else []
        proc = subprocess.run([str(exe)] + flags + ["aux"], env=env, pass_fds=(host.fileno(),),
                              capture_output=True, timeout=12)
        host.close()
        thread.join(timeout=2)
        device.close()
        result = json.loads(proc.stdout)
        assert result["result"] == expected, (scenario, result, proc.stderr)
        expected_wire = b"TAP\raEIIaxxx" if scenario=="legacy" else b"BoseTAP\r" if scenario == "silent" else (
            b"BoseTAP\raA\x01@" if scenario.startswith("zone_") else b"BoseTAP\raA\x01@aEIIBose")
        if scenario=="stay_tap":expected_wire=expected_wire[:-4]
        assert bytes(sent) == expected_wire, sent
        assert result["zone_acknowledged"] is (scenario not in ("silent", "zone_nak", "zone_silent", "legacy"))
        assert result["bose_acknowledged"] is (True if scenario in ("ack","stay_tap") else False if scenario == "nak" else None)
        assert proc.returncode == (0 if scenario in ("ack","stay_tap") else 1)
        print(f"PASS {scenario}: {expected}; no retry")
    for command, code, fail_at in (("volume_down", b"E@", None), ("volume_up", b"A@", None),
                                  ("volume_up", b"A@", 2), ("volume_down", b"E@", "silent")):
        host, device = socket.socketpair()
        sent = bytearray()
        def emulate_volume():
            steps = 0
            finished = False
            try:
                while byte := device.recv(1):
                    sent.extend(byte)
                    n = len(sent)
                    if n <= 8:
                        device.sendall(byte)
                    elif n in (9, 12):
                        device.sendall(b"X")
                    elif n > 12 and not finished:
                        if (n - 12) % 4 == 1:
                            device.sendall(b"X")
                        elif (n - 12) % 4 == 0:
                            steps += 1
                            if not (fail_at == "silent" and steps == 2):
                                device.sendall(b"W" if steps == fail_at else b"X")
                            finished = steps == fail_at or (fail_at == "silent" and steps == 2) or steps == 5
            except (BrokenPipeError, ConnectionResetError):
                pass
        thread = threading.Thread(target=emulate_volume)
        thread.start()
        proc = subprocess.run([str(exe), command],
                              env=dict(os.environ, BOSE_TEST_FD=str(host.fileno())),
                              pass_fds=(host.fileno(),), capture_output=True, timeout=12)
        host.close()
        thread.join(timeout=2)
        device.close()
        result = json.loads(proc.stdout)
        expected_wire = b"BoseTAP\raA\x01@" + (b"aE" + code) * (2 if fail_at else 5) + b"Bose"
        assert bytes(sent) == expected_wire, sent
        assert result["steps_requested"] == 5
        assert result["steps_acknowledged"] == (1 if fail_at else 5)
        assert result["bose_acknowledged"] is (None if fail_at == "silent" else fail_at is None)
        assert proc.returncode == (1 if fail_at else 0)
        if fail_at is None:
            dry = subprocess.run([str(exe), "--dry-run", command], capture_output=True, check=True)
            assert bytes.fromhex(dry.stdout.decode()) == expected_wire
        print(f"PASS {command}: five requested; stop at {fail_at or 'completion'}; no retry")
    state_test = directory / "state.c"
    state_test.write_text(r'''
#include <assert.h>
#include "airport-bose-state.h"
int main(void) {
    struct bose_state s;
    bose_state_unknown(&s,"startup",0);
    bose_state_command(&s,"power_toggle",1,1); assert(s.power==-1);
    bose_state_command(&s,"aux",1,2); assert(s.power==1 && !strcmp(s.source,"aux"));
    bose_state_command(&s,"power_toggle",1,3); assert(s.power==0);
    bose_state_command(&s,"power_toggle",1,4); assert(s.power==1 && !strcmp(s.source,"unknown"));
    bose_state_command(&s,"aux",0,5); assert(s.power==-1);
    bose_state_command(&s,"fm",1,6); assert(s.power==1 && !strcmp(s.source,"radio"));
    bose_state_unknown(&s,"connection_lost",7); assert(s.power==-1);
    return 0;
}
''')
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT),
                    str(state_test), "-o", str(directory / "state")], check=True)
    subprocess.run([str(directory / "state")], check=True)
    print("PASS state: unknown toggle, acknowledged source, toggles, failure, radio, reconnect")

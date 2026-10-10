"""Shared support for flowtable identity."""

from __future__ import annotations

import asyncio
import socket
import struct

from _flowtable_rig import DPORT, WAN_IP, console_python


async def set_mac(con, dev, address, mac):
    # Announce the new receive address through ordinary ARP. Endpoints retain
    # their existing sockets and learn the gateway's new address normally.
    script = rf'''
import socket, struct, subprocess
subprocess.run(['ip', 'link', 'set', 'dev', {dev!r}, 'address', {mac!r}], check=True)
mac = bytes.fromhex({mac.replace(':', '')!r})
ip = socket.inet_aton({address!r})
frame = b'\xff'*6 + mac + b'\x08\x06' + struct.pack('!HHBBH', 1, 0x800, 6, 4, 1) + mac + ip + b'\x00'*6 + ip
with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x806)) as s:
    s.bind(({dev!r}, 0))
    s.send(frame); s.send(frame)
'''
    await console_python(con, script)


async def wan_wire(r, sport, source_mac, operation):
    raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x800))
    raw.bind((r.wan_if, 0))
    raw.setblocking(False)
    loop = asyncio.get_running_loop()

    async def capture():
        count = 0
        async with asyncio.timeout(15):
            while count < 256:
                frame = await loop.sock_recv(raw, 65536)
                if len(frame) < 42 or frame[12:14] != b'\x08\x00' or frame[23] != 17:
                    continue
                if frame[26:30] != socket.inet_aton(r.lan_ip) or frame[30:34] != socket.inet_aton(WAN_IP):
                    continue
                ihl = (frame[14] & 15) * 4
                if struct.unpack('!HH', frame[14 + ihl:18 + ihl]) != (sport, DPORT):
                    continue
                assert frame[6:12] == bytes.fromhex(source_mac.replace(':', '')), frame.hex()
                assert frame[:6] == bytes.fromhex(r.wan_mac.replace(':', '')), frame.hex()
                count += 1
        return {"frames": count, "source_mac": source_mac}

    task = asyncio.create_task(capture())
    try:
        result = await operation
        return result, await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raw.close()

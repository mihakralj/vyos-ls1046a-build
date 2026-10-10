"""PF_KEY imports no ESN flag (patch 040).

PF_KEY cannot carry the replay_esn state an ESN SA needs. An earlier patch 040
mapped a private SADB_SAFLAGS_ESN bit onto XFRM_STATE_ESN anyway, so a
PF_KEY-added SA was a valid ESN state with a NULL replay_esn: the first ESP
packet for it reached xfrm_input(), whose xfrm_replay_seqhi() dereferences
replay_esn. ESN SAs come from netlink, which validates and allocates that
state; PF_KEY now leaves the bit alone.

The SA is added over PF_KEY on the DUT with that bit set, then a few ESP
packets for it are sent from the WAN host. The state must not say `esn`, the
packets must reach it (they fail authentication: XfrmInStateProtoError rises,
which proves they were judged by this SA rather than dropped before it), and
the kernel must stay clean (splat_window). On the old patch the state says
`esn` and the first packet oopses the KASAN kernel.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import socket
import struct

from ask_orch.uart import Console
from _flowtable_rig import console_python
from _flowtable_service_ipsec_replay import xfrm_mib
from _ipsec_helpers import dut_local_ipv4
from _topology import TARGET_WAN_IF

WAN = os.environ.get("ASK_WAN_IP", "")
OLD_ESN_FLAG = 0x01000000
PACKETS = 8

ADD_SCRIPT = r'''
import json, socket, struct
SADB_ADD, SADB_SATYPE_ESP = 3, 3
SADB_EXT_SA, SADB_EXT_ADDRESS_SRC, SADB_EXT_ADDRESS_DST = 1, 5, 6
SADB_EXT_KEY_AUTH, SADB_EXT_KEY_ENCRYPT = 8, 9
SADB_AALG_SHA1HMAC, SADB_X_EALG_AESCBC, SADB_SASTATE_MATURE = 3, 12, 1

def address(ext, ip):
    sin = struct.pack("=H", socket.AF_INET) + struct.pack("!H", 0) + socket.inet_aton(ip) + bytes(8)
    return struct.pack("=HHBBH", 3, ext, 0, 32, 0) + sin

def key(ext, material):
    body = material + bytes(-len(material) % 8)
    return struct.pack("=HHHH", 1 + len(body) // 8, ext, len(material) * 8, 0) + body

exts = (struct.pack("=HHIBBBBI", 2, SADB_EXT_SA, socket.htonl({spi}), 32, SADB_SASTATE_MATURE,
                    SADB_AALG_SHA1HMAC, SADB_X_EALG_AESCBC, {flags})
        + address(SADB_EXT_ADDRESS_SRC, {src!r}) + address(SADB_EXT_ADDRESS_DST, {dst!r})
        + key(SADB_EXT_KEY_AUTH, bytes(range(1, 21))) + key(SADB_EXT_KEY_ENCRYPT, bytes(range(32, 48))))
message = struct.pack("=BBBBHHII", 2, SADB_ADD, 0, SADB_SATYPE_ESP, 2 + len(exts) // 8, 0, 1, 0) + exts
with socket.socket(socket.AF_KEY, socket.SOCK_RAW, 2) as s:
    s.settimeout(3)
    s.send(message)
    reply = s.recv(4096)
print(json.dumps({{"type": reply[1], "errno": reply[2]}}))
'''


async def dut_xfrm_stat(target_agent, session) -> dict:
    reply = await target_agent.fs_read(session, "/proc/net/xfrm_stat")
    return xfrm_mib(bytes.fromhex(reply["content_hex"]).decode())


def send_esp(dst: str, spi: int, count: int) -> None:
    # An ESP header, a sequence number and opaque ciphertext: enough for the
    # receiver to find the SA by SPI and run its replay and integrity checks.
    with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ESP) as s:
        for seq in range(1, count + 1):
            s.sendto(struct.pack("!II", spi, seq) + secrets.token_bytes(64), (dst, 0))


async def test_pfkey_sa_carries_no_esn(aiohttp_session, target_agent):
    assert WAN, "ASK_WAN_IP must name this host's address on the DUT's WAN segment"
    local = await dut_local_ipv4(target_agent, aiohttp_session, TARGET_WAN_IF)
    spi = 0x0E500000 | secrets.randbits(16)
    state = ["src", WAN, "dst", local, "proto", "esp", "spi", hex(spi)]
    accept = ["INPUT", "-p", "esp", "-s", WAN, "-d", local, "-j", "ACCEPT"]
    await target_agent.exec_cmd(aiohttp_session, ["ip", "xfrm", "state", "delete", *state])
    inserted = False
    try:
        result = await console_python(Console.target(), ADD_SCRIPT.format(
            spi=spi, flags=OLD_ESN_FLAG, src=WAN, dst=local), timeout=30)
        reply = json.loads(result["stdout"].strip().splitlines()[-1])
        assert reply == {"type": 3, "errno": 0}, reply

        shown = await target_agent.exec_cmd(aiohttp_session, ["ip", "-d", "xfrm", "state", "get", *state])
        assert shown.get("rc") == 0, shown
        # `ip` prints a flag line only for a state with flags; an ESN state's
        # names `esn`.
        assert f"spi 0x{spi:08x}" in shown["stdout"], shown["stdout"]
        assert not re.search(r"\bflag\b.*\besn\b", shown["stdout"]), shown["stdout"]

        r = await target_agent.exec_cmd(aiohttp_session, ["iptables", "-I", *accept])
        assert r.get("rc") == 0, r
        inserted = True
        before = await dut_xfrm_stat(target_agent, aiohttp_session)
        await asyncio.to_thread(send_esp, local, spi, PACKETS)
        for _ in range(20):
            await asyncio.sleep(0.25)
            now = await dut_xfrm_stat(target_agent, aiohttp_session)
            if now["XfrmInStateProtoError"] >= before["XfrmInStateProtoError"] + PACKETS:
                break
        assert now["XfrmInStateProtoError"] - before["XfrmInStateProtoError"] == PACKETS, (before, now)
    finally:
        if inserted:
            await target_agent.exec_cmd(aiohttp_session, ["iptables", "-D", *accept])
        await target_agent.exec_cmd(aiohttp_session, ["ip", "xfrm", "state", "delete", *state])

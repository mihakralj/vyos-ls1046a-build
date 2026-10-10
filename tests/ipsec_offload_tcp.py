"""Bulk TCP through a packet-offloaded tunnel, on the software path.

TCP hands the stack GSO packets. A local socket builds them from its send
queue, because a bundle whose SA is offloaded on the route's own port keeps
the socket's segmentation offload (xfrm_dst_offload_ok()). With no flowtable
every one reaches xfrm_output() for the offloaded SA. Forwarded segments would
arrive merged only if the LAN port's GRO merged them, and the DPAA receive
path does not for this traffic, so only the local transfer is required to
exercise the segmentation; the forwarded one still has to arrive whole. SEC encrypts one packet per ESP and needs
the inner checksums finished, so each GSO packet has to be segmented in
software before SEC sees it; finishing a GSO packet's checksum in place is
refused with a warning and the packet dropped.

Two transfers of a known size over one SA pair, to a TCP sink on the WAN
host: one from the LAN VM, forwarded by the DUT, and one from the DUT itself,
from an address on its loopback. Each must arrive whole and in order, and the
sink's count must come back through the tunnel; XfrmOutError must not move;
and SEC must have been handed at least one frame per full-size segment. The
splat window fails the test on the warning. A kprobe on the software
segmentation must count more during the local transfer than a quiet window's
rate accounts for over the same time, so the test cannot pass on a path that
never built a GSO packet.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time

from ask_orch.client import Agent
from ask_orch.uart import Console
from _topology import TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_rig import (artifact_dir, command, console_command, console_json, console_python, read)
from _flowtable_service_ipsec_replay import (xfrm_mib)
from _ipsec_inbound_flow_offload import (sec_counter)
from _ipsec_offload_egress_device import (INNER, install_tunnel, wan_outer)

# The DUT's own inner address, on its loopback.
DUT_INNER = "198.18.106.3"
PORT = 48990
TOTAL = 8 << 20
# Offsets into a repeating 0..255 pattern, so the sink can check order and
# content of whatever chunk sizes TCP delivers without holding the stream.
PATTERN = bytes(range(256)) * 258
# A kprobe on __skb_gso_segment(), where a GSO packet is cut into segments in
# software. The offloaded SA's output path cuts every GSO packet these
# transfers send; a quiet window of the same probe counts what else does.
TRACING = "/sys/kernel/tracing"
PROBE = "ask_ipsec_gso"
ENABLE = f"{TRACING}/events/kprobes/{PROBE}/enable"
# Seconds of the quiet window the other windows' noise is judged by.
QUIET = 5


def sender(source):
    return f'''
import json, socket
pattern = bytes(range(256)) * 258
s = socket.create_connection(({INNER!r}, {PORT}), timeout=30, source_address=({source!r}, 0))
sent = 0
while sent < {TOTAL}:
    n = min(65536, {TOTAL} - sent)
    s.sendall(pattern[sent % 256:sent % 256 + n])
    sent += n
s.shutdown(socket.SHUT_WR)
reply = b''
while not reply.endswith(b'\\n'):
    chunk = s.recv(64)
    if not chunk:
        break
    reply += chunk
s.close()
print(json.dumps({{'sent': sent, 'reply': reply.decode().strip()}}))
'''


class Sink:
    """Counts each connection's bytes, checks them against the pattern, and
    answers with the count once the sender has finished."""

    def __init__(self):
        self.received = {}

    async def __call__(self, reader, writer):
        peer = writer.get_extra_info("peername")[0]
        count, intact = 0, True
        while chunk := await reader.read(65536):
            offset = count % 256
            intact = intact and chunk == PATTERN[offset:offset + len(chunk)]
            count += len(chunk)
        self.received[peer] = {"bytes": count, "intact": intact}
        writer.write(f"{count}\n".encode())
        await writer.drain()
        writer.close()


async def counters(r):
    return {
        "out_error": xfrm_mib(await read(r.target, r.session, "/proc/net/xfrm_stat"))["XfrmOutError"],
        "toenc": await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc"),
    }


async def gso_hits(console):
    """Times PROBE has fired, from kprobe_profile."""
    text = (await console_command(console, "cat", f"{TRACING}/kprobe_profile"))["stdout"]
    return next(int(line.split()[1]) for line in text.splitlines() if line.split()[:1] == [PROBE])


async def gso_mark(console):
    """PROBE's hits so far, and when they were read."""
    return await gso_hits(console), time.monotonic()


async def gso_window(console, mark):
    """PROBE's hits and the seconds elapsed since `mark`, and the mark to
    measure the next window from."""
    now = await gso_mark(console)
    return {"hits": now[0] - mark[0], "seconds": now[1] - mark[1]}, now


async def offloads(r, iface):
    result = await command(r.target, r.session, "ethtool", "-k", iface)
    return {line.split(":")[0].strip(): line.split(":")[1].split()[0]
            for line in result["stdout"].splitlines() if ":" in line and line.split(":")[1].split()}


async def test_offloaded_tunnel_carries_bulk_tcp(rig):
    r = rig
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    outer = await wan_outer(r)
    sink = Sink()
    server = None
    cleanup = []

    async def step(agent, argv, undo):
        await command(agent, r.session, *argv)
        cleanup.append((agent, undo))

    try:
        # Both kinds of GSO packet have to exist for this to test anything:
        # merged on the LAN port, and built for a socket whose route leaves
        # by the WAN port.
        assert (await offloads(r, TARGET_LAN_IF))["generic-receive-offload"] == "on"
        assert (await offloads(r, TARGET_WAN_IF))["generic-segmentation-offload"] == "on"
        route = json.loads(r.lan.run(f"ip -j route get {INNER}", timeout=10).stdout.strip())[0]
        assert route.get("gateway") == r.lan_gateway, ("the LAN VM must reach INNER through the DUT",
                                                        route)
        await step(r.target, ["ip", "addr", "add", DUT_INNER + "/32", "dev", "lo"],
                   ["ip", "addr", "del", DUT_INNER + "/32", "dev", "lo"])
        await step(wan, ["ip", "route", "add", DUT_INNER + "/32", "via", outer, "dev", r.wan_if],
                   ["ip", "route", "del", DUT_INNER + "/32", "via", outer, "dev", r.wan_if])
        await install_tunnel(r, wan, outer, [r.lan_ip, DUT_INNER], step)
        server = await asyncio.start_server(sink, INNER, PORT)

        transfers, gso = {}, {}
        before = await counters(r)
        with Console.target(log_path=str(artifact_dir() / "ipsec-offload-tcp-uart.log")) as console:
            await asyncio.to_thread(console.login, "root", None)
            probe = enabled = False
            try:
                # A run that died before its cleanup leaves the probe behind,
                # and adding it again fails with EEXIST; an enabled probe
                # cannot be removed.
                await console_command(console, "sh", "-c", f"if [ -e {ENABLE} ]; then "
                                      f"echo 0 > {ENABLE} && echo '-:{PROBE}' >> "
                                      f"{TRACING}/kprobe_events; fi")
                await console_command(console, "sh", "-c", f"echo 'p:{PROBE} __skb_gso_segment' >> "
                                                           f"{TRACING}/kprobe_events")
                probe = True
                await console_command(console, "sh", "-c", f"echo 1 > {ENABLE}")
                enabled = True
                # Read on the console, so the agent's own replies over TCP
                # are not counted; the quiet window counts whatever else is.
                mark = await gso_mark(console)
                await asyncio.sleep(QUIET)
                gso["quiet"], mark = await gso_window(console, mark)
                result = await lan_run_python(r.lan, sender(r.lan_ip), timeout=90,
                                              label="ipsec_offload_tcp")
                assert result.rc == 0, result.stdout
                transfers["forwarded"] = json.loads(result.stdout.strip().splitlines()[-1])
                gso["forwarded"], mark = await gso_window(console, mark)
                result = await console_python(console, sender(DUT_INNER), timeout=90)
                transfers["local"] = console_json(result["stdout"].strip())
                gso["local"], mark = await gso_window(console, mark)
            finally:
                if enabled:
                    await console_command(console, "sh", "-c", f"echo 0 > {ENABLE}", check=False)
                if probe:
                    await console_command(console, "sh", "-c", f"echo '-:{PROBE}' >> "
                                                               f"{TRACING}/kprobe_events", check=False)
        moved = {name: value - before[name] for name, value in (await counters(r)).items()}
        # Each transfer's floor is what the quiet window's rate would have
        # counted over that transfer's own length, and one more.
        noise = gso["quiet"]["hits"] / gso["quiet"]["seconds"]
        for name in ("forwarded", "local"):
            gso[name]["floor"] = math.floor(noise * gso[name]["seconds"]) + 1
        record = {"transfers": transfers, "received": sink.received, "moved": moved, "gso": gso}
        r.record("ipsec-offload-tcp", record)

        for name, source in (("forwarded", r.lan_ip), ("local", DUT_INNER)):
            assert transfers[name] == {"sent": TOTAL, "reply": str(TOTAL)}, record
            assert sink.received.get(source) == {"bytes": TOTAL, "intact": True}, record
        # The DUT's own transfer put GSO packets through the software
        # segmentation, beyond what the rest of the DUT does in the same time.
        # The forwarded one is recorded but not required to: the DPAA receive
        # path hands a frame to GRO only once the FMan parser has validated its
        # L4 checksum (_dpa_process_parse_results()), which it does not for
        # these, so forwarded segments reach xfrm_output() one by one.
        assert gso["local"]["hits"] >= gso["local"]["floor"], record
        assert moved["out_error"] == 0, record
        # Every byte left in a frame SEC encrypted, one ESP per segment: at
        # least a frame per full-size segment of both transfers.
        assert moved["toenc"] >= 2 * TOTAL // 1500, record
    finally:
        if server:
            server.close()
            await server.wait_closed()
        for agent, argv in reversed(cleanup):
            await command(agent, r.session, *argv, check=False)

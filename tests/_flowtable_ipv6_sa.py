"""Shared support for flowtable ipv6 sa."""

from __future__ import annotations

import json
import os
import secrets

from _flowtable_rig import command, read
from _flowtable_service_ipsec_replay import xfrm_mib
from _ipsec_inbound_flow_offload import crypto
from _topology import LAN_IPV6, TARGET_WAN_IF, WAN_IPV6

SPORT, DPORT = 48960, 48961
V4_WAN_SPORT, V4_WAN_DPORT = 48962, 48963
REQIDS = {"out": "49411", "in": "49412"}
# The far end's IPv6 address when the WAN carries no IPv6: on the WAN host's
# loopback, in a prefix no segment of the rig uses.
REMOTE_V6 = "fc00:a6::99"
REMOTE_PREFIX = "fc00:a6::/64"
# The bundle's MTU for AES-CBC and a 128-bit HMAC-SHA256 tag over an IPv4
# outer header: ((1500 - 20 - 8 - 16 - 16) & ~15) - 2.
BUNDLE_MTU = 1438
FITS = BUNDLE_MTU - 40 - 8


async def fragments_sent(r):
    text = await read(r.target, r.session, "/proc/ucode_frag/stats")
    return {family: int(text.split(f"Number of IPv{family} fragments sent :")[1].split()[0])
            for family in (4, 6)}


async def sa_pair(r, cleanup, remote=WAN_IPV6):
    """A tunnel-mode SA pair between the DUT's WAN address and the WAN host,
    selecting the IPv6 flow between the LAN VM and `remote`, an IPv6 address
    of the WAN host. The DUT's half is packet-offloaded; the WAN host's is
    ordinary software."""
    outer = next(a["local"] for i in json.loads((await command(
        r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", TARGET_WAN_IF))["stdout"])
        for a in i["addr_info"] if a["family"] == "inet")
    peer = os.environ["ASK_WAN_IPERF_IP"]

    async def add(agent, kind, identity, *options):
        await command(agent, r.session, "ip", "xfrm", kind, "add", *identity, *options)
        cleanup.append((agent, ["ip", "xfrm", kind, "delete", *identity]))

    for direction in ("out", "in"):
        spi = hex(0xA6000000 | secrets.randbits(24))
        outer_src, outer_dst = (outer, peer) if direction == "out" else (peer, outer)
        src, dst = (LAN_IPV6, remote) if direction == "out" else (remote, LAN_IPV6)
        state = ["src", outer_src, "dst", outer_dst, "proto", "esp", "spi", spi]
        # A state's selector takes the outer family unless told otherwise, and
        # xfrm hands a flow only a state whose selector is the flow's family.
        inner = ["sel", "src", "::/0", "dst", "::/0"]
        await add(r.wan, "state", state, *crypto(REQIDS[direction]), *inner, "replay-window", "32")
        window = ["replay-window", "32"] if direction == "in" else []
        await add(r.target, "state", state, *crypto(REQIDS[direction]), *inner, *window,
                  "offload", "packet", "dev", TARGET_WAN_IF, "dir", direction)
        selector = ["src", src + "/128", "dst", dst + "/128"]
        template = ["tmpl", "src", outer_src, "dst", outer_dst, "proto", "esp", "mode", "tunnel",
                    "reqid", REQIDS[direction], "level", "required"]
        await add(r.wan, "policy", [*selector, "dir", "in" if direction == "out" else "out"], *template)
        await add(r.target, "policy", [*selector, "dir", direction], *template,
                  "offload", "packet", "dev", TARGET_WAN_IF)
        if direction == "in":
            await add(r.target, "policy", [*selector, "dir", "fwd"], *template)


async def xfrm_counters(r):
    return xfrm_mib(await read(r.target, r.session, "/proc/net/xfrm_stat"))

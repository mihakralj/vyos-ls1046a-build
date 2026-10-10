"""Shared support for ipsec inbound flow offload."""

from __future__ import annotations

import binascii

LAN_INNER = "198.18.96.2"
PORT = 48901
REQID_OUT, REQID_IN = "48901", "48902"
COUNT = 60
# Packets that legitimately travel in software before the entries exist.
# A bidirectional tunnel needs more of these than a plain flow: conntrack must
# confirm, and only an original-direction packet can create the flow at all --
# every reply carries a sec_path and nft_flow_offload_skip() declines it. What
# separates the fast path from the slow one is that the software counter stops
# after this handful rather than tracking the whole transfer, not its exact
# value, so the bound is generous and the assertions below lean on "stopped".
SETUP = 5
# Full-size frames, deliberately. The classifier checks the size of what it
# *transmits* against the MTU programmed in the entry, and for a direction
# handed to SEC that is the outer frame -- the tunnel expansion is added before
# the comparison. A short payload fits whatever is programmed, so a wrong MTU
# excepts nothing and every assertion below passes while the hardware quietly
# hands each frame to the CPU. This payload puts the inner datagram close
# enough to the tunnel's own MTU that the sum exceeds a tunnel-reduced bound
# and stays inside the port's, which is the difference the two make.
PAYLOAD = 1400
CIPHER = "0x" + "a5" * 16
AUTH = "0x" + "5a" * 32
TABLE = "ask_ipsec_inbound"


def quoted(*argv):
    """Shell-safe join: algorithm names carry parentheses."""
    import shlex
    return " ".join(shlex.quote(str(word)) for word in argv)


def crypto(reqid):
    return ["mode", "tunnel", "reqid", reqid,
            "enc", "cbc(aes)", CIPHER,
            "auth-trunc", "hmac(sha256)", AUTH, "128"]


async def sec_counter(session, agent, iface, name):
    """`tx toenc`/`tx todec` across CPUs: frames the software path gave SEC."""
    result = await agent.exec_cmd(session, ["ethtool", "-S", iface])
    assert result["rc"] == 0, result
    return sum(int(line.split(":")[1])
               for line in result["stdout"].splitlines()
               if line.strip().startswith(f"{name} [CPU"))


async def flows(session, agent):
    """The adapter's installed directions, as dicts of its own key=value row."""
    result = await agent.fs_read(session, "/proc/cdx_flowtable")
    text = binascii.unhexlify(result.get("content_hex") or b"").decode(errors="replace")
    return [dict(field.split("=", 1) for field in line.split()[1:] if "=" in field)
            for line in text.splitlines() if line.startswith("flow ")]

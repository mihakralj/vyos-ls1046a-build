"""A tunnelled flow is steered to SEC by the classifier, not by the stack.

Step 4 proved SEC can encrypt: every packet crossed the CPU, was handed to the
engine, and came back. This is the fast path, where the classifier entry itself
names the SA and the frames never reach the driver's transmit function at all.

The oracle that separates the two is `tx toenc`, which counts frames the
*software* path handed to SEC. On the slow path it advances once per packet. On
the fast path it advances exactly **once** -- for the packet that travelled
before the hardware entry existed -- and then stops while the rest of the
transfer goes through. Throughput cannot tell the two apart; this can.

The tunnel runs between the DUT and the LAN VM, and the traffic is *forwarded*
by the DUT rather than generated on it, which is the shape a gateway actually
has. The test host is the far end of the forwarded flow, not of the tunnel.

Also asserted here, because it is what this increment exists to prevent: a flow
whose policy resolves to no usable SA must be refused, never installed in the
clear. Admission used to ask the *destination* whether it was transformed
(`dst_xfrm`), which is never true for a forwarded flow -- those are routed
plainly and transformed later at POSTROUTING -- so a flow under a `level
required` policy was offloaded and forwarded unencrypted.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import textwrap
import time

import pytest

from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF

LAN_INNER = "198.18.88.2"
PORT = 48801
REQID = 48801
COUNT = 60
CIPHER = b"\xa5" * 16
AUTH = b"\x5a" * 32
TABLE = "ask_ipsec_flow"


def quoted(*argv):
    """Shell-safe join: algorithm names carry parentheses."""
    import shlex
    return " ".join(shlex.quote(str(word)) for word in argv)


async def toenc(session, agent, iface):
    result = await agent.exec_cmd(session, ["ethtool", "-S", iface])
    assert result["rc"] == 0, result
    return sum(int(line.split(":")[1])
               for line in result["stdout"].splitlines()
               if line.strip().startswith("tx toenc [CPU"))


async def flows(session, agent):
    """The adapter's installed directions, as dicts of its own key=value row."""
    result = await agent.fs_read(session, "/proc/cdx_flowtable")
    text = bytes.fromhex(result.get("content_hex", "")).decode(errors="replace")
    rows = []
    for line in text.splitlines():
        if not line.startswith("flow "):
            continue
        rows.append(dict(field.split("=", 1)
                         for field in line.split()[1:] if "=" in field))
    return rows


@pytest.mark.usefixtures("splat_window")
async def test_tunnelled_flow_is_steered_to_sec(
    aiohttp_session, target_agent, lan, record_property,
):
    session = aiohttp_session
    spi = 0x0A880000 | (int.from_bytes(os.urandom(2), "big") or 1)

    async def dut(*argv, check=True):
        result = await target_agent.exec_cmd(session, list(argv))
        if check:
            assert result["rc"] == 0, (argv, result)
        return result

    lan_outer = next(
        field.split("/")[0]
        for field in lan.run(f"ip -o -4 addr show dev {LAN_NIC}", timeout=20).stdout.split()
        if field.count(".") == 3 and "/" in field)
    dut_lan = next(
        address["local"]
        for interface in json.loads(
            (await dut("ip", "-j", "-4", "addr", "show", "dev", TARGET_LAN_IF))["stdout"])
        for address in interface["addr_info"] if address["family"] == "inet")
    here = os.environ["ASK_WAN_IPERF_IP"]
    record_property("tunnel", f"{dut_lan} <-> {lan_outer}, inner {here} -> {LAN_INNER}")

    state = ("src", dut_lan, "dst", lan_outer, "proto", "esp", "spi", hex(spi))
    crypto = ("mode", "tunnel", "reqid", str(REQID),
              "enc", "cbc(aes)", "0x" + CIPHER.hex(),
              "auth-trunc", "hmac(sha256)", "0x" + AUTH.hex(), "128")
    selector = ("src", here + "/32", "dst", LAN_INNER + "/32")
    tmpl = ("tmpl", "src", dut_lan, "dst", lan_outer, "proto", "esp",
            "mode", "tunnel", "reqid", str(REQID), "level", "required")

    results = f"/tmp/ask_ipsec_flow_{os.getpid()}.json"
    echo = f"/tmp/ask_ipsec_echo_{os.getpid()}.py"
    cleanup = []
    try:
        # --- the LAN VM: tunnel far end, and the echo the flow talks to
        lan.run("ip xfrm state flush; ip xfrm policy flush", timeout=25)
        lan.run(f"ip address add {LAN_INNER}/32 dev lo 2>/dev/null; true", timeout=20)
        lan.run("ip xfrm state add " + quoted(*state, *crypto, "replay-window", "32"),
                timeout=25)
        lan.run("ip xfrm policy add " + quoted(*selector, "dir", "in", *tmpl), timeout=25)
        cleanup.append(lambda: lan.run(
            "ip xfrm state flush; ip xfrm policy flush; "
            f"ip address del {LAN_INNER}/32 dev lo 2>/dev/null; "
            f"pkill -f '^python3 {echo}$'; rm -f {echo} {echo}.log {results}; true", timeout=25))
        script = textwrap.dedent(f"""
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(({LAN_INNER!r}, {PORT}))
            sock.settimeout(120)
            print('READY', flush=True)
            while True:
                data, peer = sock.recvfrom(2048)
                sock.sendto(data, peer)
        """)
        compile(script, echo, "exec")
        staged = lan.run("echo " + base64.b64encode(script.encode()).decode()
                         + f" | base64 -d > {echo}", timeout=20)
        assert staged.rc == 0, staged.stdout
        started = lan.run(f"setsid python3 {echo} >{echo}.log 2>&1 & sleep 1; cat {echo}.log", timeout=25)
        assert started.rc == 0 and started.stdout.strip() == "READY", started.stdout

        # --- the DUT: offload the flowtable, then the SA and its policy
        # A flowtable binds its devices exclusively, so any table left behind
        # by another run would refuse this one's bind rather than fail loudly.
        await dut("nft", "delete", "table", "inet", TABLE, check=False)
        await dut("nft", "delete", "table", "inet", "ask", check=False)
        await dut("ip", "xfrm", "policy", "flush")
        await dut("ip", "xfrm", "state", "flush")
        await dut("nft", f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }};
 flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip protocol udp flow add @fast
 }}
}}''')
        cleanup.append(lambda: dut("nft", "delete", "table", "inet", TABLE, check=False))
        await dut("ip", "route", "replace", LAN_INNER + "/32", "via", lan_outer)
        cleanup.append(lambda: dut("ip", "route", "del", LAN_INNER + "/32", check=False))
        await dut("ip", "xfrm", "state", "add", *state, *crypto,
                  "offload", "packet", "dev", TARGET_LAN_IF, "dir", "out")
        await dut("ip", "xfrm", "policy", "add", *selector, "dir", "out", *tmpl,
                  "offload", "packet", "dev", TARGET_LAN_IF)
        cleanup.append(lambda: dut("ip", "xfrm", "policy", "flush", check=False))
        cleanup.append(lambda: dut("ip", "xfrm", "state", "flush", check=False))

        host_route = [LAN_INNER + "/32", "via", os.environ["ASK_TARGET_IP"]]
        existing = json.loads(subprocess.check_output(["ip", "-j", "route", "show", "exact", host_route[0]]))
        if existing:
            assert len(existing) == 1 and existing[0].get("gateway") == host_route[2], existing
        else:
            subprocess.run(["ip", "route", "add", *host_route], check=True, capture_output=True)
            cleanup.append(lambda: subprocess.run(["ip", "route", "del", *host_route],
                                                   check=True, capture_output=True))

        before = await toenc(session, target_agent, TARGET_LAN_IF)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        echoed = 0
        for _ in range(COUNT):
            sock.sendto(b"y" * 512, (LAN_INNER, PORT))
            try:
                sock.recv(2048)
                echoed += 1
            except OSError:
                pass
            time.sleep(0.02)
        sock.close()
        after = await toenc(session, target_agent, TARGET_LAN_IF)
        rows = await flows(session, target_agent)

        forward = [row for row in rows if row.get("out") == TARGET_LAN_IF]
        reverse = [row for row in rows if row.get("out") == TARGET_WAN_IF]
        record_property("echoed", echoed)
        record_property("toenc_delta", after - before)
        record_property("flows", "; ".join(
            f"{r.get('in')}->{r.get('out')} sa={r.get('sa')} packets={r.get('packets')}"
            for r in rows))

        assert forward, f"the tunnelled direction was not offloaded: {rows}"
        assert forward[0]["sa"] != "0", (
            "the tunnelled direction was offloaded with no SA, which forwards in "
            f"hardware what the policy says to encrypt: {forward[0]}")
        assert reverse and reverse[0]["sa"] == "0", (
            f"the return direction has no policy and must carry no SA: {reverse}")
        assert echoed >= COUNT - 2, f"only {echoed} of {COUNT} echoed"
        # One, not COUNT: the packet that travelled before the entry existed.
        # More than a handful means the classifier is not doing the steering.
        assert after - before <= 2, (
            f"{after - before} frames reached SEC through the software path; the "
            f"classifier should have steered all but the first")
    finally:
        for undo in reversed(cleanup):
            try:
                result = undo()
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                pass

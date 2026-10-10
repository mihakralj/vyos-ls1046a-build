"""A packet-offload tunnel carries traffic, and SEC is what carries it.

The tunnel runs between the DUT and the LAN VM, which is the right shape for
this: both ends are ordinary hosts on one segment and nothing in the path
belongs to the machine running the test. An earlier revision put the far end
on the orchestrator itself, where the decrypted datagrams reached the IP layer
and were then dropped by that host's own input path -- a bench artefact that
said nothing about the offload.

Only the DUT is offloaded. The LAN VM is plain software IPsec and has no idea
its peer is not, so its decryption is an independent check on what SEC emitted.

Three oracles, each answering a different question:

  - `tx toenc` on the DUT's LAN port advances by the packet count, so SEC
    produced the frames rather than the software crypto path;
  - the LAN VM's SA decrypts the same count, so those frames were well-formed
    ESP for this SPI and not merely emitted; and
  - the payloads arrive intact, so the inner packets survived the round trip.

No flowtable entry is involved: every packet goes through the stack and is
handed to SEC on the way out, which is what makes this the slow path.
"""

from __future__ import annotations

from _ipsec_packet_offload_traffic import (
    AUTH,
    CIPHER,
    COUNT,
    DUT_INNER,
    LAN_INNER,
    PORT,
    REQID,
    decrypted_packets,
    toenc,
)

import base64
import json
import os
import secrets
import shlex
import struct
import textwrap
from contextlib import AsyncExitStack

import pytest

from ask_orch.uart import Console
from _topology import LAN_NIC, TARGET_LAN_IF


@pytest.mark.usefixtures("splat_window")
async def test_packet_offload_tunnel_carries_traffic(
    aiohttp_session, target_agent, lan, record_property,
):
    session = aiohttp_session
    spi = 0x0A870000 | (secrets.randbelow(65535) + 1)

    async def dut(*argv, check=True):
        result = await target_agent.exec_cmd(session, list(argv))
        if check:
            assert result["rc"] == 0, (argv, result)
        return result

    def lan_sh(command, timeout=20):
        return lan.run(command, timeout=timeout)

    def sh(*argv):
        """Shell-safe join: algorithm names carry parentheses, which bash
        would otherwise read as syntax."""
        return " ".join(shlex.quote(str(word)) for word in argv)

    # Both outer endpoints are discovered rather than assumed: the LAN
    # segment's addressing belongs to the bench, not to this test.
    dut_outer = next(
        address["local"]
        for interface in json.loads(
            (await dut("ip", "-j", "-4", "addr", "show", "dev", TARGET_LAN_IF))["stdout"])
        for address in interface["addr_info"] if address["family"] == "inet")
    lan_show = lan_sh(f"ip -o -4 addr show dev {LAN_NIC}")
    lan_outer = next(
        field.split("/")[0]
        for field in lan_show.stdout.split()
        if field.count(".") == 3 and "/" in field)
    record_property("endpoints", f"{dut_outer} <-> {lan_outer}")

    state = ("src", dut_outer, "dst", lan_outer, "proto", "esp", "spi", hex(spi))
    crypto = ("mode", "tunnel", "reqid", str(REQID),
              "enc", "cbc(aes)", "0x" + CIPHER.hex(),
              "auth-trunc", "hmac(sha256)", "0x" + AUTH.hex(), "128")
    selector = ("src", DUT_INNER + "/32", "dst", LAN_INNER + "/32")
    tmpl = ("tmpl", "src", dut_outer, "dst", lan_outer, "proto", "esp",
            "mode", "tunnel", "reqid", str(REQID), "level", "required")

    async with AsyncExitStack() as cleanup:
        # The DUT masquerades everything leaving it; exempt the inner selector
        # or the source is rewritten before the policy ever sees it.
        nat = ("-s", DUT_INNER + "/32", "-d", LAN_INNER + "/32", "-j", "ACCEPT")
        await dut("iptables", "-t", "nat", "-I", "POSTROUTING", *nat)
        cleanup.push_async_callback(dut, "iptables", "-t", "nat", "-D",
                                    "POSTROUTING", *nat, check=False)

        await dut("ip", "address", "add", DUT_INNER + "/32", "dev", "lo")
        cleanup.push_async_callback(dut, "ip", "address", "del",
                                    DUT_INNER + "/32", "dev", "lo", check=False)
        await dut("ip", "route", "add", LAN_INNER + "/32", "via", lan_outer)
        cleanup.push_async_callback(dut, "ip", "route", "del",
                                    LAN_INNER + "/32", check=False)

        result = lan_sh("; ".join([
            f"ip address add {LAN_INNER}/32 dev lo",
            f"ip route add {DUT_INNER}/32 via {dut_outer}",
            "ip xfrm state add " + sh(*state, *crypto, "replay-window", "32"),
            "ip xfrm policy add " + sh(*selector, "dir", "in", *tmpl),
        ]), timeout=30)
        assert result.rc == 0, result.stdout

        def lan_teardown():
            lan_sh("; ".join([
                "ip xfrm policy delete " + sh(*selector, "dir", "in"),
                "ip xfrm state delete " + sh(*state),
                f"ip route del {DUT_INNER}/32",
                f"ip address del {LAN_INNER}/32 dev lo",
            ]) + " 2>/dev/null; true", timeout=30)
        cleanup.callback(lan_teardown)

        # The DUT's state and policy are both offloaded. The policy has to be:
        # xfrm_state_find() skips a packet-offloaded state whenever the policy
        # that reached it is not offloaded too, so an SA paired with a software
        # policy is never selected and the tunnel carries nothing.
        await dut("ip", "xfrm", "state", "add", *state, *crypto,
                  "offload", "packet", "dev", TARGET_LAN_IF, "dir", "out")
        cleanup.push_async_callback(dut, "ip", "xfrm", "state", "delete",
                                    *state, check=False)
        await dut("ip", "xfrm", "policy", "add", *selector, "dir", "out", *tmpl,
                  "offload", "packet", "dev", TARGET_LAN_IF)
        cleanup.push_async_callback(dut, "ip", "xfrm", "policy", "delete",
                                    *selector, "dir", "out", check=False)

        before = await toenc(session, target_agent, TARGET_LAN_IF)

        # Receiver first, backgrounded on the LAN VM so the sender cannot race
        # it. The bounded background receiver writes its result to a file
        # that the test reads after sending.
        results = f"/tmp/ask_ipsec_offload_{os.getpid()}.json"
        staged = f"/tmp/ask_ipsec_recv_{os.getpid()}.py"
        receiver = textwrap.dedent(f'''
            import json, socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(({LAN_INNER!r}, {PORT}))
            sock.settimeout(20)
            received = []
            try:
                for _ in range({COUNT}):
                    received.append(sock.recv(2048).hex())
            except Exception:
                pass
            finally:
                sock.close()
                open({results!r}, "w").write(json.dumps(received))
        ''')
        blob = base64.b64encode(receiver.encode()).decode()
        result = lan_sh(f"echo {blob} | base64 -d > {staged} && echo STAGED")
        assert "STAGED" in result.stdout, result.stdout
        lan_sh(f"rm -f {results}; setsid python3 {staged} >/dev/null 2>&1 & sleep 1")
        cleanup.callback(lambda: lan_sh(f"rm -f {staged} {results}"))

        payloads = [struct.pack("!I", index) + bytes(range(256))
                    for index in range(COUNT)]
        sender = textwrap.dedent(f'''
            import socket, struct, time
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind(({DUT_INNER!r}, 0))
                for index in range({COUNT}):
                    sock.sendto(struct.pack('!I', index) + bytes(range(256)),
                                ({LAN_INNER!r}, {PORT}))
                    time.sleep(0.01)
            print('sent')
        ''')
        with Console.target() as console:
            console.login("root", None)
            result = console.run("python3 -c " + shlex.quote(sender), timeout=30)
            assert "sent" in result.stdout, result.stdout

        received = []
        for _ in range(25):
            probe = lan_sh(f"cat {results} 2>/dev/null; echo", timeout=20)
            body = probe.stdout.strip()
            start = body.find("[")
            if start >= 0:
                try:
                    received = [bytes.fromhex(item)
                                for item in json.loads(body[start:body.rfind("]") + 1])]
                except ValueError:
                    received = []
                else:
                    break

        after = await toenc(session, target_agent, TARGET_LAN_IF)
        peer = lan_sh("ip -s xfrm state", timeout=30)
        peer_decrypted = decrypted_packets(peer.stdout, spi)
        record_property("sec_frames", after - before)
        record_property("peer_decrypted", peer_decrypted)
        record_property("delivered", len(received))

        assert after - before == COUNT, (
            f"SEC handed {after - before} frames, expected {COUNT}: the tunnel "
            f"carried traffic but the software crypto path may have done it")
        assert peer_decrypted == COUNT, (
            f"the LAN VM decrypted {peer_decrypted} of {COUNT}: SEC produced "
            f"frames its peer could not authenticate")
        assert received == payloads

        # Reinstalling the same SPI must work once the SA has carried traffic.
        #
        # This is here rather than in a test of its own because the traffic
        # above is the precondition: a fresh SA tears down cleanly, and only
        # one that has handed frames to SEC exposed the fault. Those frames
        # each put the state on an skb's sec_path, and the DPAA submit path
        # frees that skb lazily -- the buffer's next user frees it -- so on an
        # idle tunnel the references outlived the flush, xdo_dev_state_free()
        # never ran, and the classifier entry stayed. The next SA with the
        # same key was then refused by the hash table with "Resource Already
        # Exists", not by any check of ours.
        #
        # Note the SPI is random per run, which is what let this hide: every
        # run installed a key nothing held. Reusing it deliberately is the
        # whole point of the check.
        await dut("ip", "xfrm", "state", "delete", *state)
        reinstalled = await dut("ip", "xfrm", "state", "add", *state, *crypto,
                                "offload", "packet", "dev", TARGET_LAN_IF,
                                "dir", "out", check=False)
        assert reinstalled["rc"] == 0, (
            "the same SPI could not be reinstalled after the SA carried "
            f"traffic; its hardware entry outlived it: {reinstalled}")

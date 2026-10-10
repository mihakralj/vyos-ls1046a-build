"""Both flowtable paths must match the negotiated PPPoE peer and session."""
import json
import socket

import pytest
import pytest_asyncio

from _topology import LAN_IPV6, TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_rig import (DPORT, SPORT, TABLE, command, read, stop_boot_daemon)
from _flowtable_pppoe import (DPORT6, INNER_LOCAL, INNER_LOCAL6, SERVER_IF, SPORT6, _exchange6)


@pytest_asyncio.fixture(autouse=True)
async def controlled_policy():
    await stop_boot_daemon()


@pytest.mark.parametrize("pppoe_rig", ["udp", "ipv6"], indirect=True)
@pytest.mark.parametrize("hardware", [False, True], ids=["software", "hardware"])
async def test_pppoe_receive_identity(pppoe_rig, hardware):
    from scapy.all import Ether, IP, IPv6, PPP, PPPoE, UDP

    r = pppoe_rig
    ipv6 = r.session_ipv6
    local, remote = (LAN_IPV6, INNER_LOCAL6) if ipv6 else (r.lan_ip, INNER_LOCAL)
    sport, dport = (SPORT6, DPORT6) if ipv6 else (SPORT, DPORT)
    family = "ipv6" if ipv6 else "ipv4"
    echo = r.echo6 if ipv6 else r.echo
    await r.nft(f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }};
 {"flags offload;" if hardware else ""} }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 {"ip6" if ipv6 else "ip"} saddr {local} udp sport {sport} udp dport {dport} flow add @fast
 }}
}}''')
    for _ in range(10):
        await (_exchange6(r, 4) if ipv6 else r.exchange(count=4))
        state = await r.state()
        listing = await command(r.target, r.session, "conntrack", "-L", "-f", family,
                                "-p", "udp", "--orig-src", local, "--sport", str(sport))
        if (state["entries"] == 1 if hardware
                else "[OFFLOAD]" in listing["stdout"]):
            break
    if hardware:
        # The download alone: a UDP upload into the session's 1492 is
        # Linux's in either family, the LAN port delivering a full frame.
        assert state["entries"] == 1, state
        cookie = next(f["cookie"] for f in state["flows"] if f["in"] == TARGET_WAN_IF)
    else:
        assert state["entries"] == state["bindings"] == 0, state
        assert "[OFFLOAD]" in listing["stdout"], listing
        cookie = None

    async def packets():
        flows = (await r.state())["flows"]
        if not hardware:
            assert not flows, flows
            return 0
        return int(next(f["packets"] for f in flows if f["cookie"] == cookie))

    destination = (await read(r.target, r.session,
                              "/sys/class/net/eth4/address")).strip()
    session_id, peer = r.session_identity
    original = echo.datagram_received
    reports = {}
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as wire:
        wire.bind((SERVER_IF, 0))
        for kind, sid, mac in (("valid-before", session_id, peer),
                               ("wrong-session", session_id ^ 0x8000, peer),
                               ("wrong-peer", session_id, "02:00:00:00:fe:01"),
                               ("valid-after", session_id, peer)):
            sent = 0

            def reply(data, addr):
                nonlocal sent
                frame = (Ether(src=mac, dst=destination) / PPPoE(sessionid=sid) /
                         PPP(proto=0x57 if ipv6 else 0x21) /
                         (IPv6 if ipv6 else IP)(src=remote, dst=local) /
                         UDP(sport=dport, dport=sport) / data)
                wire.send(bytes(frame))
                sent += 1

            echo.datagram_received = reply
            try:
                before = await packets()
                result = await r.run_peer(f'''
import json, socket
s = socket.socket(socket.{"AF_INET6" if ipv6 else "AF_INET"}, socket.SOCK_DGRAM)
s.bind(({local!r}, {sport}))
s.settimeout(0.5)
received = 0
for n in range(4):
    payload = ({kind!r} + str(n)).encode()
    s.sendto(payload, ({remote!r}, {dport}))
    try:
        data, peer = s.recvfrom(2048)
    except TimeoutError:
        continue
    assert data == payload and peer[:2] == ({remote!r}, {dport})
    received += 1
print(json.dumps({{"received": received}}))
''', timeout=15, label="pppoe_identity")
                assert result.rc == 0, result.stdout
                after = await packets()
                reports[kind] = {**json.loads(result.stdout.strip()), "sent": sent,
                                 "hardware": after - before}
            finally:
                echo.datagram_received = original
    r.record(f"pppoe-receive-identity-{family}-{hardware}", reports)
    for kind, report in reports.items():
        expected = 4 if kind.startswith("valid-") else 0
        assert report == {"received": expected, "sent": 4, "hardware": expected if hardware else 0}, (kind, reports)

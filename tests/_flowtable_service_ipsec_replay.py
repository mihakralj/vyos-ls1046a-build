"""Shared support for flowtable service ipsec replay."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import os
import re
import secrets
import socket
import struct
from collections import Counter
from pathlib import Path

from _flowtable_rig import DPORT, WAN_IP, command, read
from _flowtable_service import FIRST
from _flowtable_service_ipsec import (
    INNER,
    LAN_INNER,
    REQIDS,
    Transform,
    Wire,
    replay_drops,
    sec_counter,
)
from _ipsec_helpers import sa_add, sa_replay_state
from _topology import TARGET_WAN_IF, lan_run

# An AES-128 key followed by the four-byte salt RFC 4106 and 4543 take with it.
GCM_KEY = "0x" + "c3" * 20
# The AEAD transforms SEC carries. GMAC (rfc4543) is not one of them: see
# test_ipsec_gmac_refused.
AEAD = {
    "rfc4106-icv16": Transform(("aead", "rfc4106(gcm(aes))", GCM_KEY, "128")),
    "rfc4106-icv8": Transform(("aead", "rfc4106(gcm(aes))", GCM_KEY, "64")),
}
GMAC = ("aead", "rfc4543(gcm(aes))", GCM_KEY, "128")
# Counters on this host that move when it refuses a frame the DUT produced.
PEER_ERRORS = ("XfrmInError", "XfrmInHdrError", "XfrmInNoStates", "XfrmInStateProtoError",
               "XfrmInStateSeqError", "XfrmInStateMismatch", "XfrmInStateInvalid", "XfrmInTmplMismatch")


def xfrm_mib(text):
    return {name: int(value) for name, value in (line.split() for line in text.splitlines() if line.strip())}


def peer_errors(before):
    """What this host, the peer that decrypts the DUT's frames, refused since
    `before`."""
    now = xfrm_mib(Path("/proc/net/xfrm_stat").read_text())
    return {name: now[name] - before[name] for name in PEER_ERRORS if now[name] != before[name]}


async def sa_state(r, spi, direction="out"):
    """`ip -s xfrm state` figures for one of the DUT's SAs, or None once it is
    gone. Packet offload leaves them to the adapter's accounting pass, which
    publishes SEC's per-SA counters once a second."""
    result = await command(r.target, r.session, "ip", "-s", "xfrm", "state", "get",
                           *r.ipsec.state(direction, spi), check=False)
    if result["rc"]:
        return None
    text = result["stdout"]
    current = re.search(r"lifetime current:\s*(\d+)\(bytes\), (\d+)\(packets\)\s*add (\S+ \S+) use (-|\S+ \S+)", text)
    assert current, text
    legacy = re.search(r"anti-replay context: seq 0x([0-9a-f]+), oseq 0x([0-9a-f]+)", text)
    esn = re.search(r"oseq-hi 0x([0-9a-f]+), oseq 0x([0-9a-f]+)", text)
    oseq = (int(legacy.group(2), 16) if legacy else
            int(esn.group(1), 16) << 32 | int(esn.group(2), 16) if esn else None)
    stats = re.search(r"stats:\s*replay-window (\d+) replay (\d+) failed (\d+)", text)
    return {"bytes": int(current.group(1)), "packets": int(current.group(2)), "use": current.group(4),
            "oseq": oseq, "replay": [int(value) for value in stats.groups()] if stats else None}


async def offloaded(r):
    """Both of the fixture's SAs are in hardware and carry its transform."""
    states = await r.ipsec.states()
    assert len(states) == 2, states
    for state in states:
        assert r.ipsec.transform.algorithms[1] in state, state
        assert re.search(rf"crypto offload parameters: dev {TARGET_WAN_IF} dir (in|out) mode packet", state), state


def aead_label(r):
    algorithms = r.ipsec.transform.algorithms
    return f"ipsec-aead-{algorithms[1].split('(')[0]}-{algorithms[-1]}"


def _afalg(kind, name, key, iv, data, *, assoclen=0, authsize=0):
    with socket.socket(socket.AF_ALG, socket.SOCK_SEQPACKET, 0) as alg:
        alg.bind((kind, name))
        alg.setsockopt(socket.SOL_ALG, socket.ALG_SET_KEY, key)
        if authsize:
            alg.setsockopt(socket.SOL_ALG, socket.ALG_SET_AEAD_AUTHSIZE, None, authsize)
        operation, _ = alg.accept()
        with operation:
            operation.sendmsg_afalg([data], op=socket.ALG_OP_ENCRYPT, iv=iv, assoclen=assoclen)
            return operation.recv(len(data) + authsize)


def esp(algorithms, spi, seq, inner):
    """Tunnel-mode ESP for `inner`, from the header to the ICV (RFC 4303).

    `algorithms` are the `ip xfrm state` arguments the SA was installed with:
    AES-CBC with HMAC-SHA256, or RFC 4106 GCM, whose output already carries
    the header and IV it was given as associated data."""
    head = struct.pack("!II", spi, seq)
    if algorithms[0] == "aead":
        _, name, key, icv = algorithms
        assert name == "rfc4106(gcm(aes))", algorithms
        pad = -(len(inner) + 2) % 4
        plain = inner + bytes(range(1, pad + 1)) + bytes([pad, socket.IPPROTO_IPIP])
        iv = os.urandom(8)
        # The kernel's rfc4106 authenticates the associated data less its
        # trailing eight bytes, which is where ESP puts the IV.
        return _afalg("aead", name, bytes.fromhex(key[2:]), iv, head + iv + plain,
                      assoclen=len(head + iv), authsize=int(icv) // 8)
    _, cipher, cipher_key, _, auth, auth_key, bits = algorithms
    assert (cipher, auth) == ("cbc(aes)", "hmac(sha256)"), algorithms
    pad = -(len(inner) + 2) % 16
    plain = inner + bytes(range(1, pad + 1)) + bytes([pad, socket.IPPROTO_IPIP])
    iv = os.urandom(16)
    body = head + iv + _afalg("skcipher", cipher, bytes.fromhex(cipher_key[2:]), iv, plain)
    return body + hmac.new(bytes.fromhex(auth_key[2:]), body, hashlib.sha256).digest()[:int(bits) // 8]


# The injected datagrams leave the tunnel toward this LAN port, outside the
# service's offload scope: a flow whose every packet carries a sec_path is
# never offered to the flowtable anyway.
RPORT = FIRST + 8
LISTENER = "/tmp/ask-ipsec-replay-listener.py"
DELIVERED = "/tmp/ask-ipsec-replay-delivered"
LISTENER_PID = "/tmp/ask-ipsec-replay-listener.pid"
LISTENER_SECONDS = 240


def listener_script(marker):
    """Record every marked datagram reaching the LAN end as SPI and sequence."""
    return f"""
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(({LAN_INNER!r}, {RPORT}))
s.settimeout(0.5)
marker = bytes.fromhex({marker.hex()!r})
end = time.monotonic() + {LISTENER_SECONDS}
with open({DELIVERED!r}, "w") as out:
    out.write("READY\\n")
    out.flush()
    while time.monotonic() < end:
        try:
            data = s.recv(4096)
        except socket.timeout:
            continue
        if data.startswith(marker):
            out.write(data[len(marker):len(marker) + 8].hex() + "\\n")
            out.flush()
"""


async def delivered(r):
    result = await lan_run(r.lan, f"cat {DELIVERED}", 10)
    assert result.rc == 0 and "READY" in result.stdout, result.stdout
    return Counter((int(line[:8], 16), int(line[8:], 16))
                   for line in re.findall(r"^([0-9a-f]{16})\r?$", result.stdout, re.M))


async def arrivals(r, spi, seen, expected):
    """The sequence numbers of `spi` that reached the LAN end since `seen`.

    Waits for the expected count, then a little longer: a duplicate travels
    no slower than its original, so anything extra is in by then."""
    def since(now):
        return Counter({seq: count for (owner, seq), count in (now - seen).items() if owner == spi})
    got = Counter()
    for _ in range(15 if expected else 0):
        await asyncio.sleep(0.2)
        got = since(await delivered(r))
        if sum(got.values()) >= expected:
            break
    await asyncio.sleep(0.5 if expected else 1.0)
    return since(await delivered(r))


@contextlib.asynccontextmanager
async def lan_listener(r, marker):
    """The LAN end's record of marked datagrams, for as long as the block runs.
    A background process with a pidfile lets the listener outlive the command
    that starts it."""
    script = base64.b64encode(listener_script(marker).encode()).decode()
    started = await lan_run(r.lan, f"echo {script} | base64 -d > {LISTENER} && rm -f {DELIVERED} && "
                                   f"(nohup python3 {LISTENER} >/dev/null 2>&1 & echo $! > {LISTENER_PID}) && "
                                   f"for i in $(seq 1 25); do grep -q READY {DELIVERED} 2>/dev/null && break; "
                                   f"sleep 0.2; done; cat {DELIVERED}", 20)
    try:
        assert started.rc == 0 and "READY" in started.stdout, started.stdout
        yield
    finally:
        await lan_run(r.lan, f"kill $(cat {LISTENER_PID}) 2>/dev/null; rm -f {LISTENER} {DELIVERED} {LISTENER_PID}", 10)
        await command(r.target, r.session, "conntrack", "-D", "-p", "udp", "--orig-src", INNER,
                      "--orig-dst", LAN_INNER, "--dport", str(RPORT), check=False)


# The /proc/net/xfrm_stat counters the adapter folds SEC's protocol refusals
# into. Which of them a refusal lands in follows the class the FMan microcode
# counted it in, and that is not reliable -- replays have been measured in
# its catch-all, other_errs, which folds to XfrmInError -- so only their sum
# is held to account. It leaves out SEC's faults, which a replay burst does
# not cause; the exact count of every refusal is ipsec_sec_refused in
# /proc/cdx_flowtable.
SEC_REFUSAL_MIBS = ("XfrmInStateSeqError", "XfrmInStateProtoError", "XfrmInError", "XfrmOutStateSeqError")


async def dut_counters(r):
    snmp = [line.split()[1:] for line in (await read(r.target, r.session, "/proc/net/snmp")).splitlines()
            if line.startswith("Ip:")]
    mib = xfrm_mib(await read(r.target, r.session, "/proc/net/xfrm_stat"))
    state = await r.state()
    return {"todec": await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx todec"),
            "reasm": int(dict(zip(*snmp))["ReasmOKs"]),
            "refusals": sum(mib[name] for name in SEC_REFUSAL_MIBS),
            **{name: mib[name] for name in SEC_REFUSAL_MIBS},
            # Every class the microcode counted, and their exact total.
            **{key: value for key, value in state.items() if key.startswith("ipsec_sec_refused")}}


class Inbound:
    """One inbound SA on the DUT and the peer frames this host sends it."""

    def __init__(self, r, marker, algorithms, window):
        self.r, self.marker, self.algorithms, self.window = r, marker, algorithms, window
        self.spi = 0xAA000000 | secrets.randbits(24)
        self.frames = {}
        self.ident = secrets.randbits(16)

    async def install(self):
        # The fixture's receiving policy names this reqid, so what SEC
        # decrypts for this SA is forwarded as the fixture's own SA would be.
        # Windows above 32 reach the kernel as an ESN-format replay state.
        await self.r.ipsec.add(self.r.target, "state", self.r.ipsec.state("in", self.spi),
                               "mode", "tunnel", "reqid", REQIDS["in"], *self.algorithms,
                               "replay-window", str(self.window),
                               "offload", "packet", "dev", TARGET_WAN_IF, "dir", "in")

    def packet(self, seq):
        """The frame for `seq`, built once: every replay is byte-identical."""
        from scapy.all import IP, UDP, Raw
        if seq not in self.frames:
            payload = self.marker + struct.pack("!II", self.spi, seq)
            inner = IP(src=INNER, dst=LAN_INNER, ttl=64) / UDP(sport=DPORT, dport=RPORT) / Raw(payload.ljust(64, b"."))
            self.ident = (self.ident + 1) & 0xFFFF
            self.frames[seq] = IP(src=WAN_IP, dst=self.r.ipsec.outer, ttl=64, id=self.ident, proto=50) / Raw(
                esp(self.algorithms, self.spi, seq, bytes(inner)))
        return self.frames[seq]

    async def send(self, frames):
        from scapy.all import Ether, fragment, sendp
        wire = []
        for seq, form in frames:
            packet = self.packet(seq)
            if form == "fragments":
                # A fresh outer ID, so no reassembly queue can mix it with
                # another datagram. The outer header is not authenticated.
                self.ident = (self.ident + 1) & 0xFFFF
                packet = packet.copy()
                packet.id = self.ident
                parts = fragment(packet, fragsize=48)
                assert len(parts) >= 2, parts
            else:
                parts = [packet]
            wire += [Ether(src=self.r.wan_mac, dst=self.r.dut_wan_mac) / part for part in parts]
        await asyncio.to_thread(sendp, wire, iface=self.r.wan_if, inter=0.005, verbose=False)


def phase(name, frames, delivered_seqs, *, rejected=0):
    """What one burst must do: which sequence numbers reach the LAN end, how
    many frames SEC must refuse, and how many reach it through the CPU."""
    cpu = sum(1 for _, form in frames if form == "fragments")
    return {"name": name, "frames": frames, "delivered": Counter(delivered_seqs),
            "rejected": rejected, "cpu": cpu}


def whole(*seqs):
    return [(seq, "whole") for seq in seqs]


def fragments(*seqs):
    return [(seq, "fragments") for seq in seqs]


def both_feeders(base):
    """A 32-wide window across both feeders: each replay is refused by the
    feeder that did not see the original, in both orders, and the window's
    edge is where the configuration put it."""
    a, d, f = range(base, base + 8), range(base + 8, base + 16), range(base + 16, base + 24)
    edge = base + 100
    return [
        phase("fresh-whole", whole(*a), a),
        phase("replay-whole", whole(*a), [], rejected=8),
        phase("replay-fragments", fragments(*a), [], rejected=8),
        phase("fresh-fragments", fragments(*d), d),
        phase("replay-whole-after-fragments", whole(*d), [], rejected=8),
        phase("fresh-whole-after-replays", whole(*f), f),
        phase("edge", whole(edge + 40), [edge + 40]),
        # 35 behind the highest is past a 32-wide window, 20 behind is not.
        phase("too-old", whole(edge + 5), [], rejected=1),
        phase("late-inside", whole(edge + 20), [edge + 20]),
    ]


def exact(base, window):
    """A window SEC keeps at exactly its width ends where Linux ends it: a
    number window - 1 behind the highest is still taken, one window behind is
    not (xfrm_replay_check_bmp())."""
    top = base + window + 16
    return [
        phase("fresh", whole(base, top), [base, top]),
        phase("late-at-edge", whole(top - (window - 1)), [top - (window - 1)]),
        phase("too-old-at-edge", whole(top - window), [], rejected=1),
        phase("replay", whole(top), [], rejected=1),
    ]


def disabled(base):
    """A zero window turns anti-replay off: replays and late frames pass."""
    return [
        phase("fresh", whole(base), [base]),
        phase("replay-whole", whole(base), [base]),
        phase("replay-fragments", fragments(base), [base]),
        phase("ahead", whole(base + 100), [base + 100]),
        phase("far-behind", whole(base - 50), [base - 50]),
    ]


CBC = Transform().algorithms
REPLAY_CASES = [
    ("cbc-32", CBC, 32, both_feeders(1000)),
    ("rfc4106-32", AEAD["rfc4106-icv16"].algorithms, 32, both_feeders(1000)),
    ("cbc-64", CBC, 64, exact(2000, 64)),
    ("cbc-128", CBC, 128, exact(4000, 128)),
    ("cbc-0", CBC, 0, disabled(3000)),
]


# ------------------------------------------ the replay state xfrm hands a daemon

SENDER = "/tmp/ask-ipsec-replay-sender.py"


def sender_script(*, count=None, seconds=None):
    """Datagrams on flow 2's tuple, out through the fixture's outbound SA:
    `count` of them a millisecond apart, or as many as go in `seconds`.
    Flow 2's tuple is exempt from the WAN masquerade, which would take it out
    of the policy's selector. The inner echo's answers are discarded."""
    return f"""
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(({LAN_INNER!r}, {FIRST}))
s.setblocking(False)
count, seconds = {count!r}, {seconds!r}
payload, sent = b"r" * 200, 0
end = time.time() + (seconds or 3600)
while (count is None or sent < count) and time.time() < end:
    try:
        s.sendto(payload, ({INNER!r}, {DPORT}))
        sent += 1
    except BlockingIOError:
        time.sleep(0.0001)
    try:
        while True:
            s.recv(2048)
    except BlockingIOError:
        pass
    if count is not None:
        time.sleep(0.001)
print("sent", sent)
"""


async def send_out(r, **how):
    script = base64.b64encode(sender_script(**how).encode()).decode()
    result = await lan_run(r.lan, f"echo {script} | base64 -d > {SENDER} && python3 {SENDER}; rm -f {SENDER}",
                           (how.get("seconds") or 0) + 30)
    assert result.rc == 0 and "sent" in result.stdout, result.stdout


async def getsa(agent, r, identity):
    """XFRM_MSG_GETSA's replay state for a state in the legacy shape (a
    window of 32 or less), as `ip -s xfrm state get` prints it, and its
    packet count."""
    text = (await command(agent, r.session, "ip", "-s", "xfrm", "state", "get", *identity))["stdout"]
    replay = re.search(r"anti-replay context: seq 0x([0-9a-f]+), oseq 0x([0-9a-f]+), bitmap 0x([0-9a-f]+)", text)
    current = re.search(r"lifetime current:\s*\d+\(bytes\), (\d+)\(packets\)", text)
    assert replay and current, text
    return {"seq": int(replay[1], 16), "oseq": int(replay[2], 16), "bitmap": int(replay[3], 16),
            "packets": int(current[1])}


def esp_sequences(path, spi):
    """The sequence numbers of `spi`'s ESP frames in a pcap, in wire order.
    A plain walk, as reused_sequences() takes one: scapy takes minutes on a
    line-rate burst."""
    numbers = []
    data = Path(path).read_bytes()
    off = 24
    while off + 16 <= len(data):
        length = struct.unpack_from("<I", data, off + 8)[0]
        frame = data[off + 16:off + 16 + length]
        off += 16 + length
        l3 = 18 if frame[12:14] == b"\x81\x00" else 14
        if len(frame) >= l3 + 28 and frame[l3 + 9] == 50:
            ihl = (frame[l3] & 0xF) * 4
            owner, seq = struct.unpack_from("!II", frame, l3 + ihl)
            if owner == spi:
                numbers.append(seq)
    return numbers


def esp_capture(r, label):
    capture = Wire(r, label)
    capture.filter = f"ether src {r.dut_wan_mac} and ip proto 50"
    capture.snaplen = 64
    return capture


LIVE_ROUNDS = 5
LIVE_BLAST_SECONDS = 3
# Datagrams the old outbound SA sends between a daemon's read and its delete.
OUT_AFTER_READ = 500
# How long a re-add may take: the adapter waits up to five seconds for the
# old SA's retirement (FT_IPSEC_RETIRE_WAIT) before it refuses with -EBUSY,
# which has to come back as that refusal rather than as the agent timing out.
READD_TIMEOUT_MS = 7000


async def readd_outbound(r, ifindex, keys, label):
    """The fixture's outbound SA read, sending OUT_AFTER_READ more, deleted
    and added again from the reading; and then what the new one sends, which
    its peer, keeping its own SA, has to take."""
    out_spi = r.ipsec.active["out"]
    out = r.ipsec.state("out", out_spi)
    # After a blast the accounting pass is still catching oseq up to SEC's real
    # counter; the assertions below pin the read to the first frame sent after
    # it, so read only once oseq has stopped climbing. Traffic has stopped by
    # here, so a short run of equal reads means the pass has caught up.
    previous, stable = None, 0
    for _ in range(100):
        current = (await getsa(r.target, r, out))["oseq"]
        stable = stable + 1 if current == previous else 0
        if stable >= 3:
            break
        previous = current
        await asyncio.sleep(0.2)
    async with esp_capture(r, f"ipsec-readd-{label}-old") as old_wire:
        read_sa = await getsa(r.target, r, out)
        read_ae = await sa_replay_state(r.target, r.session, dst=WAN_IP, spi=out_spi)
        # SEC numbering on after the read, past the number read.
        await send_out(r, count=OUT_AFTER_READ)
    sent = esp_sequences(old_wire.path, out_spi)
    # What the read gave is the number SEC had last sent, nothing added:
    # the first frame after it carries the one after it. Nothing else is
    # proved below unless the old SA went on past it.
    precondition = {"case": label, "read": read_sa["oseq"], "getae": read_ae["oseq"],
                    "frames_after_read": len(sent), "first_after_read": min(sent, default=None)}
    assert len(sent) == OUT_AFTER_READ, (
        "precondition: every datagram sent after the read must leave by the old SA", precondition)
    assert min(sent) == read_sa["oseq"] + 1 and read_ae["oseq"] == read_sa["oseq"], (
        "the published number must be the last SEC sent", precondition)
    await command(r.target, r.session, "ip", "xfrm", "state", "delete", *out)
    reply = await sa_add(r.target, r.session, src=r.ipsec.outer, dst=WAN_IP, spi=out_spi,
                         reqid=int(REQIDS["out"]), ifindex=ifindex, **keys,
                         replay=(read_sa["oseq"], 0, 0), timeout_ms=READD_TIMEOUT_MS)
    assert reply.ok, reply.raw
    drops, received = replay_drops(), await getsa(r.ipsec.wan, r, out)
    async with esp_capture(r, f"ipsec-readd-{label}-new") as new_wire:
        await send_out(r, count=200)
    numbered = esp_sequences(new_wire.path, out_spi)
    accepted = await getsa(r.ipsec.wan, r, out)
    record = {"case": label, "read": {"getsa": read_sa, "getae": read_ae},
              "old": {"frames": len(sent), "last": max(sent, default=0)},
              "new": {"frames": len(numbered), "first": min(numbered, default=0)},
              "peer": {"before": received, "after": accepted}, "peer_replay_drops": replay_drops() - drops}
    r.record(f"ipsec-readd-out-{label}", record)
    # The re-add carried the read number, which the old SA had passed by
    # OUT_AFTER_READ: only where SEC left it, with its margin, puts the new
    # SA beyond -- for the idle SA, nothing else would move it at all.
    assert numbered and min(numbered) > max(sent), record
    assert record["peer_replay_drops"] == 0, record
    assert accepted["packets"] - received["packets"] == len(numbered), record


# A documentation-range pair of its own, so no other IPsec test's endpoints
# are disturbed. Nothing is sent: the peer does not exist.
LIMIT_LOCAL, LIMIT_PEER = "198.18.105.1", "198.18.105.2"


# Each width an inbound SA asks for, the mode it runs in, and the refusal it
# gets, or None where it installs. SEC keeps 32, 64 and -- in the tunnel-mode
# protocol only -- 128 entries, and nothing between them.
WIDTHS = [
    (32, "tunnel", None),
    (64, "tunnel", None),
    (128, "tunnel", None),
    (33, "tunnel", "cdx: SEC keeps 32/64/128-packet replay windows"),
    (100, "tunnel", "cdx: SEC keeps 32/64/128-packet replay windows"),
    (200, "tunnel", "cdx: SEC keeps 32/64/128-packet replay windows"),
    (32, "transport", None),
    (128, "transport", "cdx: SEC keeps a 128-packet replay window only in tunnel mode"),
]


# Again a pair, SPIs and a reqid of its own, and again nothing is sent.
GMAC_LOCAL, GMAC_PEER = "198.18.108.1", "198.18.108.2"
GMAC_REQID = "49307"
GMAC_STATES = {
    "out": ["src", GMAC_LOCAL, "dst", GMAC_PEER, "proto", "esp", "spi", hex(0x474D0001)],
    "in": ["src", GMAC_PEER, "dst", GMAC_LOCAL, "proto", "esp", "spi", hex(0x474D0002)],
}


# Once more a pair, SPIs and a reqid of its own, and nothing is sent.
TRUNC_LOCAL, TRUNC_PEER = "198.18.109.1", "198.18.109.2"
TRUNC_REQID = "49308"
TRUNC_STATES = {
    "out": ["src", TRUNC_LOCAL, "dst", TRUNC_PEER, "proto", "esp", "spi", hex(0x54520001)],
    "in": ["src", TRUNC_PEER, "dst", TRUNC_LOCAL, "proto", "esp", "spi", hex(0x54520002)],
}
# HMAC-SHA-256 at 96 bits, which strongSwan's sha256_96 and older Linux peers
# use, and the same through `auth`, which takes xfrm's 96-bit default.
SHA256_96 = CBC[:-1] + ("96",)
SHA256_DEFAULT = CBC[:3] + ("auth",) + CBC[4:-1]

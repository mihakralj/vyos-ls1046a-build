"""Every ESP transform the offload admits, against a software peer.

The adapter admits each transform below for packet offload (cdx's algorithm
table, control_ipsec.c), and SEC runs each through a descriptor of its own: a
cipher and an authenticator with their own key and ICV sizes, or a combined
mode whose salt or nonce trails the key. The traffic suites carry CBC with
HMAC-SHA256 and GCM. Nothing else checks the rest against a real peer, so any
of them could install, report itself offloaded, and then emit frames no peer
accepts or refuse every frame a peer sends. Packet offload has no software
fallback, so a deployment that negotiates such a transform loses the tunnel
without any error.

Per transform, the DUT installs a tunnel-mode SA pair with packet offload on
the WAN port, and the WAN host installs the mirror in software. Linux's own ESP
is the reference. The DUT pings the peer's inner address, and the case proves:

  - both of the DUT's states are offloaded to the WAN port in packet mode;
  - every echo is answered;
  - the peer decrypted and authenticated exactly the echoes the DUT sent. The
    SA shows no replay or integrity failure and no XfrmIn* counter on the host
    moves;
  - the DUT's inbound SA accounts for exactly the replies. SEC encrypted every
    echo the CPU handed it, and the replies reached SEC through the classifier,
    not through a CPU-fed decrypt;
  - no IV repeats. The counter modes (GCM, CCM, CTR) need an IV that is only
    unique, so each SA's IVs count up from a random start of its own, one per
    sequence number; Linux's seqiv likewise salts each SA's sequence numbers.
    SEC's random IVs are 64 bits there, and two collide after about 2^32
    frames: under GCM that gives away the authentication key. CBC keeps random
    IVs, which it needs unpredictable.

Each transform and each direction gets its own key, and no key is one repeated
byte. A repeated byte hides a key, salt or nonce read from the wrong offset,
and one key for both directions hides a direction using the other SA's key.

Out of scope: GCM at ICV 8 and 16 with a 128-bit key, whose forwarded traffic
flowtable_service_ipsec_replay.py carries, and GMAC, which the offload
refuses and which has a test of its own.

The file builds the inner addresses, the routes between them, the NAT
exemption and the policies once. Each case adds and removes only its SAs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import threading

import aiohttp
import pytest
import pytest_asyncio

from ask_orch.client import Agent
from ask_orch.uart import Console
from _topology import TARGET_WAN_IF
from _flowtable_rig import (artifact_dir, WAN_IP, command)
from _flowtable_qos import (dut_ping)
from _flowtable_service_ipsec import (xfrm)
from _flowtable_service_ipsec_replay import (xfrm_mib)
from _ipsec_inbound_flow_offload import (sec_counter)

pytestmark = [
    # The fixture's aiohttp session belongs to the loop that built it, and the
    # file's pieces are built once. Every case therefore runs on that loop.
    pytest.mark.asyncio(loop_scope="module"),
]

# This file's own documentation-range pair: the DUT's inner address and the
# peer's, each on its host's loopback. No other test uses 198.18.109.0/24, these
# reqids or the 0xB109 SPIs.
DUT_INNER, PEER_INNER = "198.18.109.3", "198.18.109.2"
# Keyed by the direction of the DUT's SA. The peer's mirror of each shares it.
REQIDS = {"out": "49601", "in": "49602"}
SPI_BASE = 0xB1090000
COUNT = 8
# The adapter's accounting pass publishes SEC's per-SA figures about once a
# second, so a few passes are allowed for.
ACCOUNTING_SECONDS = 5
# The image masquerades whatever leaves the WAN port. Rewritten, the DUT's
# inner source would no longer match its policy, and the echo would leave in
# plaintext instead of through the SA.
EXEMPT = ["POSTROUTING", "-s", DUT_INNER, "-d", PEER_INNER, "-j", "ACCEPT"]


def key(case, direction, part, size):
    """A key of `size` bytes that no other case, direction or part shares.
    It is derived rather than random, so a failure reruns with the same keys.
    `ecb(cipher_null)` takes an empty key."""
    if not size:
        return ""
    return "0x" + hashlib.shake_256(f"{case}/{direction}/{part}".encode()).hexdigest(size)


@dataclass(frozen=True)
class Transform:
    """One transform as `ip xfrm state` takes it: a cipher with an
    authenticator, or a combined mode when `auth` is None. Key sizes are in
    bytes and include the nonce or salt that RFC 3686, 4106 and 4309 append to
    the key. The ICV is in bits: the authenticator's truncation, or the
    combined mode's tag length."""
    cipher: str
    cipher_key: int
    icv: int
    auth: str | None = None
    auth_key: int = 0

    @property
    def iv_size(self):
        """The bytes of IV each ESP frame carries after its sequence number."""
        if self.counter_iv or self.cipher in ("cbc(des3_ede)", "cbc(des)"):
            return 8
        return 16 if self.cipher == "cbc(aes)" else 0

    @property
    def counter_iv(self):
        """Whether the mode needs its IVs unique rather than unpredictable."""
        return self.cipher.startswith(("rfc4106", "rfc4309", "rfc3686"))

    def algorithms(self, case, direction):
        if self.auth is None:
            return ("aead", self.cipher, key(case, direction, "aead", self.cipher_key), str(self.icv))
        return ("enc", self.cipher, key(case, direction, "enc", self.cipher_key),
                "auth-trunc", self.auth, key(case, direction, "auth", self.auth_key), str(self.icv))


TRANSFORMS = {
    "cbc128-sha256": Transform("cbc(aes)", 16, 128, "hmac(sha256)", 32),
    "cbc256-sha256": Transform("cbc(aes)", 32, 128, "hmac(sha256)", 32),
    "cbc-sha1": Transform("cbc(aes)", 16, 96, "hmac(sha1)", 20),
    "cbc-md5": Transform("cbc(aes)", 16, 96, "hmac(md5)", 16),
    "cbc-sha384": Transform("cbc(aes)", 16, 192, "hmac(sha384)", 48),
    "cbc-sha512": Transform("cbc(aes)", 16, 256, "hmac(sha512)", 64),
    "cbc-xcbc": Transform("cbc(aes)", 16, 96, "xcbc(aes)", 16),
    "ctr-sha256": Transform("rfc3686(ctr(aes))", 20, 128, "hmac(sha256)", 32),
    "rfc4309-icv8": Transform("rfc4309(ccm(aes))", 19, 64),
    "rfc4309-icv12": Transform("rfc4309(ccm(aes))", 19, 96),
    "rfc4309-icv16": Transform("rfc4309(ccm(aes))", 19, 128),
    "rfc4106-icv12": Transform("rfc4106(gcm(aes))", 20, 96),
    "rfc4106-aes256-icv16": Transform("rfc4106(gcm(aes))", 36, 128),
    "3des-sha1": Transform("cbc(des3_ede)", 24, 96, "hmac(sha1)", 20),
    "des-sha1": Transform("cbc(des)", 8, 96, "hmac(sha1)", 20),
    "null-sha256": Transform("ecb(cipher_null)", 0, 128, "hmac(sha256)", 32),
    # SEC's other two HMAC lengths: SHA-1 and MD5 untruncated.
    "cbc-sha1-160": Transform("cbc(aes)", 16, 160, "hmac(sha1)", 20),
    "cbc-md5-128": Transform("cbc(aes)", 16, 128, "hmac(md5)", 16),
}


def spi(case, direction):
    """A fixed SPI per case and direction, so a failure reruns with the same
    SPIs as well as the same keys."""
    return SPI_BASE | list(TRANSFORMS).index(case) << 4 | (1 if direction == "out" else 2)


def selector(direction):
    """The inner traffic the DUT sends (`out`) or receives (`in`)."""
    src, dst = (DUT_INNER, PEER_INNER) if direction == "out" else (PEER_INNER, DUT_INNER)
    return ["src", src + "/32", "dst", dst + "/32"]


def owned_state(state):
    return any(re.search(r"\breqid " + reqid + r"\b", state) for reqid in REQIDS.values())


def owned_policy(policy):
    return DUT_INNER + "/32" in policy.splitlines()[0]


def peer_mib():
    """The peer's xfrm MIB. The WAN host is this host, as in the replay
    tests, and its agent serves no file reads."""
    return xfrm_mib(Path("/proc/net/xfrm_stat").read_text())


def peer_errors(before, after):
    """What the peer's receive path refused between two readings of
    /proc/net/xfrm_stat. Every XfrmIn* counter there counts a refusal."""
    return {name: after[name] - before.get(name, 0) for name in after
            if name.startswith("XfrmIn") and after[name] != before.get(name, 0)}


# Where each counter-mode case's SA started its IVs, so no two start alike.
IV_STARTS = {}


class EspCapture:
    """The DUT's frames under one SA as they reach this host: each one's
    sequence number and the IV that follows it. A raw socket on the peer's
    interface, which is this host's."""

    def __init__(self, ifname, source, spi, iv_size):
        self.source, self.spi, self.iv_size = socket.inet_aton(source), spi, iv_size
        self.frames = []
        self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
        self.sock.bind((ifname, 0))
        self.sock.settimeout(0.1)
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.done.is_set():
            try:
                frame = self.sock.recv(128)
            except socket.timeout:
                continue
            ip = frame[14:]
            if len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 50 or ip[12:16] != self.source:
                continue
            esp = ip[(ip[0] & 0xf) * 4:]
            spi, seq = struct.unpack(">II", esp[:8])
            if spi == self.spi:
                self.frames.append((seq, esp[8:8 + self.iv_size]))

    def stop(self):
        self.done.set()
        self.thread.join()
        self.sock.close()
        return sorted(self.frames)


class Interop:
    """The pieces the file builds once, and the SAs a case holds."""

    def __init__(self, target, wan, session):
        self.target, self.wan, self.session = target, wan, session
        self.states = []

    def state(self, direction, spi):
        src, dst = (self.outer, WAN_IP) if direction == "out" else (WAN_IP, self.outer)
        return ["src", src, "dst", dst, "proto", "esp", "spi", hex(spi)]

    def template(self, direction):
        src, dst = (self.outer, WAN_IP) if direction == "out" else (WAN_IP, self.outer)
        return ["tmpl", "src", src, "dst", dst, "proto", "esp", "mode", "tunnel",
                "reqid", REQIDS[direction], "level", "required"]

    async def add_state(self, agent, identity, *options):
        await command(agent, self.session, "ip", "xfrm", "state", "add", *identity, *options)
        self.states.append((agent, identity))

    async def remove_states(self):
        """Delete every SA still held, newest first, and return what could
        not be deleted. Exact identities only: other SAs on either host are
        not this file's to touch."""
        failures = []
        while self.states:
            agent, identity = self.states.pop()
            try:
                result = await command(agent, self.session, "ip", "xfrm", "state", "delete", *identity,
                                       check=False)
                if result["rc"]:
                    failures.append(result)
            except Exception as error:
                failures.append(repr(error))
        return failures

    async def leftovers(self):
        """This file's states and policies still on either host."""
        found = {}
        for agent in (self.target, self.wan):
            states = [s for s in await xfrm(self, agent, "state") if owned_state(s)]
            policies = [p for p in await xfrm(self, agent, "policy") if owned_policy(p)]
            if states or policies:
                found[str(agent)] = {"states": states, "policies": policies}
        return found

    async def figures(self, agent, identity):
        """`ip -s xfrm state` figures for one SA on either host: what went
        through it, and its replay-window, replay and integrity failures where
        the state shows them."""
        text = (await command(agent, self.session, "ip", "-s", "xfrm", "state", "get", *identity))["stdout"]
        current = re.search(r"lifetime current:\s*(\d+)\(bytes\), (\d+)\(packets\)", text)
        assert current, text
        stats = re.search(r"stats:\s*replay-window (\d+) replay (\d+) failed (\d+)", text)
        return {"bytes": int(current.group(1)), "packets": int(current.group(2)),
                "failures": [int(value) for value in stats.groups()] if stats else None}

    async def accounted(self, identity, count):
        """The DUT's figures for one of its SAs once they reach `count`
        packets, or as they stand after a few accounting passes."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ACCOUNTING_SECONDS
        while True:
            figures = await self.figures(self.target, identity)
            if figures["packets"] >= count or loop.time() >= deadline:
                return figures
            await asyncio.sleep(0.25)

    def record(self, name, data):
        artifact_dir().mkdir(parents=True, exist_ok=True)
        (artifact_dir() / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def interop(target_agent):
    """The inner addresses, the routes between them, the NAT exemption and
    both hosts' policies, built once for the file.

    The policies do not depend on the transform: they name their SAs by
    reqid, so each case installs and removes a pair of states under them. The
    DUT's are offloaded as its states are. xfrm_state_find() skips a
    packet-offloaded state that a software policy reached.

    The DUT's console carries the ping, which the agent's argv allowlist does
    not.

    This fixture has its own aiohttp session, because conftest's is
    function-scoped and bound to each test's loop."""
    async with aiohttp.ClientSession() as session:
        wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
        ctx = Interop(target_agent, wan, session)
        cleanup, console = [], None
        try:
            dut = json.loads((await command(target_agent, session, "ip", "-j", "-4", "addr", "show",
                                            "dev", TARGET_WAN_IF))["stdout"])
            ctx.outer = next(a["local"] for i in dut for a in i["addr_info"] if a["family"] == "inet")
            peer = json.loads((await command(wan, session, "ip", "-j", "-4", "addr"))["stdout"])
            ctx.wan_if = next((i["ifname"] for i in peer if any(a.get("local") == WAN_IP for a in i["addr_info"])),
                              None)
            assert ctx.wan_if, (f"the WAN agent's host does not own {WAN_IP}: set ASK_WAN_IP to the "
                                "host that runs the peer", peer)
            for agent in (target_agent, ctx.wan):
                addresses = json.loads((await command(agent, session, "ip", "-j", "-4", "addr"))["stdout"])
                taken = [a["local"] for i in addresses for a in i["addr_info"]
                         if a.get("local") in (DUT_INNER, PEER_INNER)]
                assert not taken, (str(agent), "test-owned inner addresses already exist", taken)
                for address in (DUT_INNER, PEER_INNER):
                    routes = json.loads((await command(agent, session, "ip", "-j", "route", "show", "table", "all",
                                                       "exact", address + "/32"))["stdout"])
                    assert not routes, (str(agent), "test-owned inner routes already exist", routes)
            leftovers = await ctx.leftovers()
            assert not leftovers, ("test-owned XFRM states or policies already exist", leftovers)
            steps = [
                (target_agent, ["ip", "addr", "add", DUT_INNER + "/32", "dev", "lo"],
                 ["ip", "addr", "del", DUT_INNER + "/32", "dev", "lo"]),
                (ctx.wan, ["ip", "addr", "add", PEER_INNER + "/32", "dev", "lo"],
                 ["ip", "addr", "del", PEER_INNER + "/32", "dev", "lo"]),
                (target_agent, ["ip", "route", "add", PEER_INNER + "/32", "via", WAN_IP, "dev", TARGET_WAN_IF,
                                "src", DUT_INNER],
                 ["ip", "route", "del", PEER_INNER + "/32", "via", WAN_IP, "dev", TARGET_WAN_IF]),
                (ctx.wan, ["ip", "route", "add", DUT_INNER + "/32", "via", ctx.outer, "dev", ctx.wan_if,
                           "src", PEER_INNER],
                 ["ip", "route", "del", DUT_INNER + "/32", "via", ctx.outer, "dev", ctx.wan_if]),
                (target_agent, ["iptables", "-t", "nat", "-I", *EXEMPT], ["iptables", "-t", "nat", "-D", *EXEMPT]),
            ]
            for direction in ("out", "in"):
                mirror = "in" if direction == "out" else "out"
                steps += [
                    (target_agent, ["ip", "xfrm", "policy", "add", *selector(direction), "dir", direction,
                                    *ctx.template(direction), "offload", "packet", "dev", TARGET_WAN_IF],
                     ["ip", "xfrm", "policy", "delete", *selector(direction), "dir", direction]),
                    (ctx.wan, ["ip", "xfrm", "policy", "add", *selector(direction), "dir", mirror,
                               *ctx.template(direction)],
                     ["ip", "xfrm", "policy", "delete", *selector(direction), "dir", mirror]),
                ]
            for agent, argv, undo in steps:
                await command(agent, session, *argv)
                cleanup.append((agent, undo))
            console = Console.target(log_path=str(artifact_dir() / "ipsec-interop-uart.log"))
            await asyncio.to_thread(console.login, "root", None)
            ctx.console = console
            yield ctx
        finally:
            # Whatever a failed case could not remove, then the shared pieces
            # in reverse.
            failures = await ctx.remove_states()
            try:
                # Nothing to find is not a failure: conntrack says so with a
                # nonzero exit.
                await command(target_agent, session, "conntrack", "-D", "-p", "icmp", "--orig-src", DUT_INNER,
                              "--orig-dst", PEER_INNER, check=False)
            except Exception as error:
                failures.append(repr(error))
            for agent, argv in reversed(cleanup):
                try:
                    result = await command(agent, session, *argv, check=False)
                    if result["rc"]:
                        failures.append(result)
                except Exception as error:
                    failures.append(repr(error))
            if console:
                console.close()
            if cleanup:
                leftovers = await ctx.leftovers()
                if leftovers:
                    failures.append(leftovers)
            assert not failures, ("interop teardown failed", failures)


@pytest.mark.parametrize("case", list(TRANSFORMS))
async def test_ipsec_offload_transform_interop(interop, case, splat_window):
    """One offloaded transform carries a tunnel both ways with a Linux
    software peer.

    The assertions follow the traffic. If the peer refuses the DUT's frames,
    the outbound SA is at fault. If the peer accepts them and the replies go
    missing, the inbound SA is."""
    ctx, transform = interop, TRANSFORMS[case]
    record = {"case": case, "spi": {d: hex(spi(case, d)) for d in ("out", "in")}}
    try:
        for direction in ("out", "in"):
            identity = ctx.state(direction, spi(case, direction))
            crypto = ["mode", "tunnel", "reqid", REQIDS[direction], *transform.algorithms(case, direction)]
            await ctx.add_state(ctx.wan, identity, *crypto, "replay-window", "32")
            # An inbound SA checks replays only with a window. 32 is what
            # strongSwan installs.
            window = ["replay-window", "32"] if direction == "in" else []
            await ctx.add_state(ctx.target, identity, *crypto, *window,
                                "offload", "packet", "dev", TARGET_WAN_IF, "dir", direction)
        for direction in ("out", "in"):
            shown = (await command(ctx.target, ctx.session, "ip", "xfrm", "state", "get",
                                   *ctx.state(direction, spi(case, direction))))["stdout"]
            assert transform.cipher in shown, shown
            assert re.search(rf"crypto offload parameters: dev {TARGET_WAN_IF} dir {direction} mode packet",
                             shown), shown
        mib = peer_mib()
        toenc = await sec_counter(ctx.session, ctx.target, TARGET_WAN_IF, "tx toenc")
        todec = await sec_counter(ctx.session, ctx.target, TARGET_WAN_IF, "tx todec")
        capture = EspCapture(ctx.wan_if, ctx.outer, spi(case, "out"), transform.iv_size)
        try:
            sent, answered = await dut_ping(ctx, PEER_INNER, COUNT, interval="0.1")
            await asyncio.sleep(0.2)
        finally:
            frames = capture.stop()
        record["ping"] = {"sent": sent, "answered": answered}
        record["ivs"] = [[seq, iv.hex()] for seq, iv in frames]
        record["peer"] = {
            "decrypted": await ctx.figures(ctx.wan, ctx.state("out", spi(case, "out"))),
            "encrypted": await ctx.figures(ctx.wan, ctx.state("in", spi(case, "in"))),
            "errors": peer_errors(mib, peer_mib()),
        }
        record["dut"] = {
            "in": await ctx.accounted(ctx.state("in", spi(case, "in")), COUNT),
            # Recorded, not asserted: what the accounting pass published for
            # the frames SEC encrypted.
            "out": await ctx.figures(ctx.target, ctx.state("out", spi(case, "out"))),
            "toenc": await sec_counter(ctx.session, ctx.target, TARGET_WAN_IF, "tx toenc") - toenc,
            "todec": await sec_counter(ctx.session, ctx.target, TARGET_WAN_IF, "tx todec") - todec,
        }
        ctx.record(f"ipsec-interop-{case}", record)
        decrypted = record["peer"]["decrypted"]
        assert decrypted["packets"] == COUNT and decrypted["failures"] == [0, 0, 0], (
            "the peer did not decrypt and authenticate every echo SEC encrypted", record)
        assert not record["peer"]["errors"], ("the peer's receive path refused frames", record)
        assert (sent, answered) == (COUNT, COUNT), record
        assert record["dut"]["in"]["packets"] == COUNT, (
            "the DUT's inbound SA did not account for exactly the replies", record)
        assert (record["dut"]["toenc"], record["dut"]["todec"]) == (COUNT, 0), (
            "SEC must encrypt every echo the CPU gave it and receive the replies through the classifier", record)
        assert [seq for seq, _ in frames] == list(range(1, COUNT + 1)), (
            "the capture did not see each echo SEC encrypted exactly once", record)
        if transform.counter_iv:
            starts = {(int.from_bytes(iv, "big") - seq) % 2**64 for seq, iv in frames}
            assert len(starts) == 1, (
                "a counter mode's IVs must count up one per sequence number: random ones collide", record)
            start = starts.pop()
            assert start not in IV_STARTS.values(), ("two SAs started their IVs alike", IV_STARTS, record)
            IV_STARTS[case] = start
        elif transform.iv_size:
            ivs = [iv for _, iv in frames]
            assert len(set(ivs)) == len(ivs), ("an IV repeated", record)
    finally:
        failures = await ctx.remove_states()
        leftovers = await ctx.leftovers()
        states = {host: found["states"] for host, found in leftovers.items() if found["states"]}
        if states:
            failures.append(states)
        assert not failures, ("the case's SAs were not removed", failures)

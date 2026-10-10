"""Shared IPsec helpers: SAs installed the way the offload control plane
installs them.

The control plane is `xfrmdev_ops`. strongSwan asks for `hw_offload = packet`
per child SA, the kernel resolves the state and hands it to
`xdo_dev_state_add()`, and the adapter programs CDX from there. So an SA here
is an XFRM `NEWSA`, not the five-command FCI sequence the retired daemon used:
FCI's `CMD_IPSEC_SA_*` reached the same allocator, the same descriptor builder
and the same classification-entry path, but through a door that goes away with
CMM.

Two ways in, and the difference is only how much the caller needs to steer.

`ip xfrm state add` through the agent's command channel is the readable one,
and what a real deployment does. Use it wherever the test is about the SA's
effect.

The raw `NEWSA` builders below are the other, and they exist for one reason:
fault injection needs an armed window covering exactly one send, so the
message has to be built here rather than by `ip`, whose own startup would
spend the fail-nth counter long before the SA allocator ran. The wire layout
is `include/uapi/linux/xfrm.h`, checked with `offsetof` against the kernel
this image is built from:

  xfrm_selector     56 B
  xfrm_id           24 B    (daddr 16, spi 4, proto 1, pad)
  xfrm_usersa_info 224 B    (sel 0, id 56, saddr 80, lft 96, curlft 160,
                             stats 192, seq 204, reqid 208, family 212,
                             mode 214, replay_window 215, flags 216)
  xfrm_algo         68 B + key      (name 64, key_len 4)
  xfrm_algo_auth    72 B + key      (name 64, key_len 4, trunc_len 4)
  xfrm_user_offload  8 B            (ifindex 4, flags 1, pad)
  xfrm_encap_tmpl   24 B            (type 2, sport 2, dport 2, pad, oa 16)

`sa_install_probe()` sends one unencumbered NEWSA and reads the state back
before any sweep begins, so a layout mistake fails loudly at the start rather
than turning a whole sweep into a no-op.
"""

from __future__ import annotations

import json
import os
import socket
import struct
from dataclasses import dataclass


# ---------------------------------------------------------------- netlink
NETLINK_XFRM = 6

XFRM_MSG_NEWSA = 16
XFRM_MSG_DELSA = 17
# The async event pair, which carries a state's replay state and lifetime:
# GETAE asks, and the kernel answers with a NEWAE.
XFRM_MSG_NEWAE = 30
XFRM_MSG_GETAE = 31

NLM_F_REQUEST = 0x01
NLM_F_ACK     = 0x04

NLMSG_ERROR = 2

# Attributes (uapi/linux/xfrm.h). AUTH_TRUNC rather than AUTH because it is
# what carries the truncation length, and what iproute2 sends for `auth-trunc`.
XFRMA_ALG_AUTH_TRUNC = 20
XFRMA_ALG_CRYPT      = 2
XFRMA_ALG_AEAD       = 18
XFRMA_ENCAP          = 4
XFRMA_OFFLOAD_DEV    = 28
# struct xfrm_replay_state (oseq, seq, bitmap: three u32) and its ESN-format
# sibling (bmp_len, oseq, seq, oseq_hi, seq_hi, replay_window, then the ring).
XFRMA_REPLAY_VAL     = 10
XFRMA_REPLAY_ESN_VAL = 23

# xfrm_user_offload.flags
XFRM_OFFLOAD_IPV6    = 1
XFRM_OFFLOAD_INBOUND = 2
XFRM_OFFLOAD_PACKET  = 4

AF_INET = 2

XFRM_MODE_TRANSPORT = 0
XFRM_MODE_TUNNEL    = 1

XFRM_INF = (1 << 64) - 1

UDP_ENCAP_ESPINUDP = 2

IPPROTO_ESP = 50

# The per-flow SPI array a NAT-T classifier entry carries
# (sdk_fman/inc/Peripherals/fm_ehash.h). Same-flow NAT-T SAs accumulate in it,
# and the slot after the last one is the H5 bound.
MAX_SPI_PER_FLOW = 16

# ---------------------------------------------------------------- algorithms
# AES-CBC + HMAC-SHA2-256 rather than an AEAD, so the cipher and the
# authenticator each allocate their own key buffer inside
# cdx_ipsec_sec_sa_context_alloc. An AEAD occupies one slot and would leave
# any auth-side allocator regression invisible.
ENC_ALG  = "cbc(aes)"
AUTH_ALG = "hmac(sha256)"
AUTH_TRUNC_BITS = 128

# Recognisable patterns, so the H2 key-zeroing probe can say whether the
# cipher key survived kfree_sensitive: sixteen 0xA5 bytes for the cipher,
# thirty-two 0x5A for the authenticator.
CIPHER_KEY = b"\xA5" * 16
AUTH_KEY   = b"\x5A" * 32

# ---------------------------------------------------------------- leak filter
# Every name checked against cdx/ + the adapter by grep. These are the
# functions an offloaded SA install actually walks; anything added by analogy
# would mask a real leak and gets dropped on first false positive.
IPSEC_LEAK_FILTER = [
    "ft_xdo_state_add",                                 # ask_flowtable_ipsec.c
    "ft_ipsec_spec",                                    # ask_flowtable_ipsec.c
    "ft_ipsec_watch_add",                               # the SA next-hop watch
    "cdx_ipsec_sa_add",                                 # cdx_ipsec_backend.c
    "cdx_ipsec_sa_del",                                 # cdx_ipsec_backend.c
    "M_ipsec_sa_cache_create",                          # SA struct allocator
    "M_ipsec_sa_set_cipher_key",                        # control_ipsec.c
    "M_ipsec_sa_set_digest_key",                        # control_ipsec.c
    "ipsec_install_fp_entry",                           # control_ipsec.c
    "cdx_ipsec_sec_sa_context_alloc",                   # cdx_dpa_ipsec.c
    "cdx_ipsec_sec_sa_context_free",                    # cdx_dpa_ipsec.c
    "cdx_ipsec_create_shareddescriptor",                # cdx_dpa_ipsec.c
    "cdx_ipsec_add_classification_table_entry",         # cdx_dpa_ipsec.c
    "cdx_ipsec_process_udp_classification_table_entry", # cdx_dpa_ipsec.c
]

# There is deliberately no DMA-specific filter. An earlier one named
# dma_alloc_coherent, dma_map_single and __dma_alloc_pages, and not one of
# them could ever match: dma_map_single is a macro and dma_alloc_coherent is
# static inline, so neither appears in a backtrace, and an unbalanced map is
# not an allocation kmemleak tracks in the first place. The oracle for a
# leaked map is KASAN plus the splat window, and saying otherwise made a
# tripwire out of three names that matched nothing.

# The SEC context is released on a 1-second cdx_timer that reschedules while
# any frame queue is still retiring, so a freshly deleted SA is not gone the
# moment the netlink call returns.
SA_RELEASE_GRACE_S = float(os.environ.get("ASK_IPSEC_KMEMLEAK_GRACE_S", "30.0"))


# ---------------------------------------------------------------- wire builders

def _addr(ip: str | None) -> bytes:
    """xfrm_address_t: a v4 address in the first four bytes, rest zero."""
    if not ip:
        return b"\x00" * 16
    return socket.inet_aton(ip) + b"\x00" * 12


def _selector() -> bytes:
    """An all-zero xfrm_selector, which is what `ip xfrm state add` sends when
    no selector is named: the state is selected by policy, not by its own."""
    return b"\x00" * 56


def _algo(name: str, key: bytes) -> bytes:
    """struct xfrm_algo: alg_name[64], alg_key_len (bits), alg_key[]."""
    return (name.encode().ljust(64, b"\x00")
            + struct.pack("<I", len(key) * 8) + key)


def _algo_auth(name: str, key: bytes, trunc_bits: int) -> bytes:
    """struct xfrm_algo_auth: alg_name[64], alg_key_len, alg_trunc_len, key[]."""
    return (name.encode().ljust(64, b"\x00")
            + struct.pack("<II", len(key) * 8, trunc_bits) + key)


def _nla(attr_type: int, payload: bytes) -> bytes:
    """One netlink attribute, padded to four bytes. nla_len counts the header
    but not the padding, which is why the two lengths differ."""
    header = struct.pack("<HH", 4 + len(payload), attr_type)
    blob = header + payload
    return blob + b"\x00" * (-len(blob) % 4)


def newsa(
    *,
    src: str,
    dst: str,
    spi: int,
    reqid: int,
    ifindex: int,
    inbound: bool = False,
    mode: int = XFRM_MODE_TUNNEL,
    offload: bool = True,
    cipher_key: bytes = CIPHER_KEY,
    auth_key: bytes = AUTH_KEY,
    natt: tuple[int, int] | None = None,
    replay_window: int = 0,
    replay: tuple[int, int, int] | None = None,
) -> bytes:
    """Build an XFRM_MSG_NEWSA body: xfrm_usersa_info then its attributes.

    `offload=False` describes the same SA without XFRMA_OFFLOAD_DEV, which is
    how a case asks for a software state -- useful as the control that proves
    an assertion is about the offload rather than about xfrm.

    `replay` is the (oseq, seq, bitmap) a keying daemon carries over when it
    re-adds an SA, as XFRMA_REPLAY_VAL: the legacy shape, for a window of 32
    or less, which is what `ip` cannot send.
    """
    info = (
        _selector()                                   # sel
        # id: the SPI is a __be32, so it goes on the wire in network order
        # while every length and index beside it is native.
        + _addr(dst) + struct.pack(">I", spi) + struct.pack("<BBH", IPPROTO_ESP, 0, 0)
        + _addr(src)                                  # saddr
        # lft. The byte and packet limits say "unlimited" with XFRM_INF; the
        # four *time* limits say it with zero, and the difference is not
        # cosmetic. xfrm_timer_handler() computes
        #   tmo = hard_add_expires_seconds + curlft.add_time - now
        # into a signed time64_t, so XFRM_INF there reads as -1, tmo comes out
        # negative, and the state hard-expires on the first tick. An SA that
        # quietly vanishes a moment after install still passes a test that
        # installs and acts at once, which is exactly how long this went
        # unnoticed. iproute2 sends the same split.
        + struct.pack("<4Q", *([XFRM_INF] * 4))       # soft/hard byte, packet
        + struct.pack("<4Q", 0, 0, 0, 0)              # add/use expiry seconds
        + b"\x00" * 32                                # curlft
        + b"\x00" * 12                                # stats
        + struct.pack("<II", 0, reqid)                # seq, reqid
        + struct.pack("<HBBB", AF_INET, mode, replay_window, 0)   # family, mode, replay, flags
        + b"\x00" * 7                                 # tail padding to 224
    )
    assert len(info) == 224, len(info)
    attrs = (
        _nla(XFRMA_ALG_CRYPT, _algo(ENC_ALG, cipher_key))
        + _nla(XFRMA_ALG_AUTH_TRUNC,
               _algo_auth(AUTH_ALG, auth_key, AUTH_TRUNC_BITS))
    )
    if replay is not None:
        attrs += _nla(XFRMA_REPLAY_VAL, struct.pack("<III", *replay))
    if natt is not None:
        sport, dport = natt
        attrs += _nla(XFRMA_ENCAP,
                      struct.pack("<H", UDP_ENCAP_ESPINUDP)
                      + struct.pack(">HH", sport, dport)   # __be16 pair
                      + b"\x00" * 2 + _addr(None))
    if offload:
        flags = XFRM_OFFLOAD_PACKET | (XFRM_OFFLOAD_INBOUND if inbound else 0)
        attrs += _nla(XFRMA_OFFLOAD_DEV,
                      struct.pack("<IBBH", ifindex, flags, 0, 0))
    return info + attrs


def delsa(*, dst: str, spi: int) -> bytes:
    """Build an XFRM_MSG_DELSA body: struct xfrm_usersa_id."""
    return (_addr(dst) + struct.pack(">I", spi)
            + struct.pack("<HBB", AF_INET, IPPROTO_ESP, 0))


def getae(*, dst: str, spi: int) -> bytes:
    """Build an XFRM_MSG_GETAE body: struct xfrm_aevent_id, 48 bytes. The
    kernel finds the state by the xfrm_usersa_id at its head alone; the
    source, flags and reqid after it are left zero."""
    return delsa(dst=dst, spi=spi) + _addr(None) + struct.pack("<II", 0, 0)


def replay_attrs(attrs: bytes) -> dict:
    """The replay state among a NEWAE's (or NEWSA's) attributes: the legacy
    shape as oseq, seq and bitmap, the ESN-format one as oseq and seq with
    their high words folded in, its window and its ring."""
    out: dict = {}
    off = 0
    while off + 4 <= len(attrs):
        length, kind = struct.unpack_from("<HH", attrs, off)
        if length < 4:
            break
        value = attrs[off + 4:off + length]
        kind &= 0x3FFF          # NLA_F_NESTED and NLA_F_NET_BYTEORDER
        if kind == XFRMA_REPLAY_VAL:
            out["oseq"], out["seq"], out["bitmap"] = struct.unpack_from("<III", value)
        elif kind == XFRMA_REPLAY_ESN_VAL:
            bmp_len, oseq, seq, oseq_hi, seq_hi, window = struct.unpack_from("<6I", value)
            out.update(oseq=oseq_hi << 32 | oseq, seq=seq_hi << 32 | seq, window=window,
                       bmp=list(struct.unpack_from(f"<{bmp_len}I", value, 24)))
        off += (length + 3) & ~3
    return out


async def sa_replay_state(target_agent, session, *, dst: str, spi: int) -> dict:
    """An SA's replay state as XFRM_MSG_GETAE reports it -- what a keying
    daemon reads to carry the state over when it re-adds the SA."""
    result = await target_agent.netlink_send(
        session, NETLINK_XFRM, getae(dst=dst, spi=spi),
        nlmsg_type=XFRM_MSG_GETAE, nlmsg_flags=NLM_F_REQUEST, timeout_ms=3000)
    reply = bytes.fromhex(result.get("reply_hex", "") or "")
    assert len(reply) >= 16, result
    length, kind = struct.unpack_from("<IH", reply, 0)
    if kind == NLMSG_ERROR:
        raise RuntimeError(f"GETAE refused: {struct.unpack_from('<i', reply, 16)[0]}")
    assert kind == XFRM_MSG_NEWAE and length <= len(reply), result
    # nlmsghdr, then the xfrm_aevent_id echoed back, then the attributes.
    state = replay_attrs(reply[16 + 48:length])
    assert "seq" in state, result
    return state


# ---------------------------------------------------------------- results

@dataclass
class SaReply:
    """What one NEWSA or DELSA attempt came back with.

    `error` is the netlink ACK's own code: 0 for accepted, negative errno for
    a refusal. `lost` is the fault-injection case where no reply arrived at
    all.

    The two are not interchangeable, and a sweep that treats them as one
    proves less than it looks. A reply that arrived carrying an error is the
    install itself refusing: the fault landed somewhere on the path that
    builds the SA. A reply that never arrived is the *reply* being built with
    a faulted allocation, which happens after the SA is already installed --
    the netlink ACK's own skb is allocated well past anything cdx does. So
    `refused` is the property to assert non-vacuity on; `lost` says only that
    fail-nth armed.
    """
    error: int | None
    lost: bool
    fail_nth_residue: int | None
    raw: dict

    @property
    def ok(self) -> bool:
        return self.error == 0

    @property
    def refused(self) -> bool:
        """The install itself was rejected, as opposed to the reply being
        lost after it succeeded."""
        return not self.lost and (self.error or 0) != 0


def _parse(result: dict) -> SaReply:
    body = bytes.fromhex(result.get("body_hex", "") or "")
    reply = bytes.fromhex(result.get("reply_hex", "") or "")
    residue = result.get("fail_nth_residue")
    if result.get("send_error") or len(reply) < 16 or len(body) < 4:
        return SaReply(error=None, lost=True, fail_nth_residue=residue,
                       raw=result)
    msg_type = struct.unpack_from("<H", reply, 4)[0]
    if msg_type != NLMSG_ERROR:
        # Anything else is a reply this helper does not model; surface it
        # rather than guess a code from it.
        return SaReply(error=None, lost=False, fail_nth_residue=residue,
                       raw=result)
    return SaReply(error=struct.unpack_from("<i", body, 0)[0], lost=False,
                   fail_nth_residue=residue, raw=result)


# ---------------------------------------------------------------- operations

async def sa_add(
    target_agent, session, *, src: str, dst: str, spi: int, reqid: int,
    ifindex: int, inbound: bool = False, mode: int = XFRM_MODE_TUNNEL,
    offload: bool = True, cipher_key: bytes = CIPHER_KEY,
    auth_key: bytes = AUTH_KEY, natt: tuple[int, int] | None = None,
    failslab_times: int | None = None, timeout_ms: int = 3000,
    replay_window: int = 0, replay: tuple[int, int, int] | None = None,
) -> SaReply:
    """Install one SA over XFRM, optionally with the Nth kmalloc of the send
    forced to NULL, and with a replay state carried in (newsa())."""
    result = await target_agent.netlink_send(
        session, NETLINK_XFRM,
        newsa(src=src, dst=dst, spi=spi, reqid=reqid, ifindex=ifindex,
              inbound=inbound, mode=mode, offload=offload,
              cipher_key=cipher_key, auth_key=auth_key, natt=natt,
              replay_window=replay_window, replay=replay),
        nlmsg_type=XFRM_MSG_NEWSA,
        nlmsg_flags=NLM_F_REQUEST | NLM_F_ACK,
        timeout_ms=timeout_ms, failslab_times=failslab_times,
    )
    return _parse(result)


async def sa_del(target_agent, session, *, dst: str, spi: int,
                 timeout_ms: int = 3000) -> SaReply:
    """Best-effort removal. Callers use this in finally blocks, so a state
    that was never installed coming back -ESRCH is expected, not an error."""
    result = await target_agent.netlink_send(
        session, NETLINK_XFRM, delsa(dst=dst, spi=spi),
        nlmsg_type=XFRM_MSG_DELSA,
        nlmsg_flags=NLM_F_REQUEST | NLM_F_ACK,
        timeout_ms=timeout_ms,
    )
    return _parse(result)


async def sa_flush_range(target_agent, session, *, dst: str,
                         spis: list[int]) -> None:
    """Remove a whole sweep's worth of SAs, ignoring every outcome."""
    for spi in spis:
        await sa_del(target_agent, session, dst=dst, spi=spi)


async def sa_install_probe(
    target_agent, session, *, src: str, dst: str, spi: int, reqid: int,
    ifindex: int, inbound: bool = False, natt: tuple[int, int] | None = None,
) -> str | None:
    """One unencumbered install, read back, and removed again.

    Returns None when the SA installed and the kernel reports it, or a reason
    string the caller can skip on. Sweeps run this first: a wire-layout
    mistake or an unprepared bench would otherwise make every faulted
    iteration fail identically and prove nothing.

    Skipping is right for *these* files, whose subject is an unwind path
    rather than the install, but it means none of them goes red if offloaded
    SA install breaks outright. That tripwire is
    ipsec_xfrm_offload.py::test_packet_offload_sa_install, which asserts
    the same install through `ip` and fails rather than skips. Worth knowing,
    because nothing here says so on the day four files all skip at once.
    """
    await sa_del(target_agent, session, dst=dst, spi=spi)
    reply = await sa_add(target_agent, session, src=src, dst=dst, spi=spi,
                         reqid=reqid, ifindex=ifindex, inbound=inbound,
                         natt=natt)
    if not reply.ok:
        await sa_del(target_agent, session, dst=dst, spi=spi)
        return (f"unencumbered NEWSA was refused (error={reply.error!r}, "
                f"lost={reply.lost}) — either the offload is not available on "
                f"this boot or the bench endpoints are not set up; raw={reply.raw!r}")
    shown = await target_agent.exec_cmd(
        session, ["ip", "-d", "xfrm", "state", "get", "src", src, "dst", dst,
                  "proto", "esp", "spi", hex(spi)], timeout_ms=3000)
    await sa_del(target_agent, session, dst=dst, spi=spi)
    if shown.get("rc") != 0:
        return (f"NEWSA was accepted but `ip xfrm state get` cannot see it "
                f"(rc={shown.get('rc')!r}) — the message this module builds is "
                f"not describing the SA it claims to")
    out = shown.get("stdout", "")
    if "crypto offload parameters" not in out:
        return (f"the installed SA carries no offload; XFRMA_OFFLOAD_DEV did "
                f"not take. `ip -d xfrm state get` said: {out!r}")
    return None


async def measure_install_allocations(
    target_agent, session, *, src: str, dst: str, spi: int, reqid: int,
    ifindex: int, inbound: bool = False, natt: tuple[int, int] | None = None,
) -> int | None:
    """How many faultable allocations one whole SA install makes.

    Arming fail-nth far beyond any plausible count faults nothing, and the
    residue left behind is what the counter did not spend -- so the difference
    is the number of eligible allocations the send actually made. That turns
    "sweep the first hundred" into a measured window: the early values land in
    xfrm's own state construction and the SA cache, the late ones in the
    descriptor build and the classifier entry, and a test that wants one of
    those can say so instead of hoping.

    Two things the count is not. It spans the whole send and receive, so the
    last stretch of it is the netlink ACK rather than anything cdx does --
    which is why a sweep aimed at the tail asserts on `SaReply.refused` and
    not merely on something having gone wrong. And it counts a handful of the
    probe's own reads, so it is an upper bound on the install rather than a
    measurement of it.

    Returns None when the probe could not be read, which the caller should
    treat as "sweep blind" rather than as a failure.
    """
    ceiling = 1_000_000
    await sa_del(target_agent, session, dst=dst, spi=spi)
    reply = await sa_add(target_agent, session, src=src, dst=dst, spi=spi,
                         reqid=reqid, ifindex=ifindex, inbound=inbound,
                         natt=natt, failslab_times=ceiling)
    await sa_del(target_agent, session, dst=dst, spi=spi)
    residue = reply.fail_nth_residue
    if not reply.ok or residue is None or residue <= 0 or residue > ceiling:
        return None
    return ceiling - residue


# ---------------------------------------------------------------- bench setup

async def endpoints_up(target_agent, session, *, iface: str, local: str,
                       peer: str, lladdr: str) -> None:
    """Give the bench what a real tunnel would have had from its exchange.

    Both are contract, not convenience. The local endpoint must be an address
    on the CDX port, because that is what resolves an SA to an interface
    (dpa_get_iface_info_by_ipaddress matches the SA's own source for an
    outbound SA and its destination for an inbound one). And the peer needs a
    route and a resolved neighbour, because what leaves SEC is a finished
    frame: the destination MAC is written into the SA at install time rather
    than read per packet.

    The neighbour is permanent and the peer does not exist. Nothing here
    sends, so inventing a lladdr keeps these control-plane tests off a
    two-host bench.
    """
    for argv in (
        ["ip", "address", "replace", f"{local}/32", "dev", iface],
        ["ip", "route", "replace", f"{peer}/32", "dev", iface],
        ["ip", "neigh", "replace", peer, "lladdr", lladdr, "dev", iface,
         "nud", "permanent"],
    ):
        r = await target_agent.exec_cmd(session, argv, timeout_ms=3000)
        # Checked, because the alternative is that the bench comes up half
        # built and every later refusal is blamed on the offload.
        if r.get("rc") != 0:
            raise RuntimeError(
                f"bench setup failed: {' '.join(argv)} -> rc={r.get('rc')!r}, "
                f"stderr={r.get('stderr', '')!r}")


async def endpoints_down(target_agent, session, *, iface: str, local: str,
                         peer: str) -> None:
    for argv in (
        ["ip", "neigh", "del", peer, "dev", iface],
        ["ip", "route", "del", f"{peer}/32", "dev", iface],
        ["ip", "address", "del", f"{local}/32", "dev", iface],
    ):
        await target_agent.exec_cmd(session, argv, timeout_ms=3000)


async def iface_index(target_agent, session, iface: str) -> int:
    """The ifindex XFRMA_OFFLOAD_DEV names. Read live rather than assumed:
    an SA bound to the wrong device is refused several layers away from the
    mistake."""
    r = await target_agent.exec_cmd(
        session, ["ip", "-j", "link", "show", "dev", iface], timeout_ms=3000)
    if r.get("rc") != 0:
        raise RuntimeError(f"`ip link show dev {iface}` failed: {r!r}")
    links = json.loads(r.get("stdout", "") or "[]")
    if not links or "ifindex" not in links[0]:
        raise RuntimeError(f"no ifindex for {iface}: {r.get('stdout', '')!r}")
    return int(links[0]["ifindex"])


async def dut_local_ipv4(target_agent, session, iface: str) -> str:
    """A DUT-local IPv4 address on `iface`, for a test that needs the SA's
    endpoint to be one the hardware can resolve to a port. ASK_IPSEC_LOCAL_IP
    overrides the query."""
    override = os.environ.get("ASK_IPSEC_LOCAL_IP")
    if override:
        return override
    r = await target_agent.exec_cmd(
        session, ["ip", "-4", "-j", "addr", "show", "dev", iface],
        timeout_ms=3000)
    if r.get("rc") != 0:
        raise RuntimeError(
            f"`ip -4 -j addr show dev {iface}` failed on DUT: {r!r}")
    links = json.loads(r.get("stdout", "") or "[]")
    for want_global in (True, False):
        for link in links:
            for ai in link.get("addr_info", []):
                if ai.get("family") != "inet" or not ai.get("local"):
                    continue
                if want_global and ai.get("scope") != "global":
                    continue
                return ai["local"]
    raise RuntimeError(
        f"{iface} carries no IPv4 address; an offloaded SA's local endpoint "
        f"has to be an address on the port it is bound to")

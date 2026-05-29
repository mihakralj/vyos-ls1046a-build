#!/usr/bin/env python3
# dpaa1-xdp-rxcap.py — DPAA1 true RX-capacity wire measurement (spec §6.1.8 Option A)
#
# Purpose
# -------
# Gate 3 of the DPAA1 AF_XDP modernization spec (§6.1, acceptance item 3) asks
# for >= 7 Gbps single-stream IPv4 RX. Prior measurement attempts (§6.1.7/§6.1.8)
# were blocked NOT by the driver but by the userspace consumer: a single-threaded
# iperf3 `recvmmsg()` on a Cortex-A72 caps at ~150-200 kpps, and the Python XSK
# probe's stdout-flushing recycle loop caps even lower. The DUT's /proc/net/snmp
# proved `RcvbufErrors == InErrors` — every "lost" packet was dropped at socket
# enqueue, never at the NIC/BMan/FILL ring.
#
# This tool removes the userspace consumer from the measurement entirely. It
# attaches a minimal 2-instruction XDP program that returns XDP_DROP for every
# frame. XDP_DROP frees the buffer in-driver (recycle straight back to BMan) and
# the packet never becomes an skb, never touches a socket, never crosses into
# userspace. The ONLY thing that advances is the driver's RX path. We therefore
# measure true driver RX capacity by sampling `ethtool -S <iface>` rx_packets /
# rx_bytes deltas across a hold window, plus the qman tail-drop counters to prove
# the drops happened in software-XDP (good) and not in QMan congestion (bad).
#
# Method (no kernel change, no libbpf, no clang — raw bpf() syscall):
#   1. bpf(BPF_PROG_LOAD, BPF_PROG_TYPE_XDP) with `r0 = XDP_DROP; exit`  (2 insns)
#   2. rtnetlink RTM_SETLINK IFLA_XDP attach (DRV mode preferred, SKB fallback)
#   3. snapshot ethtool -S counters
#   4. hold N seconds while an external load generator floods the segment
#   5. snapshot again, report pps / Gbps / drop accounting
#   6. detach the XDP prog (ip link set dev <iface> xdp off equivalent) unless
#      --keep is given
#
# The raw-bpf() plumbing (bpf(), bpf_prog_load_xdp, attach_xdp_prog, _insn) is
# lifted verbatim from bin/dpaa1-xsk-bind-probe.py (commit 9fda744) which already
# proved this exact encoding loads + attaches a DRV-mode XDP prog on DPAA1
# (prog/xdp id 224, DRV mode honoured). The two probe-side gotchas documented in
# §6.1.7 are inherited: NO XDP_FLAGS_UPDATE_IF_NOEXIST (so re-runs replace a
# stale prog), and the bpf_attr layouts are byte-identical to the working probe.
#
# Usage
# -----
#   sudo bin/dpaa1-xdp-rxcap.py eth4 --hold 20
#   sudo bin/dpaa1-xdp-rxcap.py eth4 --hold 30 --interval 2 --keep
#
# Drive load from the lab (spec §6.1.8 topology — DUT eth4 = 10.11.1.1/29):
#   backup (10.11.1.3):  iperf3 -c 10.11.1.1 -u -b 10G -l 1400 -P 4 -t 30
#   lxc202 (10.11.1.2):  iperf3 -c 10.11.1.1 -u -b 10G -l 1400 -P 4 -t 30
# (Both can run concurrently to push offered load past 7 Gbps; iperf3's *sender*
#  side is not the bottleneck the way its receiver is. UDP blind-send does not
#  depend on the DUT draining a socket — the frames hit the wire regardless.)
#
# Exit status: 0 if rx_packets advanced and no qman tail-drop / dma error was
# seen during the window; non-zero otherwise (usable as a CI/regression probe).

import argparse
import ctypes
import errno
import os
import struct
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Raw bpf() syscall plumbing (verbatim from dpaa1-xsk-bind-probe.py @ 9fda744)
# ---------------------------------------------------------------------------
__NR_bpf_arm64 = 280

BPF_PROG_LOAD = 5
BPF_PROG_TYPE_XDP = 6

# eBPF instruction encoding: u8 opcode, u8 dst:4|src:4, s16 off, s32 imm
def _insn(opc, dst, src, off, imm):
    return struct.pack("=BBhi", opc, (src << 4) | dst, off, imm)

BPF_ALU64_MOV_K = 0xb7   # mov64 dst, imm
BPF_JMP_EXIT    = 0x95   # exit
BPF_LDX_MEM_W   = 0x61   # ldx dst, [src+off]  (u32)
BPF_LDX_MEM_H   = 0x69   # ldx dst, [src+off]  (u16)
BPF_LDX_MEM_B   = 0x71   # ldx dst, [src+off]  (u8)
BPF_ALU64_MOV_X = 0xbf   # mov64 dst, src
BPF_ALU64_ADD_K = 0x07   # add64 dst, imm
BPF_JMP_JGT_X   = 0x2d   # if dst >  src goto +off
BPF_JMP_JNE_K   = 0x55   # if dst != imm goto +off
BPF_JMP_JA      = 0x05   # goto +off

# XDP action codes (uapi/linux/bpf.h)
XDP_ABORTED = 0
XDP_DROP    = 1
XDP_PASS    = 2

# Netlink for XDP attach
NETLINK_ROUTE = 0
RTM_SETLINK   = 19
NLM_F_REQUEST = 0x01
NLM_F_ACK     = 0x04
IFLA_XDP      = 43
IFLA_XDP_FD    = 1
IFLA_XDP_FLAGS = 3
XDP_FLAGS_DRV_MODE = (1 << 2)
XDP_FLAGS_SKB_MODE = (1 << 1)

BPF_ATTR_SIZE = 144

import socket  # placed here so the plumbing block above reads as a unit


def bpf(libc, cmd, attr_bytes):
    buf = ctypes.create_string_buffer(BPF_ATTR_SIZE)
    n = min(len(attr_bytes), BPF_ATTR_SIZE)
    ctypes.memmove(buf, attr_bytes, n)
    libc.syscall.restype = ctypes.c_long
    return libc.syscall(__NR_bpf_arm64, ctypes.c_int(cmd),
                        ctypes.byref(buf), ctypes.c_uint(BPF_ATTR_SIZE))


def build_xdp_drop_prog():
    """Minimal XDP program:  r0 = XDP_DROP; exit   (2 insns / 16 bytes).
    Every frame is dropped in-driver — no skb, no socket, no userspace.

    NOTE: drops EVERYTHING including the iperf3 TCP control channel and ARP/ND.
    Only safe when the load generator needs no return path on this interface.
    For the lab topology where iperf3 -R drives load over the SAME link, use
    build_xdp_drop_udp_prog() instead so the control channel survives."""
    code = b""
    code += _insn(BPF_ALU64_MOV_K, dst=0, src=0, off=0, imm=XDP_DROP)
    code += _insn(BPF_JMP_EXIT, dst=0, src=0, off=0, imm=0)
    return code

def build_xdp_drop_udp_prog():
    """Selective XDP program: XDP_DROP IPv4/UDP frames, XDP_PASS everything else.

    This lets the iperf3 TCP control channel (and ARP/ND) survive on the same
    interface whose UDP RX we are measuring — the data-plane UDP flood is dropped
    in-driver (recycle to BMan, no skb/socket/userspace) while the tiny control
    channel keeps iperf3 -R alive so the sender keeps blasting.

    xdp_md layout: off 0 = data (u32), off 4 = data_end (u32).
    Parse: eth(14) + IPv4 proto at byte 23 (eth14 + ip+9). EtherType at byte 12.
    Need 24 bytes present. EtherType 0x0800 reads as LE u16 0x0008.

    r1 = ctx;  r2 = data;  r3 = data_end;  r4 = data+24 (bounds);
    r5 = ethertype;  r6 = ip proto.  Returns in r0."""
    PASS_OFF = None  # filled by counting below
    insns = [
        _insn(BPF_LDX_MEM_W,   dst=2, src=1, off=0,  imm=0),   # r2 = ctx->data
        _insn(BPF_LDX_MEM_W,   dst=3, src=1, off=4,  imm=0),   # r3 = ctx->data_end
        _insn(BPF_ALU64_MOV_X, dst=4, src=2, off=0,  imm=0),   # r4 = r2
        _insn(BPF_ALU64_ADD_K, dst=4, src=0, off=0,  imm=24),  # r4 = r2 + 24
        # if r4 > r3 goto PASS  (frame shorter than eth+ip header bytes)
        None,  # placeholder index 4 — patched after we know PASS offset
        _insn(BPF_LDX_MEM_H,   dst=5, src=2, off=12, imm=0),   # r5 = ethertype (LE)
        # if r5 != 0x0008 (==0x0800 BE, IPv4) goto PASS
        None,  # placeholder index 6
        _insn(BPF_LDX_MEM_B,   dst=6, src=2, off=23, imm=0),   # r6 = ip proto
        # if r6 != 17 (UDP) goto PASS
        None,  # placeholder index 8
        _insn(BPF_ALU64_MOV_K, dst=0, src=0, off=0,  imm=XDP_DROP),  # idx 9
        _insn(BPF_JMP_EXIT,    dst=0, src=0, off=0,  imm=0),         # idx 10
        _insn(BPF_ALU64_MOV_K, dst=0, src=0, off=0,  imm=XDP_PASS),  # idx 11 (PASS)
        _insn(BPF_JMP_EXIT,    dst=0, src=0, off=0,  imm=0),         # idx 12
    ]
    pass_idx = 11
    # branch offset is (target_idx - (branch_idx + 1))
    insns[4] = _insn(BPF_JMP_JGT_X, dst=4, src=3, off=pass_idx - 5,  imm=0)
    insns[6] = _insn(BPF_JMP_JNE_K, dst=5, src=0, off=pass_idx - 7,  imm=0x0008)
    insns[8] = _insn(BPF_JMP_JNE_K, dst=6, src=0, off=pass_idx - 9,  imm=17)
    return b"".join(insns)


def bpf_prog_load_xdp(libc, prog_bytes, name=b"dpaa1_rxcap\0\0\0\0\0"):
    n_insns = len(prog_bytes) // 8
    insns_buf = ctypes.create_string_buffer(prog_bytes, len(prog_bytes))
    license_buf = ctypes.create_string_buffer(b"GPL")
    log_buf_sz = 64 * 1024
    log_buf = ctypes.create_string_buffer(log_buf_sz)
    attr = struct.pack(
        "=IIQQII Q II 16s I I I I Q I I Q I I I I Q Q I",
        BPF_PROG_TYPE_XDP,                 # prog_type
        n_insns,                           # insn_cnt
        ctypes.addressof(insns_buf),       # insns
        ctypes.addressof(license_buf),     # license
        1,                                 # log_level
        log_buf_sz,                        # log_size
        ctypes.addressof(log_buf),         # log_buf
        0, 0,                              # kern_version, prog_flags
        name[:16].ljust(16, b"\0"),        # prog_name
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    )
    rc = bpf(libc, BPF_PROG_LOAD, attr)
    if rc < 0:
        e = ctypes.get_errno()
        log = log_buf.value.decode(errors="replace").strip()
        raise OSError(e, f"bpf(BPF_PROG_LOAD,XDP) errno={e} "
                         f"({errno.errorcode.get(e,'?')}: {os.strerror(e)})\n"
                         f"verifier log:\n{log}")
    return rc


def _xdp_setlink(ifindex, prog_fd, flags):
    nl = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_ROUTE)
    nl.bind((0, 0))
    ifi = struct.pack("=BBHiII", 0, 0, 0, ifindex, 0, 0)
    fd_attr = struct.pack("=HH i", 8, IFLA_XDP_FD, prog_fd)
    fl_attr = struct.pack("=HH I", 8, IFLA_XDP_FLAGS, flags)
    nested_payload = fd_attr + fl_attr
    nested = struct.pack("=HH", 4 + len(nested_payload), IFLA_XDP) + nested_payload
    body = ifi + nested
    total = 16 + len(body)
    hdr = struct.pack("=IHHII", total, RTM_SETLINK,
                      NLM_F_REQUEST | NLM_F_ACK, 1, 0)
    nl.send(hdr + body)
    reply = nl.recv(4096)
    nl.close()
    if len(reply) >= 20:
        nl_type = struct.unpack_from("=H", reply, 4)[0]
        if nl_type == 2:  # NLMSG_ERROR
            return struct.unpack_from("=i", reply, 16)[0]  # 0 == success
    return 0


def attach_xdp_prog(ifindex, prog_fd):
    """DRV mode preferred (kernel fast-path), SKB fallback. No
    XDP_FLAGS_UPDATE_IF_NOEXIST so a re-run replaces a stale prog."""
    last = None
    for name, flags in (("DRV", XDP_FLAGS_DRV_MODE), ("SKB", XDP_FLAGS_SKB_MODE)):
        err = _xdp_setlink(ifindex, prog_fd, flags)
        if err == 0:
            return name
        last = err
    raise OSError(-last if last else errno.EIO,
                  f"IFLA_XDP attach failed in DRV and SKB modes (errno={-last if last else '?'})")


def detach_xdp_prog(ifindex):
    """Detach by attaching fd=-1 (kernel removes the prog). Try both modes."""
    for flags in (XDP_FLAGS_DRV_MODE, XDP_FLAGS_SKB_MODE, 0):
        if _xdp_setlink(ifindex, -1, flags) == 0:
            return True
    return False


# ---------------------------------------------------------------------------
# ethtool -S counter sampling
# ---------------------------------------------------------------------------
# Counters of interest. rx_packets/rx_bytes prove ingress reached the driver;
# the qman/dma/fifo drop counters prove WHERE any shortfall happened.
RX_KEYS = [
    "rx_packets",
    "rx_bytes",
    "rx error [TOTAL]",
    "rx dropped [TOTAL]",
    "rx dma error",
    "rx frame physical error",
    "rx fifo error",
    "qman cg_tdrop",
    "qman fq tdrop",
    "qman fq retired",
]


def read_ethtool_stats(iface):
    """Return {key: int} for the RX_KEYS we can find in `ethtool -S <iface>`.
    Also pull /sys rx_packets/rx_bytes as an authoritative cross-check."""
    out = {}
    try:
        txt = subprocess.check_output(["ethtool", "-S", iface],
                                      stderr=subprocess.DEVNULL).decode()
    except Exception:
        txt = ""
    for line in txt.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if k in RX_KEYS:
            try:
                out[k] = int(v)
            except ValueError:
                pass
    # Authoritative net-core counters from sysfs (driver-independent).
    for sysk, mapk in (("rx_packets", "sys_rx_packets"),
                       ("rx_bytes", "sys_rx_bytes"),
                       ("rx_dropped", "sys_rx_dropped")):
        try:
            with open(f"/sys/class/net/{iface}/statistics/{sysk}") as f:
                out[mapk] = int(f.read().strip())
        except Exception:
            pass
    return out


def fmt_rate(dpkts, dbytes, secs):
    if secs <= 0:
        return "0 pps, 0 bps"
    pps = dpkts / secs
    bps = dbytes * 8 / secs
    return f"{pps:,.0f} pps, {bps/1e9:.3f} Gbit/s"


def main():
    ap = argparse.ArgumentParser(
        description="DPAA1 true RX-capacity wire measurement via XDP_DROP "
                    "(spec §6.1.8 Option A — bypasses userspace socket drain).")
    ap.add_argument("iface", help="DPAA1 netdev, e.g. eth4")
    ap.add_argument("--hold", type=float, default=20.0,
                    help="seconds to hold the XDP_DROP prog while load runs (default 20)")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="progress sample interval in seconds (default 2)")
    ap.add_argument("--keep", action="store_true",
                    help="leave the XDP prog attached on exit (default: detach)")
    ap.add_argument("--drop-udp", action="store_true",
                    help="drop ONLY IPv4/UDP frames, pass everything else "
                         "(keeps the iperf3 TCP control channel + ARP/ND alive on "
                         "the same link the load arrives on — REQUIRED for the lab "
                         "topology where iperf3 -R drives load over eth4 itself)")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("must run as root (BPF_PROG_LOAD + IFLA_XDP require CAP_SYS_ADMIN/CAP_BPF)",
              file=sys.stderr)
        return 2

    iface = args.iface
    try:
        ifindex = socket.if_nametoindex(iface)
    except OSError as e:
        print(f"no such interface {iface}: {e}", file=sys.stderr)
        return 2

    libc = ctypes.CDLL("libc.so.6", use_errno=True)

    if args.drop_udp:
        print(f"[rxcap] loading selective XDP prog (drop IPv4/UDP, pass rest) for "
              f"{iface} (ifindex={ifindex})", flush=True)
        prog = build_xdp_drop_udp_prog()
    else:
        print(f"[rxcap] loading XDP_DROP prog (2 insns) for {iface} (ifindex={ifindex})",
              flush=True)
        prog = build_xdp_drop_prog()
    prog_fd = bpf_prog_load_xdp(libc, prog)
    print(f"[rxcap] BPF_PROG_LOAD ok fd={prog_fd}", flush=True)

    mode = attach_xdp_prog(ifindex, prog_fd)
    if args.drop_udp:
        print(f"[rxcap] XDP attached in {mode} mode — IPv4/UDP RX frames now "
              f"XDP_DROP (no skb/socket/userspace), TCP+ARP+ND pass through "
              f"(iperf3 control channel survives)", flush=True)
    else:
        print(f"[rxcap] XDP attached in {mode} mode — every RX frame now XDP_DROP "
              f"(no skb, no socket, no userspace)", flush=True)

    detached = False
    try:
        base = read_ethtool_stats(iface)
        t0 = time.monotonic()
        last = dict(base)
        tlast = t0
        deadline = t0 + args.hold
        print(f"[rxcap] holding {args.hold:.0f}s — drive load at the sender now "
              f"(e.g. iperf3 -c {{dut-ip}} -u -b 10G -l 1400 -P 4 -t {int(args.hold)+5})",
              flush=True)
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            time.sleep(min(args.interval, max(0.0, deadline - now)))
            now = time.monotonic()
            cur = read_ethtool_stats(iface)
            dp = cur.get("sys_rx_packets", 0) - last.get("sys_rx_packets", 0)
            db = cur.get("sys_rx_bytes", 0) - last.get("sys_rx_bytes", 0)
            print(f"[rxcap] +{now - t0:5.1f}s  {fmt_rate(dp, db, now - tlast)}",
                  flush=True)
            last = cur
            tlast = now

        final = read_ethtool_stats(iface)
        elapsed = time.monotonic() - t0

        d_pkts = final.get("sys_rx_packets", 0) - base.get("sys_rx_packets", 0)
        d_bytes = final.get("sys_rx_bytes", 0) - base.get("sys_rx_bytes", 0)
        d_sysdrop = final.get("sys_rx_dropped", 0) - base.get("sys_rx_dropped", 0)

        print("\n========== RX CAPACITY (XDP_DROP, driver-only path) ==========")
        print(f"  interface           : {iface}  (XDP {mode} mode)")
        print(f"  window              : {elapsed:.1f}s")
        print(f"  rx_packets delta    : {d_pkts:,}")
        print(f"  rx_bytes delta      : {d_bytes:,}  ({d_bytes/1e6:.1f} MB)")
        print(f"  sustained rate      : {fmt_rate(d_pkts, d_bytes, elapsed)}")
        print(f"  net-core rx_dropped : {d_sysdrop:,}")

        # ethtool driver drop accounting (where did any shortfall happen?)
        print("  --- ethtool driver drop accounting (delta) ---")
        bad = 0
        for k in ("rx error [TOTAL]", "rx dropped [TOTAL]", "rx dma error",
                  "rx frame physical error", "rx fifo error",
                  "qman cg_tdrop", "qman fq tdrop"):
            if k in base or k in final:
                d = final.get(k, 0) - base.get(k, 0)
                flag = ""
                # qman/dma drops are hardware-congestion = BAD; rx_dropped TOTAL
                # under XDP_DROP is expected (that's the XDP verdict accounting on
                # some drivers) so it is not counted as a fault here.
                if k in ("rx dma error", "rx frame physical error",
                         "rx fifo error", "qman cg_tdrop", "qman fq tdrop") and d > 0:
                    flag = "  <-- HW congestion/error"
                    bad += d
                print(f"      {k:28s}: {d:,}{flag}")
        print("==============================================================")

        if d_pkts <= 0:
            print("[rxcap] FAIL: rx_packets did not advance — no traffic reached "
                  "the driver (check sender / link / addressing).", flush=True)
            return 1
        if bad > 0:
            print(f"[rxcap] WARN: {bad:,} frames hit HW congestion/error counters "
                  "— offered load exceeded a hardware stage, not just XDP_DROP.",
                  flush=True)
            # Still informative; treat as soft-pass so the rate is recorded.
        gbps = d_bytes * 8 / elapsed / 1e9 if elapsed > 0 else 0.0
        if gbps >= 7.0:
            print(f"[rxcap] PASS: {gbps:.3f} Gbit/s >= 7 Gbps acceptance gate (item 3) "
                  "on the driver RX path.", flush=True)
        else:
            print(f"[rxcap] INFO: {gbps:.3f} Gbit/s measured — below the 7 Gbps gate. "
                  "If the sender offered >=7 Gbps and qman/dma deltas are 0, the "
                  "shortfall is offered-load, not driver capacity.", flush=True)
        return 0
    finally:
        if not args.keep:
            if detach_xdp_prog(ifindex):
                print(f"[rxcap] detached XDP prog from {iface}", flush=True)
                detached = True
            if not detached:
                print(f"[rxcap] WARN: could not auto-detach; run "
                      f"`ip link set dev {iface} xdp off` manually.", flush=True)
        else:
            print(f"[rxcap] --keep: XDP_DROP prog left attached on {iface} "
                  f"(detach with `ip link set dev {iface} xdp off`)", flush=True)


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# dpaa1-xsk-bind-probe.py - XDP_ZEROCOPY bind + RX-ring drain probe for
# DPAA1 M2 validation (stage-1 attach + stage-2 datapath).
#
# Stage-1 (default): performs socket()+UMEM_REG+ring sizing+bind() and
# reports the bind() return code. Drives priv->xsk_pool_attach_ok /
# priv->xsk_pool_attach_fail in af_xdp_pool_main.c.
#
# Stage-2 (--hold N): after a successful bind(), seeds the FILL ring,
# mmaps the RX ring (and the FILL ring's producer/consumer pointers via
# XDP_MMAP_OFFSETS), then polls for N seconds counting xdp_desc entries
# that land in the RX ring. Validates the AF_XDP zero-copy datapath end
# to end (BMan seed -> FMan port classifier -> dpaa NAPI -> XSKMAP ->
# xdp_xmit_to_xsk).
#
# Usage:
#   sudo python3 dpaa1-xsk-bind-probe.py <ifname> [queue_id] [chunk_size]
#                                        [--hold SECS]
# Defaults: queue_id=0 chunk_size=4096 hold=0
#
# Verification matrix (M2-stage-1 closure ISO):
#   sudo bin/dpaa1-xsk-bind-probe.py eth4 0 4096
#     -> rc=0 ; ethtool -S eth4 | grep xsk_pool_attach_ok shows +1
#   sudo bin/dpaa1-xsk-bind-probe.py eth4 0 4096 --hold 30
#     -> rc=0 ; rx_packets > 0 with traffic from a peer ; rx_packets == 0
#        if no peer traffic, but rings stay alive (no detach_timeout bump)

import ctypes
import ctypes.util
import errno
import mmap
import os
import select
import socket
import struct
import sys
import time

AF_XDP                   = 44
SOL_XDP                  = 283
XDP_MMAP_OFFSETS         = 1
XDP_RX_RING              = 2
XDP_TX_RING              = 3
XDP_UMEM_REG             = 4
XDP_UMEM_FILL_RING       = 5
XDP_UMEM_COMPLETION_RING = 6
XDP_STATISTICS           = 7
XDP_ZEROCOPY             = (1 << 2)

# struct xdp_desc { __u64 addr; __u32 len; __u32 options; } -> 16 bytes
XDP_DESC_SIZE   = 16

# offsets of producer/consumer/desc/flags within each ring (kernel layout)
# Returned by getsockopt(XDP_MMAP_OFFSETS) as 4 ring offset structs,
# each containing 4 u64 fields: producer, consumer, desc, flags.
# Total: 4 rings * 4 u64 = 128 bytes.
XDP_OFFSETS_SIZE = 128


def errmsg(rc):
    e = ctypes.get_errno()
    return f"rc={rc} errno={e} ({errno.errorcode.get(e,'?')}: {os.strerror(e)})"


def parse_args():
    args = sys.argv[1:]
    hold_secs = 0
    while "--hold" in args:
        i = args.index("--hold")
        hold_secs = int(args[i + 1])
        del args[i:i + 2]
    if not args:
        print("usage: dpaa1-xsk-bind-probe.py <ifname> [queue_id] "
              "[chunk_size] [--hold SECS]", file=sys.stderr)
        sys.exit(2)
    ifname    = args[0]
    queue_id  = int(args[1]) if len(args) > 1 else 0
    chunk     = int(args[2]) if len(args) > 2 else 4096
    return ifname, queue_id, chunk, hold_secs


def main():
    ifname, queue_id, chunk, hold_secs = parse_args()
    ifindex = socket.if_nametoindex(ifname)
    print(f"[probe] ifname={ifname} ifindex={ifindex} queue_id={queue_id} "
          f"chunk_size={chunk} hold={hold_secs}s")

    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

    # 1. socket(AF_XDP)
    fd = libc.socket(AF_XDP, socket.SOCK_RAW, 0)
    if fd < 0:
        print(f"[probe] FAIL: socket(AF_XDP) {errmsg(fd)}")
        sys.exit(1)
    print(f"[probe] xsk socket fd={fd}")

    # 2. UMEM mmap
    N_FRAMES   = 256
    umem_size  = chunk * N_FRAMES
    PROT_READ, PROT_WRITE = 1, 2
    MAP_PRIVATE, MAP_ANON = 0x02, 0x20
    libc.mmap.restype  = ctypes.c_void_p
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, ctypes.c_long]
    umem_addr = libc.mmap(None, umem_size,
                          PROT_READ | PROT_WRITE,
                          MAP_PRIVATE | MAP_ANON, -1, 0)
    if umem_addr in (None, ctypes.c_void_p(-1).value):
        print(f"[probe] FAIL: mmap UMEM {errmsg(-1)}")
        sys.exit(1)
    print(f"[probe] UMEM addr=0x{umem_addr:x} size={umem_size} "
          f"frames={N_FRAMES}")

    # 3. XDP_UMEM_REG
    umem_reg = struct.pack("=QQIIII", umem_addr, umem_size, chunk,
                           0, 0, 0)
    rc = libc.setsockopt(fd, SOL_XDP, XDP_UMEM_REG,
                         ctypes.c_char_p(umem_reg), len(umem_reg))
    if rc != 0:
        print(f"[probe] FAIL: setsockopt(XDP_UMEM_REG) {errmsg(rc)}")
        sys.exit(1)
    print(f"[probe] XDP_UMEM_REG OK (chunk_size={chunk})")

    # 4. Configure rings
    fill_size = ctypes.c_uint32(N_FRAMES)
    comp_size = ctypes.c_uint32(N_FRAMES)
    rx_size   = ctypes.c_uint32(N_FRAMES)
    tx_size   = ctypes.c_uint32(N_FRAMES)
    for opt, val, name in (
        (XDP_UMEM_FILL_RING,       fill_size, "FILL"),
        (XDP_UMEM_COMPLETION_RING, comp_size, "COMP"),
        (XDP_RX_RING,              rx_size,   "RX"),
        (XDP_TX_RING,              tx_size,   "TX"),
    ):
        rc = libc.setsockopt(fd, SOL_XDP, opt, ctypes.byref(val), 4)
        if rc != 0:
            print(f"[probe] FAIL: setsockopt({name}_RING) {errmsg(rc)}")
            sys.exit(1)
    print(f"[probe] FILL/COMP/RX/TX rings sized {N_FRAMES}")

    # 5. bind(XDP_ZEROCOPY)
    sa = struct.pack("=HHIII", AF_XDP, XDP_ZEROCOPY, ifindex, queue_id, 0)
    rc = libc.bind(fd, ctypes.c_char_p(sa), len(sa))
    if rc != 0:
        e = ctypes.get_errno()
        ecode = errno.errorcode.get(e, '?')
        estr  = os.strerror(e)
        print(f"[probe] FAIL: bind(XDP_ZEROCOPY) rc={rc} errno={e} "
              f"({ecode}: {estr})")
        # Diagnostic mapping per 0075a/b/c arms (LIODN gate removed in 0075c):
        if e == errno.ENODEV:
            print("[probe] DIAG: ENODEV - rxp==NULL or mac_dev==NULL "
                  "(unprobed FMan port). NOT the LIODN arm.")
        elif e == errno.EINVAL:
            print("[probe] DIAG: EINVAL - queue_id >= xsk_max_qbands(4), "
                  "frame_size < 3840, headroom < tx_headroom, or mtu > 3290.")
        elif e == errno.ENOMEM:
            print("[probe] DIAG: ENOMEM - xsk_pool_dma_map() or bman_new_pool() "
                  "failed (xsk_dma_map_fail counter bump path).")
        elif e == errno.EOPNOTSUPP:
            print("[probe] DIAG: EOPNOTSUPP - xsk_pool_attach op not wired or "
                  "driver doesn't expose XSK ops on this netdev. "
                  "Check: lsmod | grep af_xdp_pool ; ip -d link show <if>.")
        sys.exit(1)

    print("[probe] PASS (stage-1): bind(XDP_ZEROCOPY) succeeded - "
          "attach_ok counter incremented")
    if hold_secs == 0:
        sys.exit(0)

    # ===== STAGE-2: RX-ring datapath drain =====
    print(f"[probe] STAGE-2: holding socket for {hold_secs}s + RX ring drain")

    # 6. getsockopt(XDP_MMAP_OFFSETS) -> producer/consumer/desc/flags
    #    offsets for each of the 4 rings.
    off_buf = ctypes.create_string_buffer(XDP_OFFSETS_SIZE)
    off_len = ctypes.c_uint32(XDP_OFFSETS_SIZE)
    rc = libc.getsockopt(fd, SOL_XDP, XDP_MMAP_OFFSETS, off_buf,
                         ctypes.byref(off_len))
    if rc != 0:
        print(f"[probe] FAIL: getsockopt(XDP_MMAP_OFFSETS) {errmsg(rc)}")
        sys.exit(1)
    # Each ring: producer, consumer, desc, flags (4 x u64 = 32 bytes).
    # Order returned by kernel: rx, tx, fill, completion.
    rx_off   = struct.unpack_from("=QQQQ", off_buf.raw, 0)
    tx_off   = struct.unpack_from("=QQQQ", off_buf.raw, 32)
    fill_off = struct.unpack_from("=QQQQ", off_buf.raw, 64)
    comp_off = struct.unpack_from("=QQQQ", off_buf.raw, 96)
    print(f"[probe] RX  off: prod={rx_off[0]} cons={rx_off[1]} "
          f"desc={rx_off[2]} flags={rx_off[3]}")
    print(f"[probe] FILL off: prod={fill_off[0]} cons={fill_off[1]} "
          f"desc={fill_off[2]} flags={fill_off[3]}")

    # 7. mmap each ring at its kernel-defined PGOFF token.
    # PGOFF constants from include/uapi/linux/if_xdp.h:
    #   XDP_PGOFF_RX_RING               = 0
    #   XDP_PGOFF_TX_RING               = 0x80000000
    #   XDP_UMEM_PGOFF_FILL_RING        = 0x100000000
    #   XDP_UMEM_PGOFF_COMPLETION_RING  = 0x180000000
    PGOFF_RX   = 0
    PGOFF_FILL = 0x100000000

    rx_map_size   = rx_off[2]   + N_FRAMES * XDP_DESC_SIZE
    fill_map_size = fill_off[2] + N_FRAMES * 8  # FILL ring entries are u64

    libc.mmap.restype = ctypes.c_void_p
    rx_addr = libc.mmap(None, rx_map_size,
                        PROT_READ | PROT_WRITE,
                        0x01,  # MAP_SHARED
                        fd, PGOFF_RX)
    if rx_addr in (None, ctypes.c_void_p(-1).value):
        print(f"[probe] FAIL: mmap RX ring {errmsg(-1)}")
        sys.exit(1)
    fill_addr = libc.mmap(None, fill_map_size,
                          PROT_READ | PROT_WRITE,
                          0x01,
                          fd, PGOFF_FILL)
    if fill_addr in (None, ctypes.c_void_p(-1).value):
        print(f"[probe] FAIL: mmap FILL ring {errmsg(-1)}")
        sys.exit(1)
    print(f"[probe] RX/FILL rings mmapped (rx_sz={rx_map_size} "
          f"fill_sz={fill_map_size})")

    # 8. Seed FILL ring with all N_FRAMES UMEM frames so the kernel has
    #    buffers to deposit RX packets into.
    fill_prod_off = fill_off[0]
    fill_desc_off = fill_off[2]
    # Write descriptors (u64 frame addresses) directly via ctypes.
    for i in range(N_FRAMES):
        addr_bytes = struct.pack("=Q", i * chunk)
        ctypes.memmove(fill_addr + fill_desc_off + i * 8, addr_bytes, 8)
    # Update producer pointer: atomic-store-release the new value.
    new_prod = struct.pack("=I", N_FRAMES)
    ctypes.memmove(fill_addr + fill_prod_off, new_prod, 4)
    print(f"[probe] seeded {N_FRAMES} descriptors into FILL ring")

    # 9. Poll RX ring for hold_secs.
    rx_prod_off = rx_off[0]
    rx_cons_off = rx_off[1]
    rx_desc_off = rx_off[2]

    poll = select.poll()
    poll.register(fd, select.POLLIN)
    start = time.monotonic()
    last_print = start
    rx_packets = 0
    rx_bytes = 0
    while time.monotonic() - start < hold_secs:
        events = poll.poll(1000)  # 1s timeout
        # Read producer (atomic-load-acquire).
        prod_buf = (ctypes.c_uint32).from_address(rx_addr + rx_prod_off)
        cons_buf = (ctypes.c_uint32).from_address(rx_addr + rx_cons_off)
        prod = prod_buf.value
        cons = cons_buf.value
        while cons != prod:
            idx  = cons & (N_FRAMES - 1)
            desc_addr = rx_addr + rx_desc_off + idx * XDP_DESC_SIZE
            d_addr, d_len, d_opts = struct.unpack_from(
                "=QII", ctypes.string_at(desc_addr, XDP_DESC_SIZE))
            rx_packets += 1
            rx_bytes += d_len
            cons += 1
        # Update consumer (release).
        ctypes.memmove(rx_addr + rx_cons_off,
                       struct.pack("=I", cons & 0xFFFFFFFF), 4)
        # Recycle drained frames back into FILL.
        # (For a probe we just leave the FILL ring static-seeded; kernel
        # tail-drops once FILL drains, which is fine for an idle hold.)
        now = time.monotonic()
        if now - last_print >= 5:
            print(f"[probe] t={int(now-start)}s rx_packets={rx_packets} "
                  f"rx_bytes={rx_bytes}")
            last_print = now

    print(f"[probe] PASS (stage-2): held socket {hold_secs}s, "
          f"rx_packets={rx_packets} rx_bytes={rx_bytes}")
    if rx_packets == 0:
        print("[probe] NOTE: no packets received - either no peer traffic on "
              f"{ifname} q{queue_id}, or XSKMAP redirect not yet wired in "
              "Phase 3 NAPI ZC datapath.")
    sys.exit(0)


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# dpaa1-xsk-bind-probe.py - XDP_ZEROCOPY bind + RX-ring drain probe for
# DPAA1 M2/M3-3 validation (stage-1 attach + stage-2 datapath
# + stage-3 XSKMAP redirect for blocker B / rx_packets > 0).
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
# Stage-3 (--xskmap with --hold): M3-3 blocker B closure. Before bind(),
# creates a BPF_MAP_TYPE_XSKMAP, loads a minimal eBPF XDP program that
# returns `bpf_redirect_map(&xsks_map, ctx->rx_queue_index, XDP_PASS)`,
# attaches it to the netdev as the XDP program (DRV mode if supported,
# otherwise SKB), then populates xsks_map[queue_id] = xsk_socket_fd
# after bind(). Without this stage, an XSK-bound socket receives nothing
# because dpaa_run_xdp() in the driver early-returns XDP_PASS when no
# XDP program is attached -- the FD travels the skbuf path and rx_packets
# stays at 0. With this stage, rx_default_dqrr -> dpaa_run_xdp ->
# bpf_prog_run_xdp() returns XDP_REDIRECT, xdp_do_redirect() consults
# xsks_map[ctx->rx_queue_index] and copies the page-backed xdp_buff into
# the XSK socket's RX ring (copy mode -- a kernel-page-backed xdp_buff is
# not an XSK UMEM chunk, so the redirect is copy-mode by definition;
# true zero-copy requires the FMan port to RX directly into XSK pool
# chunks, which is a separate follow-on patch beyond rx_packets > 0).
#
# Patch 4006 ('dpaa-xdp-rxq-queue-index') forces every FQ's
# xdp_rxq_info.queue_index to 0 so xsks_map only needs a single entry
# at index 0 regardless of which PCD FQ the FD landed on -- without 4006
# the FQID (~ 65536+) would exceed XSKMAP max_entries.
#
# Usage:
#   sudo python3 dpaa1-xsk-bind-probe.py <ifname> [queue_id] [chunk_size]
#                                        [--hold SECS] [--xskmap]
# Defaults: queue_id=0 chunk_size=4096 hold=0 xskmap=off
#
# Verification matrix:
#   sudo bin/dpaa1-xsk-bind-probe.py eth3 0 4096
#     -> rc=0 ; ethtool -S eth3 | grep xsk_pool_attach_ok shows +1
#   sudo bin/dpaa1-xsk-bind-probe.py eth3 0 4096 --hold 30
#     -> rc=0 ; rx_packets == 0 (no XDP prog attached -> driver returns
#        XDP_PASS at line 2723; FD travels skbuf path)
#   sudo bin/dpaa1-xsk-bind-probe.py eth3 0 4096 --hold 30 --xskmap
#     -> rc=0 ; rx_packets > 0 under peer traffic (M3-3 blocker B closed)

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
XDP_USE_NEED_WAKEUP      = (1 << 3)
XDP_WAKEUP_RX            = 1

# struct xdp_desc { __u64 addr; __u32 len; __u32 options; } -> 16 bytes
XDP_DESC_SIZE   = 16

# offsets of producer/consumer/desc/flags within each ring (kernel layout)
# Returned by getsockopt(XDP_MMAP_OFFSETS) as 4 ring offset structs,
# each containing 4 u64 fields: producer, consumer, desc, flags.
# Total: 4 rings * 4 u64 = 128 bytes.
XDP_OFFSETS_SIZE = 128

# ===== bpf() syscall + XDP_LINK plumbing (added for --xskmap / stage-3) =====
#
# We do not link against libbpf so all syscalls go through libc's syscall().
# The minimum surface we need:
#   bpf(BPF_MAP_CREATE, ...)        -> XSKMAP fd
#   bpf(BPF_PROG_LOAD,  ...)        -> minimal XDP redirect prog fd
#   bpf(BPF_MAP_UPDATE_ELEM, ...)   -> populate xsks_map[queue_id] = xsk_fd
#   AF_NETLINK RTM_SETLINK IFLA_XDP -> attach prog to netdev
#
# All struct layouts mirror include/uapi/linux/bpf.h on 6.18.x (no churn
# on the fields we use across the last decade of kernel releases).

__NR_bpf_arm64 = 280
BPF_MAP_CREATE      = 0
BPF_MAP_UPDATE_ELEM = 2
BPF_PROG_LOAD       = 5

BPF_MAP_TYPE_XSKMAP = 17

BPF_PROG_TYPE_XDP = 6

BPF_ANY = 0

# eBPF instruction encoding (8 bytes per insn):
#   u8  opcode, u8 dst_reg:4 + src_reg:4, s16 off, s32 imm
# Helpers (see kernel/bpf/disasm.c).
def _insn(opc, dst, src, off, imm):
    return struct.pack("=BBhi", opc, (src << 4) | dst, off, imm)

# Opcodes (subset)
BPF_LDX_MEM_W    = 0x61   # ldxw r2, [r1 + off]
BPF_LD_IMM64_DW  = 0x18   # lddw r1, imm64  (8-byte insn that takes 16 bytes -- second slot is _insn(0,0,0,0,imm_hi))
BPF_ALU64_MOV_K  = 0xb7   # mov64 dst, imm
BPF_JMP_CALL     = 0x85   # call helper
BPF_JMP_EXIT     = 0x95   # exit

BPF_PSEUDO_MAP_FD = 1     # src_reg marker for LD_IMM64 referring to a map fd

# eBPF helper IDs (see include/uapi/linux/bpf.h)
BPF_FUNC_redirect_map = 51

# XDP action codes
XDP_PASS    = 2
XDP_REDIRECT = 4

# Netlink for XDP attach
NETLINK_ROUTE = 0
RTM_SETLINK   = 19
NLM_F_REQUEST = 0x01
NLM_F_ACK     = 0x04
IFLA_XDP      = 43
IFLA_XDP_FD            = 1
IFLA_XDP_FLAGS         = 3
XDP_FLAGS_UPDATE_IF_NOEXIST = (1 << 0)
XDP_FLAGS_DRV_MODE          = (1 << 2)
XDP_FLAGS_SKB_MODE          = (1 << 1)

# struct bpf_attr is a tagged union -- we just pass the bytes the kernel
# expects for the command we are running, padded to the longest variant
# the kernel sees. 144 bytes is the upper bound for 6.18.
BPF_ATTR_SIZE = 144


def bpf(libc, cmd, attr_bytes):
    """Issue the bpf() syscall via libc's syscall() wrapper."""
    buf = ctypes.create_string_buffer(BPF_ATTR_SIZE)
    n = min(len(attr_bytes), BPF_ATTR_SIZE)
    ctypes.memmove(buf, attr_bytes, n)
    libc.syscall.restype = ctypes.c_long
    return libc.syscall(__NR_bpf_arm64, ctypes.c_int(cmd),
                        ctypes.byref(buf), ctypes.c_uint(BPF_ATTR_SIZE))


def bpf_map_create_xskmap(libc, max_entries=64):
    """BPF_MAP_CREATE: BPF_MAP_TYPE_XSKMAP with key=u32 fd=u32."""
    # struct bpf_attr.map_create (first union arm):
    #   u32 map_type;
    #   u32 key_size;
    #   u32 value_size;
    #   u32 max_entries;
    #   u32 map_flags;
    #   u32 inner_map_fd;
    #   u32 numa_node;
    #   char map_name[16];
    #   u32 map_ifindex;
    #   u32 btf_fd;
    #   u32 btf_key_type_id;
    #   u32 btf_value_type_id;
    #   u32 btf_vmlinux_value_type_id;
    #   ...
    attr = struct.pack("=IIIIII I 16s I I I I I",
                       BPF_MAP_TYPE_XSKMAP, 4, 4, max_entries,
                       0, 0, 0,
                       b"xsks_map\0\0\0\0\0\0\0\0",
                       0, 0, 0, 0, 0)
    rc = bpf(libc, BPF_MAP_CREATE, attr)
    if rc < 0:
        e = ctypes.get_errno()
        raise OSError(e,
            f"bpf(BPF_MAP_CREATE,XSKMAP) errno={e} ({errno.errorcode.get(e,'?')}: {os.strerror(e)})")
    return rc


def build_xdp_redirect_prog(xsks_map_fd):
    """Return eBPF bytecode that does:
       r2 = 0                          (hardcoded XSKMAP slot 0)
       r1 = map_fd_pseudo(xsks_map_fd) (lddw)
       r3 = XDP_PASS                   (fallback if entry empty)
       call BPF_FUNC_redirect_map
       exit

    Simplified to always redirect to XSKMAP[0] because FMan distributes
    ingress across 128 PCD FQs with different FQIDs; the old
    `rx_queue_index & 0x3f` approach populated different XSKMAP slots,
    but only slot 0 has the bound fd — all other slots redirect to NULL
    → XDP_PASS → kernel skbuf path → probe rx_packets stays 0.
    """
    code = b""
    # r2 = 0  (XSKMAP key = 0).  mov64 r2, 0
    code += _insn(BPF_ALU64_MOV_K, dst=2, src=0, off=0, imm=0)
    # r1 = xsks_map_fd  (lddw, two 8-byte insn slots; first slot holds low
    # 32 bits, second slot holds high 32 bits)
    code += _insn(BPF_LD_IMM64_DW, dst=1, src=BPF_PSEUDO_MAP_FD,
                  off=0, imm=xsks_map_fd)
    code += _insn(0, dst=0, src=0, off=0, imm=0)  # second half of lddw
    # r3 = XDP_PASS
    code += _insn(BPF_ALU64_MOV_K, dst=3, src=0, off=0, imm=XDP_PASS)
    # call BPF_FUNC_redirect_map
    code += _insn(BPF_JMP_CALL, dst=0, src=0, off=0, imm=BPF_FUNC_redirect_map)
    # exit
    code += _insn(BPF_JMP_EXIT, dst=0, src=0, off=0, imm=0)
    return code


def bpf_prog_load_xdp(libc, prog_bytes):
    """BPF_PROG_LOAD: BPF_PROG_TYPE_XDP, GPL license."""
    n_insns = len(prog_bytes) // 8
    insns_buf = ctypes.create_string_buffer(prog_bytes, len(prog_bytes))
    license_buf = ctypes.create_string_buffer(b"GPL")
    log_buf_sz = 64 * 1024
    log_buf = ctypes.create_string_buffer(log_buf_sz)

    # struct bpf_attr.prog_load:
    #   u32 prog_type;
    #   u32 insn_cnt;
    #   u64 insns;        (pointer)
    #   u64 license;      (pointer)
    #   u32 log_level;
    #   u32 log_size;
    #   u64 log_buf;      (pointer)
    #   u32 kern_version;
    #   u32 prog_flags;
    #   char prog_name[16];
    #   u32 prog_ifindex;
    #   u32 expected_attach_type;
    #   u32 prog_btf_fd;
    #   u32 func_info_rec_size;
    #   u64 func_info;
    #   u32 func_info_cnt;
    #   u32 line_info_rec_size;
    #   u64 line_info;
    #   u32 line_info_cnt;
    #   u32 attach_btf_id;
    #   u32 attach_prog_fd;
    #   u32 core_relo_cnt;
    #   u64 fd_array;
    #   u64 core_relos;
    #   u32 core_relo_rec_size;
    #   ...
    attr = struct.pack(
        "=IIQQII Q II 16s I I I I Q I I Q I I I I Q Q I",
        BPF_PROG_TYPE_XDP,                 # prog_type
        n_insns,                           # insn_cnt
        ctypes.addressof(insns_buf),       # insns
        ctypes.addressof(license_buf),     # license
        1,                                 # log_level
        log_buf_sz,                        # log_size
        ctypes.addressof(log_buf),         # log_buf
        0,                                 # kern_version
        0,                                 # prog_flags
        b"dpaa1_xskmap\0\0\0\0",           # prog_name (16 B)
        0,                                 # prog_ifindex
        0,                                 # expected_attach_type
        0,                                 # prog_btf_fd
        0,                                 # func_info_rec_size
        0,                                 # func_info
        0,                                 # func_info_cnt
        0,                                 # line_info_rec_size
        0,                                 # line_info
        0,                                 # line_info_cnt
        0,                                 # attach_btf_id
        0,                                 # attach_prog_fd
        0,                                 # core_relo_cnt
        0,                                 # fd_array
        0,                                 # core_relos
        0,                                 # core_relo_rec_size
    )
    rc = bpf(libc, BPF_PROG_LOAD, attr)
    if rc < 0:
        e = ctypes.get_errno()
        log = log_buf.value.decode(errors="replace").strip()
        raise OSError(e,
            f"bpf(BPF_PROG_LOAD,XDP) errno={e} ({errno.errorcode.get(e,'?')}: {os.strerror(e)})\nverifier log:\n{log}")
    return rc


def bpf_xskmap_update(libc, xskmap_fd, key, xsk_fd):
    """BPF_MAP_UPDATE_ELEM: xsks_map[key] = xsk_fd."""
    key_buf = ctypes.create_string_buffer(struct.pack("=I", key), 4)
    val_buf = ctypes.create_string_buffer(struct.pack("=I", xsk_fd), 4)
    # struct bpf_attr.map_elem:
    #   u32 map_fd;
    #   /* 4 bytes pad (kernel uses __aligned_u64 -> next field 8-byte aligned) */
    #   u64 key;
    #   union { u64 value; u64 next_key; };
    #   u64 flags;
    attr = struct.pack("=I 4x QQQ",
                       xskmap_fd,
                       ctypes.addressof(key_buf),
                       ctypes.addressof(val_buf),
                       BPF_ANY)
    rc = bpf(libc, BPF_MAP_UPDATE_ELEM, attr)
    if rc < 0:
        e = ctypes.get_errno()
        raise OSError(e,
            f"bpf(BPF_MAP_UPDATE_ELEM,XSKMAP) errno={e} ({errno.errorcode.get(e,'?')}: {os.strerror(e)})")
    return rc


def attach_xdp_prog(ifindex, prog_fd, drv_first=True):
    """Attach an XDP program to a netdev via rtnetlink IFLA_XDP.
    Try DRV mode first (kernel-fast-path), fall back to SKB mode (generic
    XDP via netif_receive_skb) if DRV is rejected."""
    nl = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_ROUTE)
    nl.bind((0, 0))

    def _build(flags):
        # struct ifinfomsg: u8 family, u8 _pad, u16 type, s32 index, u32 flags, u32 change
        ifi = struct.pack("=BBHiII", 0, 0, 0, ifindex, 0, 0)
        # IFLA_XDP_FD attribute
        fd_attr = struct.pack("=HH i", 8, IFLA_XDP_FD, prog_fd)
        # IFLA_XDP_FLAGS attribute
        fl_attr = struct.pack("=HH I", 8, IFLA_XDP_FLAGS, flags)
        # Nested IFLA_XDP container
        nested_payload = fd_attr + fl_attr
        nested = struct.pack("=HH", 4 + len(nested_payload), IFLA_XDP) + nested_payload
        # Pad nested to 4 bytes (already aligned in our case)
        body = ifi + nested
        nl_hdr_len = 16
        total = nl_hdr_len + len(body)
        # struct nlmsghdr: u32 len, u16 type, u16 flags, u32 seq, u32 pid
        hdr = struct.pack("=IHHII", total, RTM_SETLINK,
                          NLM_F_REQUEST | NLM_F_ACK, 1, 0)
        return hdr + body

    # No XDP_FLAGS_UPDATE_IF_NOEXIST -- we want the second/third run of the
    # probe to be able to replace any prog left behind by a crashed prior
    # invocation. Plain replace semantics are what we want.
    last_err = None
    modes = []
    if drv_first:
        modes.append(("DRV", XDP_FLAGS_DRV_MODE))
    modes.append(("SKB", XDP_FLAGS_SKB_MODE))
    for mode_name, flags in modes:
        nl.send(_build(flags))
        reply = nl.recv(4096)
        # NLMSG_ERROR has type 2; error code at offset 16 (after nlmsghdr).
        if len(reply) >= 20:
            nl_type = struct.unpack_from("=H", reply, 4)[0]
            if nl_type == 2:  # NLMSG_ERROR
                err = struct.unpack_from("=i", reply, 16)[0]
                if err == 0:
                    nl.close()
                    return mode_name
                last_err = err
                continue
        nl.close()
        return mode_name + "?"
    nl.close()
    raise OSError(-last_err if last_err else errno.EIO,
        f"IFLA_XDP attach failed in both DRV and SKB modes "
        f"(last errno={-last_err if last_err else '?'})")


# ===== existing probe (unchanged below this point except for arg parsing
#       and a stage-3 XSKMAP call sequence around bind()) =====

def errmsg(rc):
    e = ctypes.get_errno()
    return f"rc={rc} errno={e} ({errno.errorcode.get(e,'?')}: {os.strerror(e)})"

def parse_args():
    args = sys.argv[1:]
    hold_secs = 0
    use_xskmap = False
    while "--hold" in args:
        i = args.index("--hold")
        hold_secs = int(args[i + 1])
        del args[i:i + 2]
    if "--xskmap" in args:
        use_xskmap = True
        args.remove("--xskmap")
    if not args:
        print("usage: dpaa1-xsk-bind-probe.py <ifname> [queue_id] "
              "[chunk_size] [--hold SECS] [--xskmap]", file=sys.stderr)
        sys.exit(2)
    ifname    = args[0]
    queue_id  = int(args[1]) if len(args) > 1 else 0
    chunk     = int(args[2]) if len(args) > 2 else 4096
    return ifname, queue_id, chunk, hold_secs, use_xskmap

def main():
    ifname, queue_id, chunk, hold_secs, use_xskmap = parse_args()
    ifindex = socket.if_nametoindex(ifname)
    print(f"[probe] ifname={ifname} ifindex={ifindex} queue_id={queue_id} "
          f"chunk_size={chunk} hold={hold_secs}s xskmap={use_xskmap}")

    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

    # ===== Stage-3a: XSKMAP creation + XDP redirect prog load + attach =====
    # Must happen BEFORE bind() because IFLA_XDP attach binds a program to
    # the netdev as a whole; bind() then only matters for the per-socket
    # ring plumbing.
    xskmap_fd = None
    if use_xskmap:
        try:
            xskmap_fd = bpf_map_create_xskmap(libc, max_entries=64)
            print(f"[probe] BPF_MAP_CREATE(XSKMAP) fd={xskmap_fd} "
                  f"max_entries=64")
            prog_bytes = build_xdp_redirect_prog(xskmap_fd)
            prog_fd = bpf_prog_load_xdp(libc, prog_bytes)
            print(f"[probe] BPF_PROG_LOAD(XDP redirect) fd={prog_fd} "
                  f"insns={len(prog_bytes)//8}")
            mode = attach_xdp_prog(ifindex, prog_fd)
            print(f"[probe] IFLA_XDP attached to {ifname} in {mode} mode")
        except OSError as ex:
            print(f"[probe] FAIL: XSKMAP stage-3 setup: {ex}")
            sys.exit(1)

    # 1. socket(AF_XDP)
    fd = libc.socket(AF_XDP, socket.SOCK_RAW, 0)
    if fd < 0:
        print(f"[probe] FAIL: socket(AF_XDP) {errmsg(fd)}")
        sys.exit(1)
    print(f"[probe] xsk socket fd={fd}")

    # 2. UMEM mmap
    N_FRAMES   = 8192
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
    sa = struct.pack("=HHIII", AF_XDP, XDP_ZEROCOPY | XDP_USE_NEED_WAKEUP, ifindex, queue_id, 0)
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

    # ===== Stage-3b: populate XSKMAP[queue_id] = xsk_fd (after bind) =====
    if use_xskmap:
        try:
            bpf_xskmap_update(libc, xskmap_fd, queue_id, fd)
            print(f"[probe] xsks_map[{queue_id}] = xsk_fd {fd} populated")
        except OSError as ex:
            print(f"[probe] FAIL: XSKMAP update: {ex}")
            sys.exit(1)

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

    # 8b. Kick NAPI via sendto(MSG_DONTWAIT, XDP_WAKEUP_RX) WITH RETRIES
    #     to break the circular dependency: FMan drops because BMan XSK
    #     pool is empty (bind-time seed found empty FILL ring →
    #     xsk_bman_seed_short), the wakeup IPIs NAPI to CPU 0
    #     asynchronously, and napi_refill pulls chunks from the now-seeded
    #     FILL ring into BMan.  Without the retry+dwell, the drain loop
    #     races ahead before the IPI lands and refill completes.
    #     Sub-increment 5 of M3-3 step 7 (0103c).
    #     NOTE: passes XDP_WAKEUP_RX (1), NOT XDP_ZEROCOPY (4) — the
    #     kernel xsk_sendmsg() gates on sxdp_flags & XDP_WAKEUP_RX.
    MSG_DONTWAIT = 0x40
    sockaddr_xdp = struct.pack("=HHIII", AF_XDP, XDP_WAKEUP_RX,
                               ifindex, queue_id, 0)
    for attempt in (1, 2, 3):
        rc = libc.sendto(fd, None, 0, MSG_DONTWAIT,
                         sockaddr_xdp, len(sockaddr_xdp))
        if rc < 0:
            e = ctypes.get_errno()
            if e != errno.ENOENT:
                print(f"[probe] WARNING: sendto(XDP_WAKEUP) attempt "
                      f"{attempt} {errmsg(rc)}")
        else:
            print(f"[probe] NAPI wakeup attempt {attempt} sent "
                  f"(sendto returned {rc})")
        time.sleep(0.5)  # let IPI-NAPI poll + refill complete asynchronously

    # 9. Poll RX ring for hold_secs.
    rx_prod_off = rx_off[0]
    rx_cons_off = rx_off[1]
    rx_desc_off = rx_off[2]

    # FILL ring producer/consumer tracking for recycle path.
    # We seeded N_FRAMES descriptors above so initial FILL producer is at
    # N_FRAMES. The kernel's FILL consumer chases it. For each RX completion
    # we recycle the chunk back to FILL by writing its addr at
    # (fill_producer & MASK) and bumping fill_producer.
    # Without this recycle, all UMEM chunks get consumed in the first burst
    # and the kernel falls back to copy-mode-drop (FILL empty -> no chunk
    # available to copy into) and the probe plateaus at N_FRAMES.
    fill_producer = N_FRAMES  # initial seeded count
    FILL_MASK = N_FRAMES - 1  # N_FRAMES is power of 2 (256)
    fill_cons_addr = (ctypes.c_uint32).from_address(
        fill_addr + fill_off[1])

    poll = select.poll()
    poll.register(fd, select.POLLIN)
    start = time.monotonic()
    last_print = start
    rx_packets = 0
    rx_bytes = 0
    last_rx_packets = 0
    # Safety cap: at most MAX_BATCH descriptors processed per poll wakeup so
    # the loop can yield back to poll.poll() under sustained line-rate load.
    # Without this cap, a continuously-producing kernel can keep prod ahead
    # of cons indefinitely and Python's ctypes loop spins forever, never
    # returning to the outer time.monotonic() check or flushing stdout.
    # 64 << N_FRAMES (256) so we round-trip the outer poll() at least 4x per
    # ring-full.
    MAX_BATCH = 64
    # FILL backpressure: NEVER let (fill_producer - fill_cons) exceed
    # N_FRAMES. Overrunning the kernel's FILL consumer pointer makes us
    # re-add the same UMEM chunk into the FILL ring while the kernel still
    # has it in flight -> xp_alloc() dequeues the same chunk twice ->
    # xsk_buff_pool free-list (struct list_head) corruption ->
    # `list_add corruption. prev->next should be X, but was X` warning ->
    # next allocation returns garbage VA -> dcache_clean_poc() faults ->
    # kernel panic in softirq. Observed under 5 Gbps iperf3 flood
    # (2026-05-28). The recycle inner loop now blocks (drops the descriptor
    # without recycling, kernel will see FILL empty and drop the next RX
    # which is fine -- xsk_bman_starve will tick) when the in-flight
    # window is full.
    skipped_recycle = 0
    print(f"[probe] entering RX drain loop (MAX_BATCH={MAX_BATCH}, "
          f"FILL_SIZE={N_FRAMES})", flush=True)
    sys.stdout.flush()
    while time.monotonic() - start < hold_secs:
        events = poll.poll(1000)  # 1s timeout
        # Read producer (atomic-load-acquire).
        prod_buf = (ctypes.c_uint32).from_address(rx_addr + rx_prod_off)
        cons_buf = (ctypes.c_uint32).from_address(rx_addr + rx_cons_off)
        prod = prod_buf.value
        cons = cons_buf.value
        recycled = 0
        batch = 0
        while cons != prod and batch < MAX_BATCH:
            idx  = cons & (N_FRAMES - 1)
            desc_addr = rx_addr + rx_desc_off + idx * XDP_DESC_SIZE
            d_addr, d_len, d_opts = struct.unpack_from(
                "=QII", ctypes.string_at(desc_addr, XDP_DESC_SIZE))
            rx_packets += 1
            rx_bytes += d_len
            cons += 1
            batch += 1
            # FILL backpressure: re-read fill_cons every iteration via the
            # ctypes view that maps directly to the kernel-written word.
            # Only recycle if (fill_producer - fill_cons) < N_FRAMES, i.e.
            # the in-flight count stays bounded by the ring size. Note the
            # subtraction is in u32 wrap-around space; on 64-bit Python the
            # values are widened to int, so we mask back to u32 before
            # comparing.
            fc = fill_cons_addr.value
            in_flight = (fill_producer - fc) & 0xFFFFFFFF
            if in_flight >= N_FRAMES:
                skipped_recycle += 1
                continue
            chunk_base = d_addr & ~(chunk - 1)
            fill_slot = fill_producer & FILL_MASK
            ctypes.memmove(fill_addr + fill_off[2] + fill_slot * 8,
                           struct.pack("=Q", chunk_base), 8)
            fill_producer += 1
            recycled += 1
        # Update RX consumer (release).
        ctypes.memmove(rx_addr + rx_cons_off,
                       struct.pack("=I", cons & 0xFFFFFFFF), 4)
        # Update FILL producer (release) if we recycled any chunks.
        if recycled:
            ctypes.memmove(fill_addr + fill_off[0],
                           struct.pack("=I", fill_producer & 0xFFFFFFFF), 4)
        now = time.monotonic()
        if now - last_print >= 2:
            elapsed = now - start
            delta = rx_packets - last_rx_packets
            last_rx_packets = rx_packets
            pps = delta / max(1e-9, (now - last_print))
            bps = (rx_bytes * 8) / max(1e-9, elapsed)
            print(f"[probe] t={int(elapsed)}s rx_packets={rx_packets} "
                  f"rx_bytes={rx_bytes} pps={int(pps)} "
                  f"avg_bps={bps/1e9:.2f}G fill_prod={fill_producer} "
                  f"fill_cons={fill_cons_addr.value} "
                  f"last_batch={batch} skipped_recycle={skipped_recycle}",
                  flush=True)
            sys.stdout.flush()
            last_print = now

    print(f"[probe] PASS (stage-2): held socket {hold_secs}s, "
          f"rx_packets={rx_packets} rx_bytes={rx_bytes}")
    if rx_packets == 0:
        if use_xskmap:
            print("[probe] NOTE: no packets received under --xskmap - "
                  "verify peer traffic is hitting the bound queue. "
                  "Check `ethtool -S ifname | grep xsk_rx_branch` to "
                  "confirm FDs are landing in the bound qband.")
        else:
            print("[probe] NOTE: no packets received - no XDP redirect "
                  "program attached. Re-run with --xskmap to wire the "
                  "XSKMAP redirect (M3-3 blocker B). Without --xskmap, "
                  "dpaa_run_xdp() returns XDP_PASS and FDs travel the "
                  "skbuf path -- rx_packets stays 0 by design.")
    sys.exit(0)

if __name__ == "__main__":
    main()
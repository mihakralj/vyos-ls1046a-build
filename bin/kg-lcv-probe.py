#!/usr/bin/env python3
"""kg-lcv-probe — Phase-3 (T-M6-1 IPv6 dispatch) parser-LCV + KeyGen match-vector
experiment tool for the LS1046A FMan (210.10.1 microcode).

WHAT THIS DOES
--------------
The FMan selects a KeyGen scheme per frame by: "first enabled scheme where
SI=1 AND (QLCV & kgse_mv) == kgse_mv", where QLCV = plan_mask & LCV, and the
per-frame LCV is the OR of each parsed header slot's pmda[slot].lcv mask.

IPv4 parses to HXS slot 5, IPv6 to slot 6 (silicon-fixed). Mainline programs
EVERY pmda[i].lcv = 0xffffffff, so the LCV cannot distinguish protocols. To
route IPv6 to its own scheme we must (1) split pmda[5].lcv / pmda[6].lcv into
distinct single bits, and (2) set each scheme's kgse_mv to its protocol bit,
with NIA_KG_DIRECT disabled so the SI walk runs.

This tool reads/writes the parser HWP pmda[].lcv registers and reads the KeyGen
per-scheme registers (via the fmkg_ar indirect protocol; scheme WRITE is
deliberately NOT implemented here — scheme mutation goes through the existing
fe_kg_ekfc debugfs node which does the whole-struct read/modify/write safely).

SAFETY
------
- Default action is READ. Writes require --write and print a readback compare.
- --restore rewrites pmda[5/6].lcv back to 0xffffffff (mainline default).
- Operate on a SACRIFICIAL 1G RX port only (eth1=0x0d HWP 0x1a8d800,
  eth2=0x09 HWP 0x1a89800). NEVER eth0 (mgmt) or eth3/eth4 (working ASK).
- Cold-boot before the experiment; one variable at a time; readback every write.

REGISTER MAP (verified live 2026-08-19)
---------------------------------------
FMan CCSR base            = 0x01a00000
RX port HWP regs          = 0x01a00000 + port_ccsr_off + 0x800
  eth2 (port@89000, 0x09) = 0x01a89800
  eth1 (port@8d000, 0x0d) = 0x01a8d800
pmda[i]                   = {ssa @ +0, lcv @ +4}, 8-byte stride
  pmda[5].lcv (IPv4)      = HWP + 0x2c
  pmda[6].lcv (IPv6)      = HWP + 0x34
KeyGen block              = 0x01ac1000 (AR @ +0x1FC, shadow @ +0x100)
"""
import argparse
import json
import mmap
import os
import struct
import sys
import time

FMAN_BASE = 0x01A00000
KG_BASE   = 0x01AC1000
KG_SIZE   = 0x1000
AR_OFF    = 0x1FC
SHADOW    = 0x100
GO, READ, ERR = 0x80000000, 0x40000000, 0x20000000
NUM_SHIFT = 16

# eth<n> -> RX-port HWP physical base (sacrificial 1G ports only)
HWP_BASE = {
    "eth1": 0x01A8D800,   # port@8d000, hw_port 0x0d
    "eth2": 0x01A89800,   # port@89000, hw_port 0x09
}
LCV_IPV4 = 5 * 8 + 4      # pmda[5].lcv
LCV_IPV6 = 6 * 8 + 4      # pmda[6].lcv

SCHEME_FIELDS = [
    ("kgse_mode", 0x00), ("kgse_ekfc", 0x04), ("kgse_spc", 0x40),
    ("kgse_dv0", 0x44), ("kgse_dv1", 0x48), ("kgse_ccbs", 0x4C),
    ("kgse_mv", 0x50),
]


def _map(page_base):
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    try:
        return mmap.mmap(fd, 0x1000, mmap.MAP_SHARED,
                         mmap.PROT_READ | mmap.PROT_WRITE, offset=page_base)
    finally:
        os.close(fd)


def _rd(mm, off):
    return struct.unpack_from(">I", mm, off)[0]


def _wr(mm, off, val):
    mm[off:off + 4] = struct.pack(">I", val)


# ---------- parser HWP LCV ----------

def hwp_dump(dev):
    base = HWP_BASE[dev]
    mm = _map(base & ~0xFFF)
    o = base & 0xFFF
    print(f"{dev} HWP base=0x{base:08x}")
    for i in range(16):
        ssa = _rd(mm, o + i * 8)
        lcv = _rd(mm, o + i * 8 + 4)
        tag = {5: " IPv4", 6: " IPv6", 0xA: " TCP", 0xB: " UDP"}.get(i, "")
        print(f"  pmda[{i:2d}] ssa=0x{ssa:08x} lcv=0x{lcv:08x}{tag}")
    mm.close()


def hwp_set_lcv(dev, v4_bit, v6_bit):
    base = HWP_BASE[dev]
    mm = _map(base & ~0xFFF)
    o = base & 0xFFF
    old4, old6 = _rd(mm, o + LCV_IPV4), _rd(mm, o + LCV_IPV6)
    _wr(mm, o + LCV_IPV4, v4_bit)
    _wr(mm, o + LCV_IPV6, v6_bit)
    new4, new6 = _rd(mm, o + LCV_IPV4), _rd(mm, o + LCV_IPV6)
    mm.close()
    ok = (new4 == v4_bit and new6 == v6_bit)
    print(f"{dev} pmda[5].lcv 0x{old4:08x}->0x{new4:08x} (want 0x{v4_bit:08x})")
    print(f"{dev} pmda[6].lcv 0x{old6:08x}->0x{new6:08x} (want 0x{v6_bit:08x})")
    print("READBACK OK" if ok else "READBACK MISMATCH")
    return ok


# ---------- KeyGen scheme read (indirect AR) ----------

def kg_read_scheme(sid, timeout_s=0.5):
    mm = _map(KG_BASE)
    try:
        _wr(mm, AR_OFF, GO | READ | (sid << NUM_SHIFT))
        dl = time.monotonic() + timeout_s
        while True:
            cur = _rd(mm, AR_OFF)
            if not (cur & GO):
                break
            if time.monotonic() > dl:
                raise TimeoutError(f"scheme {sid}: GO stuck 0x{cur:08x}")
        if cur & ERR:
            raise RuntimeError(f"scheme {sid}: ERR 0x{cur:08x}")
        return {n: _rd(mm, SHADOW + off) for n, off in SCHEME_FIELDS}
    finally:
        mm.close()


def kg_dump(schemes):
    for sid in schemes:
        r = kg_read_scheme(sid)
        en = " ENABLED" if r["kgse_mode"] & 0x80000000 else ""
        print(f"scheme {sid:2d}: mode=0x{r['kgse_mode']:08x} "
              f"ekfc=0x{r['kgse_ekfc']:08x} mv=0x{r['kgse_mv']:08x} "
              f"spc=0x{r['kgse_spc']:08x}{en}")


def kg_spc(schemes):
    return {sid: kg_read_scheme(sid)["kgse_spc"] for sid in schemes}


# ---------- full scheme read/write + port SP bind (Phase-3 experiment) ----------
# Whole-struct scheme access mirroring keygen_write_scheme(). 23 u32 words at
# shadow 0x100. AR: scheme write = GO | (sid<<16) | UPD_CNT; port SP =
# GO | READ/WRITE | SEL_PORT(0x02000000) | WSEL_SP(0x00008000) | hw_port_id.
SCHEME_WORDS = 23                     # 0x00..0x58 inclusive
SEL_PORT = 0x02000000
WSEL_SP = 0x00008000
UPD_CNT = 0x00008000                  # FM_KG_KGAR_SCM_WSEL_UPDATE_CNT


def _kg_map():
    return _map(KG_BASE)


def _ar_wait(mm, timeout_s=0.5):
    dl = time.monotonic() + timeout_s
    while True:
        cur = _rd(mm, AR_OFF)
        if not (cur & GO):
            break
        if time.monotonic() > dl:
            raise TimeoutError(f"AR GO stuck 0x{cur:08x}")
    if cur & ERR:
        raise RuntimeError(f"AR ERR 0x{cur:08x}")


def scheme_read_full(sid):
    mm = _kg_map()
    try:
        _wr(mm, AR_OFF, GO | READ | (sid << NUM_SHIFT))
        _ar_wait(mm)
        return [_rd(mm, SHADOW + i * 4) for i in range(SCHEME_WORDS)]
    finally:
        mm.close()


def scheme_write_full(sid, words, update_counter=True):
    assert len(words) == SCHEME_WORDS
    mm = _kg_map()
    try:
        for i, w in enumerate(words):
            _wr(mm, SHADOW + i * 4, w)
        _wr(mm, AR_OFF, GO | (sid << NUM_SHIFT) | (UPD_CNT if update_counter else 0))
        _ar_wait(mm)
    finally:
        mm.close()


def port_sp_read(pid):
    mm = _kg_map()
    try:
        _wr(mm, AR_OFF, GO | READ | SEL_PORT | WSEL_SP | pid)
        _ar_wait(mm)
        return _rd(mm, SHADOW)          # fmkg_pe_sp @ 0x100
    finally:
        mm.close()


def port_sp_write(pid, sp):
    mm = _kg_map()
    try:
        _wr(mm, SHADOW, sp)
        _wr(mm, AR_OFF, GO | SEL_PORT | WSEL_SP | pid)   # WRITE = no READ bit
        _ar_wait(mm)
    finally:
        mm.close()


def _hwp_read_all_lcv(dev):
    base = HWP_BASE[dev]
    mm = _map(base & ~0xFFF)
    o = base & 0xFFF
    lcv = [_rd(mm, o + i * 8 + 4) for i in range(16)]
    mm.close()
    return lcv


def _hwp_write_all_lcv(dev, lcv):
    base = HWP_BASE[dev]
    mm = _map(base & ~0xFFF)
    o = base & 0xFFF
    for i, v in enumerate(lcv):
        _wr(mm, o + i * 8 + 4, v)
    mm.close()


MV_IDX = 0x50 // 4                     # kgse_mv word index (20)
MODE_IDX = 0
SPC_IDX = 0x40 // 4


def exp_snapshot(args):
    """Save the pre-experiment register state to JSON for exact restore."""
    snap = {
        "dev": args.dev, "port_id": args.port_id,
        "v4_scheme": args.v4_scheme, "v6_scheme": args.v6_scheme,
        "lcv": _hwp_read_all_lcv(args.dev),
        "port_sp": port_sp_read(args.port_id),
        "scheme_v4": scheme_read_full(args.v4_scheme),
        "scheme_v6": scheme_read_full(args.v6_scheme),
    }
    json.dump(snap, open(args.file, "w"))
    print(f"snapshot saved to {args.file}: dev={args.dev} port=0x{args.port_id:02x} "
          f"SP=0x{snap['port_sp']:08x} v4_scheme={args.v4_scheme} "
          f"v6_scheme={args.v6_scheme}")


def exp_apply(args):
    """Program the LCV split + v4/v6 kgse_mv + SP bind. Publish-last: bind the
    v6 scheme into the port SP only after LCV and both schemes are set."""
    snap = json.load(open(args.file))
    dev, pid = snap["dev"], snap["port_id"]
    s4, s6 = snap["v4_scheme"], snap["v6_scheme"]
    V4BIT, V6BIT = int(args.v4, 0), int(args.v6, 0)

    if not args.write:
        print(f"DRY RUN: would set {dev} pmda[5].lcv=0x{V4BIT:08x} "
              f"pmda[6].lcv=0x{V6BIT:08x} (all other HXS lcv=0); "
              f"scheme{s4}.kgse_mv=0x{V4BIT:08x}; clone scheme{s4}->scheme{s6} "
              f"kgse_mv=0x{V6BIT:08x}; bind scheme{s6} into port 0x{pid:02x} SP.")
        return

    # 1) v4 scheme: set kgse_mv = V4BIT (all other fields preserved, counter kept)
    w4 = list(snap["scheme_v4"])
    w4[MV_IDX] = V4BIT
    scheme_write_full(s4, w4, update_counter=False)

    # 2) v6 scheme: clone v4 scheme, set kgse_mv = V6BIT, zero its counter
    w6 = list(snap["scheme_v4"])
    w6[MV_IDX] = V6BIT
    w6[SPC_IDX] = 0
    scheme_write_full(s6, w6, update_counter=True)

    # 3) parser LCV: zero every HXS, then set slot5=V4BIT, slot6=V6BIT
    lcv = [0] * 16
    lcv[5] = V4BIT
    lcv[6] = V6BIT
    _hwp_write_all_lcv(dev, lcv)

    # 4) publish last: add the v6 scheme bit to the port's SP
    sp = port_sp_read(pid)
    sp_new = sp | (1 << (31 - s6))
    port_sp_write(pid, sp_new)

    # readback compare
    r4 = scheme_read_full(s4)[MV_IDX]
    r6 = scheme_read_full(s6)
    rlcv = _hwp_read_all_lcv(dev)
    rsp = port_sp_read(pid)
    ok = (r4 == V4BIT and r6[MV_IDX] == V6BIT and (r6[MODE_IDX] & 0x80000000)
          and rlcv[5] == V4BIT and rlcv[6] == V6BIT and rsp == sp_new)
    print(f"applied: scheme{s4}.mv=0x{r4:08x} scheme{s6}.mv=0x{r6[MV_IDX]:08x} "
          f"scheme{s6}.mode=0x{r6[MODE_IDX]:08x} lcv5=0x{rlcv[5]:08x} "
          f"lcv6=0x{rlcv[6]:08x} SP 0x{sp:08x}->0x{rsp:08x}")
    print("READBACK OK" if ok else "READBACK MISMATCH")


def exp_restore(args):
    snap = json.load(open(args.file))
    dev, pid = snap["dev"], snap["port_id"]
    s4, s6 = snap["v4_scheme"], snap["v6_scheme"]
    if not args.write:
        print(f"DRY RUN: would restore {dev} LCV, port 0x{pid:02x} SP=0x"
              f"{snap['port_sp']:08x}, scheme{s4} and scheme{s6} to snapshot.")
        return
    # unbind first (SP), then restore schemes, then restore LCV
    port_sp_write(pid, snap["port_sp"])
    scheme_write_full(s4, snap["scheme_v4"], update_counter=False)
    scheme_write_full(s6, snap["scheme_v6"], update_counter=True)
    _hwp_write_all_lcv(dev, snap["lcv"])
    rsp = port_sp_read(pid)
    rlcv = _hwp_read_all_lcv(dev)
    ok = (rsp == snap["port_sp"] and rlcv == snap["lcv"])
    print(f"restored: SP=0x{rsp:08x} lcv5=0x{rlcv[5]:08x} lcv6=0x{rlcv[6]:08x}")
    print("RESTORE OK" if ok else "RESTORE MISMATCH")


def main():
    ap = argparse.ArgumentParser(description="Phase-3 LCV/kgse_mv probe")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("hwp-dump");  p.add_argument("dev", choices=HWP_BASE)
    p = sub.add_parser("hwp-set")
    p.add_argument("dev", choices=HWP_BASE)
    p.add_argument("--v4", default="0x40000000")
    p.add_argument("--v6", default="0x80000000")
    p.add_argument("--write", action="store_true", help="actually write (else refuse)")
    p = sub.add_parser("hwp-restore"); p.add_argument("dev", choices=HWP_BASE)
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("kg-dump")
    p.add_argument("schemes", nargs="*", type=lambda x: int(x, 0),
                   default=list(range(6)))
    p = sub.add_parser("kg-spc")
    p.add_argument("schemes", nargs="*", type=lambda x: int(x, 0),
                   default=[3, 4, 5])

    # port scheme-partition read
    p = sub.add_parser("port-sp")
    p.add_argument("port_id", type=lambda x: int(x, 0))

    # experiment: snapshot / apply / restore
    for name in ("exp-snapshot", "exp-apply", "exp-restore"):
        q = sub.add_parser(name)
        q.add_argument("--file", default="/tmp/kg-exp.json")
        if name == "exp-snapshot":
            q.add_argument("dev", choices=HWP_BASE)
            q.add_argument("--port-id", type=lambda x: int(x, 0), required=True)
            q.add_argument("--v4-scheme", type=int, required=True)
            q.add_argument("--v6-scheme", type=int, required=True)
        else:
            q.add_argument("--v4", default="0x40000000")
            q.add_argument("--v6", default="0x80000000")
            q.add_argument("--write", action="store_true")

    a = ap.parse_args()
    if a.cmd == "hwp-dump":
        hwp_dump(a.dev)
    elif a.cmd == "hwp-set":
        if not a.write:
            print("refusing to write without --write (dry run). Would set "
                  f"{a.dev} pmda[5].lcv={a.v4} pmda[6].lcv={a.v6}")
            return
        hwp_set_lcv(a.dev, int(a.v4, 0), int(a.v6, 0))
    elif a.cmd == "hwp-restore":
        if not a.write:
            print(f"dry run: would restore {a.dev} pmda[5/6].lcv=0xffffffff")
            return
        hwp_set_lcv(a.dev, 0xFFFFFFFF, 0xFFFFFFFF)
    elif a.cmd == "kg-dump":
        kg_dump(a.schemes)
    elif a.cmd == "kg-spc":
        for sid, spc in kg_spc(a.schemes).items():
            print(f"scheme {sid:2d}: kgse_spc=0x{spc:08x} ({spc})")
    elif a.cmd == "port-sp":
        sp = port_sp_read(a.port_id)
        schemes = [i for i in range(32) if sp & (1 << (31 - i))]
        print(f"port 0x{a.port_id:02x} SP=0x{sp:08x} bound_schemes={schemes}")
    elif a.cmd == "exp-snapshot":
        exp_snapshot(a)
    elif a.cmd == "exp-apply":
        exp_apply(a)
    elif a.cmd == "exp-restore":
        exp_restore(a)


if __name__ == "__main__":
    main()

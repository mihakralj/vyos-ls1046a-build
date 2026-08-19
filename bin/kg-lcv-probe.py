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


if __name__ == "__main__":
    main()

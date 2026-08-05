#!/usr/bin/env python3
"""Read live FMan KeyGen per-scheme registers via the fmkg_ar indirect
access-register protocol -- read-intent only (FM_KG_KGAR_READ, never
FM_KG_KGAR_WRITE). Protocol and register offsets are taken directly from
this project's own in-tree mainline driver
(work/linux-6.18.34/drivers/net/ethernet/freescale/fman/fman_keygen.c),
which implements this exact sequence for legitimate scheme reads -- not
re-derived from documentation guesswork.

struct fman_kg_regs (fman_keygen.c:210):
  0x000: fmkg_gcr .. 0x0FC: (globals)
  0x100-0x158: shadow window -- struct fman_kg_scheme_regs, populated by
               the AR read trigger below
  0x1FC: fmkg_ar (KeyGen Action Register)

struct fman_kg_scheme_regs (specs/fman-keygen-flow-key-spec.md Sec5.1):
  0x100 kgse_mode, 0x104 kgse_ekfc, 0x108 kgse_ekdv, 0x10C kgse_bmch,
  0x110 kgse_bmcl, 0x114 kgse_fqb, 0x118 kgse_hc, 0x11C kgse_ppc,
  0x120-0x13C kgse_gec[8], 0x140 kgse_spc, 0x144 kgse_dv0, 0x148 kgse_dv1,
  0x14C kgse_ccbs, 0x150 kgse_mv, 0x154 kgse_om, 0x158 kgse_vsp

AR bits (fman_keygen.c:41-49):
  FM_KG_KGAR_GO=0x80000000  FM_KG_KGAR_READ=0x40000000
  FM_KG_KGAR_WRITE=0x00000000 (i.e. absence of READ bit)
  FM_KG_KGAR_ERR=0x20000000  FM_KG_KGAR_SEL_SCHEME_ENTRY=0x00000000
  FM_KG_KGAR_NUM_SHIFT=16    DUMMY_PORT_ID=0

Read-scheme AR value = GO | READ | SEL_SCHEME_ENTRY | (scheme_id << 16)
                      = 0xC0000000 | (scheme_id << 16)

Physical base confirmed via /proc/device-tree/soc/fman@1a00000/keygen@c1000
(reg = 0xc1000 0x1000): FMan base (0x01a00000) + 0xc1000 = 0x01ac1000.

2026-08-01 result on `.106`: 12/32 schemes enabled, all in AC_CC mode
(kgse_mode = 0x8X000006), all with kgse_ccbs = 0 -- confirming AC_CC mode
does not use kgse_ccbs for the group-table pointer (that field is for the
separate CCBS dispatch mode). The real pointer lives in the per-port
FMBM_RCCB BMI register instead (see plans/NXP-106-ORACLE-VALIDATION-PLAN.md
Phase 2e for the follow-up and the open question on decoding it).
"""
import mmap
import os
import struct
import sys
import time

FMAN_BASE = 0x01a00000
KG_OFFSET = 0xc1000
KG_BASE = FMAN_BASE + KG_OFFSET
KG_SIZE = 0x1000

AR_OFFSET = 0x1FC
SHADOW_OFFSET = 0x100

GO = 0x80000000
READ = 0x40000000
ERR = 0x20000000
NUM_SHIFT = 16
MAX_SCHEMES = 32

SCHEME_FIELDS = [
    ("kgse_mode", 0x00), ("kgse_ekfc", 0x04), ("kgse_ekdv", 0x08),
    ("kgse_bmch", 0x0C), ("kgse_bmcl", 0x10), ("kgse_fqb", 0x14),
    ("kgse_hc", 0x18), ("kgse_ppc", 0x1C),
    ("kgse_spc", 0x40), ("kgse_dv0", 0x44), ("kgse_dv1", 0x48),
    ("kgse_ccbs", 0x4C), ("kgse_mv", 0x50), ("kgse_om", 0x54),
    ("kgse_vsp", 0x58),
]


def be32(mm, off):
    return struct.unpack_from(">I", mm, off)[0]


def set_be32(mm, off, val):
    mm[off:off + 4] = struct.pack(">I", val)


def read_scheme(mm, scheme_id, timeout_s=0.5):
    ar_val = GO | READ | (scheme_id << NUM_SHIFT)
    set_be32(mm, AR_OFFSET, ar_val)

    deadline = time.monotonic() + timeout_s
    while True:
        cur = be32(mm, AR_OFFSET)
        if not (cur & GO):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"scheme {scheme_id}: GO bit never cleared (last=0x{cur:08x})")

    if cur & ERR:
        raise RuntimeError(f"scheme {scheme_id}: FM_KG_KGAR_ERR set (ar=0x{cur:08x})")

    return {name: be32(mm, SHADOW_OFFSET + off) for name, off in SCHEME_FIELDS}


def main():
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    try:
        mm = mmap.mmap(fd, KG_SIZE, mmap.MAP_SHARED,
                        mmap.PROT_READ | mmap.PROT_WRITE, offset=KG_BASE)
    finally:
        os.close(fd)

    try:
        for scheme_id in range(MAX_SCHEMES):
            try:
                regs = read_scheme(mm, scheme_id)
            except (TimeoutError, RuntimeError) as e:
                print(f"scheme {scheme_id:2d}: ERROR {e}", file=sys.stderr)
                continue
            mode = regs["kgse_mode"]
            enabled = bool(mode & 0x80000000)
            marker = "  <-- ENABLED" if enabled else ""
            print(f"scheme {scheme_id:2d}: mode=0x{mode:08x} ekfc=0x{regs['kgse_ekfc']:08x} "
                  f"hc=0x{regs['kgse_hc']:08x} ccbs=0x{regs['kgse_ccbs']:08x} "
                  f"mv=0x{regs['kgse_mv']:08x}{marker}")
    finally:
        mm.close()


if __name__ == "__main__":
    main()

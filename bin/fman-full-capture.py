#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# fman-full-capture.py — single-pass, structured (JSON) silicon-state
# capture for cross-board FMan v3 comparison (dpaa1 `.185`, NXP-ASK
# `.106`, and any third 210.10.1/108.x board). Superset of
# ask-pcd-regdump.py (BMI Rx port + KG schemes) + kg-scheme-read.py
# (subsumed) + muram-mmap-dump.py (wrapped for a targeted region), plus
# FPM registers, which no existing tool captures at all.
#
# WHY THIS EXISTS: every FMan comparison so far this project (§5.2 of
# arch/fman-microcode-210-programming-reference.md) has been done by
# running separate tools by hand on each board and diffing printed text
# ad hoc. That does not scale to a three-board comparison and is easy
# to do inconsistently (different port-id args, different KG scheme
# ranges, etc). This tool captures everything comparable in ONE pass,
# emits one JSON document per board, self-identifying (hostname, board
# label, capture timestamp) so `diff <(jq -S . a.json) <(jq -S . b.json)`
# gives a clean structural diff.
#
# SCOPE, STATED PLAINLY:
#   - BMI Rx port registers: yes (same field list as ask-pcd-regdump.py).
#   - KG global + all 32 scheme registers: yes (full field list,
#     superset of kg-scheme-read.py which omits kgse_gec[]/mv/ccbs).
#   - FPM registers: yes, NEW — no existing tool reads these at all.
#     Covers the "interesting" subset (control/status/FMFP_EXTC/CLF*),
#     not the raw scratch arrays (fmfp_drd[16], fmfp_ts[128]).
#   - FM_CTL per-port params page (internalFEBufferManagementIndexAddr
#     +0x54, internalFEBufferDepletionCounter +0x58, per the
#     fman_port_recover design spec): NOT covered. Its MURAM offset is
#     board/config-state-dependent (only exists once a port has been
#     PCD-attached), not a fixed register — needs a separate, more
#     invasive discovery step. Deliberately left as a follow-up rather
#     than guessed at here.
#   - MURAM: raw byte-range dump (base+size, or --follow-rccb PORT_OFF
#     to dump N bytes starting at that port's live FMBM_RCCB value) —
#     base64-encoded into the JSON so it round-trips through diff/jq.
#
# Root required (same /dev/mem mmap technique as the three tools above;
# see muram-mmap-dump.py's docstring for why plain read()/dd doesn't
# work against MURAM specifically). Read-only towards MURAM. The KG
# indirect-read protocol necessarily writes the KGAR trigger register
# (same as ask-pcd-regdump.py/kg-scheme-read.py) but never writes scheme
# content — established safe by extensive use this project.
#
# Usage:
#   sudo python3 bin/fman-full-capture.py --board-label ".185-dpaa1" \
#       --port-off 0x91000 --port-label "eth4/hwport0x11" \
#       --port-id 0x11 > capture-185.json
#
#   sudo python3 bin/fman-full-capture.py --board-label ".106-nxp-ask" \
#       --port-off 0x91000 --port-label "eth4/hwport0x11" \
#       --port-id 0x11 --follow-rccb 0x91000 --rccb-dump-size 0x100 \
#       > capture-106.json
#
# Then, cross-board diff:
#   jq -S . capture-185.json > a.json; jq -S . capture-106.json > b.json
#   diff a.json b.json

import argparse
import base64
import mmap
import os
import socket
import struct
import sys
import time

FMAN_CCSR_BASE = 0x01a00000
FMAN_CCSR_LEN = 0x000fe000  # /proc/iomem: 0x01a00000-0x01afdfff

KG_OFFSET = 0xC1000
FPM_OFFSET = 0xC3000  # FM_MM_FPM, confirmed against vendor SDK + this
                       # kernel's own fman.c struct fman_fpm_regs layout
                       # (F-167, arch/.../§5.3.4/§5.3.5)

# --- KeyGen (superset of ask-pcd-regdump.py / kg-scheme-read.py) -----------

FM_KG_KGAR_GO = 0x80000000
FM_KG_KGAR_READ = 0x40000000
FM_KG_KGAR_ERR = 0x20000000
FM_KG_KGAR_SEL_SCHEME_ENTRY = 0x00000000
FM_KG_KGAR_SEL_PORT_ENTRY = 0x02000000
FM_KG_KGAR_SEL_PORT_WSEL_SP = 0x00008000
FM_KG_KGAR_NUM_SHIFT = 16

KG_GCR_OFF = 0x000
KG_SEER_OFF = 0x01C
KG_GSR_OFF = 0x024
KG_TPC_OFF = 0x028
KG_AR_OFF = 0x1FC
KG_IND_OFF = 0x100

SCHEME_FIELDS = [
    ("kgse_mode", 0x000), ("kgse_ekfc", 0x004), ("kgse_ekdv", 0x008),
    ("kgse_bmch", 0x00C), ("kgse_bmcl", 0x010), ("kgse_fqb", 0x014),
    ("kgse_hc", 0x018), ("kgse_ppc", 0x01C),
    ("kgse_gec0", 0x020), ("kgse_gec1", 0x024), ("kgse_gec2", 0x028),
    ("kgse_gec3", 0x02C), ("kgse_gec4", 0x030), ("kgse_gec5", 0x034),
    ("kgse_gec6", 0x038), ("kgse_gec7", 0x03C),
    ("kgse_spc", 0x040), ("kgse_dv0", 0x044), ("kgse_dv1", 0x048),
    ("kgse_ccbs", 0x04C), ("kgse_mv", 0x050), ("kgse_om", 0x054),
    ("kgse_vsp", 0x058),
]

PORT_SP_OFF = 0x000

# --- BMI Rx port (struct fman_port_rx_bmi_regs) -----------------------------

RX_BMI_FIELDS = [
    ("fmbm_rcfg", 0x000), ("fmbm_rst", 0x004), ("fmbm_rda", 0x008),
    ("fmbm_rfp", 0x00c), ("fmbm_rfed", 0x010), ("fmbm_ricp", 0x014),
    ("fmbm_rim", 0x018), ("fmbm_rebm", 0x01c), ("fmbm_rfne", 0x020),
    ("fmbm_rfca", 0x024), ("fmbm_rfpne", 0x028), ("fmbm_rpso", 0x02c),
    ("fmbm_rpp", 0x030), ("fmbm_rccb", 0x034), ("fmbm_reth", 0x038),
    ("fmbm_rfqid", 0x060), ("fmbm_refqid", 0x064), ("fmbm_rfene", 0x070),
    ("fmbm_rcmne", 0x07c), ("fmbm_rstc", 0x200), ("fmbm_rfrc", 0x204),
    ("fmbm_rfbc", 0x208), ("fmbm_rlfc", 0x20c), ("fmbm_rffc", 0x210),
    ("fmbm_rfdc", 0x214), ("fmbm_rfldec", 0x218), ("fmbm_rodc", 0x21c),
    ("fmbm_rbdc", 0x220), ("fmbm_rpec", 0x224),
]

# --- FPM (struct fman_fpm_regs, fman.c) — NEW, no prior tool covers this ---
# "interesting" subset: control/status/sync + per-port FMFP_PS + CLF*.
# Excludes raw scratch arrays (fmfp_drd[16] @0x80, fmfp_ts[128] @0x400).

FPM_FIELDS = [
    ("fmfp_tnc", 0x00), ("fmfp_prc", 0x04), ("fmfp_brkc", 0x08),
    ("fmfp_mxd", 0x0c), ("fmfp_dist1", 0x10), ("fmfp_dist2", 0x14),
    ("fm_epi", 0x18), ("fm_rie", 0x1c),
    ("fmfp_tsc1", 0x60), ("fmfp_tsc2", 0x64), ("fmfp_tsp", 0x68),
    ("fmfp_tsf", 0x6c), ("fm_rcr", 0x70),
    ("fmfp_extc", 0x74),  # F-167 target register
    ("fmfp_ext1", 0x78), ("fmfp_ext2", 0x7c),
    ("fm_ip_rev_1", 0xc4), ("fm_ip_rev_2", 0xc8), ("fm_rstc", 0xcc),
    ("fm_cld", 0xd0), ("fm_npi", 0xd4), ("fmfp_exte", 0xd8),
    ("fmfp_ee", 0xdc),
    ("fmfp_clfabc", 0x200), ("fmfp_clfcc", 0x204),
    ("fmfp_clfaval", 0x208), ("fmfp_clfbval", 0x20c),
    ("fmfp_clfcval", 0x210), ("fmfp_clfamsk", 0x214),
    ("fmfp_clfbmsk", 0x218), ("fmfp_clfcmsk", 0x21c),
    ("fmfp_clfamc", 0x220), ("fmfp_clfbmc", 0x224),
    ("fmfp_clfcmc", 0x228), ("fmfp_decceh", 0x22c),
]
FPM_PS_BASE = 0x100  # fmfp_ps[64], one u32 per hwport id
FPM_PS_STALLED = 0x00800000


class FmanRegs:
    def __init__(self):
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.mm = mmap.mmap(self.fd, FMAN_CCSR_LEN, mmap.MAP_SHARED,
                             mmap.PROT_READ | mmap.PROT_WRITE,
                             offset=FMAN_CCSR_BASE)

    def r32(self, off):
        return struct.unpack(">I", self.mm[off:off + 4])[0]

    def w32(self, off, val):
        self.mm[off:off + 4] = struct.pack(">I", val)

    def close(self):
        self.mm.close()
        os.close(self.fd)


def kg_indirect_op(fr, ar_value, timeout_us=10000):
    fr.w32(KG_OFFSET + KG_AR_OFF, ar_value)
    deadline = time.monotonic_ns() + timeout_us * 1000
    while time.monotonic_ns() < deadline:
        ar = fr.r32(KG_OFFSET + KG_AR_OFF)
        if not (ar & FM_KG_KGAR_GO):
            return (not (ar & FM_KG_KGAR_ERR)), ar
    return False, fr.r32(KG_OFFSET + KG_AR_OFF)


def capture_kg(fr):
    out = {
        "global": {
            "fmkg_gcr": fr.r32(KG_OFFSET + KG_GCR_OFF),
            "fmkg_gsr": fr.r32(KG_OFFSET + KG_GSR_OFF),
            "fmkg_seer": fr.r32(KG_OFFSET + KG_SEER_OFF),
            "fmkg_tpc": fr.r32(KG_OFFSET + KG_TPC_OFF),
        },
        "schemes": {},
    }
    for s in range(32):
        ar = (FM_KG_KGAR_GO | FM_KG_KGAR_READ | FM_KG_KGAR_SEL_SCHEME_ENTRY
              | (s << FM_KG_KGAR_NUM_SHIFT))
        ok, final_ar = kg_indirect_op(fr, ar)
        if not ok:
            out["schemes"][str(s)] = {"error": f"KGAR failed ar=0x{final_ar:08x}"}
            continue
        fields = {name: fr.r32(KG_OFFSET + KG_IND_OFF + off)
                  for name, off in SCHEME_FIELDS}
        mode = fields["kgse_mode"]
        if (mode >> 31) & 1 or fields["kgse_mv"] or fields["kgse_spc"]:
            out["schemes"][str(s)] = fields
    return out


def capture_kg_port_partitions(fr, port_ids):
    out = {}
    for pid in port_ids:
        ar = (FM_KG_KGAR_GO | FM_KG_KGAR_READ | FM_KG_KGAR_SEL_PORT_ENTRY
              | pid | FM_KG_KGAR_SEL_PORT_WSEL_SP)
        ok, final_ar = kg_indirect_op(fr, ar)
        if not ok:
            out[f"0x{pid:02x}"] = {"error": f"KGAR failed ar=0x{final_ar:08x}"}
            continue
        sp = fr.r32(KG_OFFSET + KG_IND_OFF + PORT_SP_OFF)
        out[f"0x{pid:02x}"] = {
            "fmkg_pe_sp": sp,
            "bound_schemes": [b for b in range(32) if sp & (1 << b)],
        }
    return out


def capture_bmi_port(fr, port_off):
    return {name: fr.r32(port_off + off) for name, off in RX_BMI_FIELDS}


def capture_fpm(fr, port_ids):
    out = {name: fr.r32(FPM_OFFSET + off) for name, off in FPM_FIELDS}
    ps = {}
    for pid in port_ids:
        val = fr.r32(FPM_OFFSET + FPM_PS_BASE + pid * 4)
        ps[f"0x{pid:02x}"] = {
            "fmfp_ps": val,
            "stalled": bool(val & FPM_PS_STALLED),
        }
    out["fmfp_ps_by_port"] = ps
    return out


def capture_muram_region(base, size):
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    try:
        page = base & ~0xFFF
        pad = base - page
        m = mmap.mmap(fd, size + pad, mmap.MAP_SHARED, mmap.PROT_READ,
                      offset=page)
        try:
            data = m[pad:pad + size]
        finally:
            m.close()
    finally:
        os.close(fd)
    return data


def main():
    ap = argparse.ArgumentParser(
        description="Single-pass structured FMan v3 silicon-state capture "
                    "for cross-board comparison (KG + BMI + FPM + MURAM).")
    ap.add_argument("--board-label", required=True,
                     help="Free-text identifier for this board/build in the "
                          "output JSON, e.g. '.185-dpaa1' or '.106-nxp-ask'.")
    ap.add_argument("--port-off", type=lambda s: int(s, 0), action="append",
                     default=None,
                     help="FMan-internal BMI Rx port offset to capture. "
                          "Repeatable. Default: 0x90000 and 0x91000.")
    ap.add_argument("--port-label", action="append", default=None,
                     help="Label for the matching --port-off, matched by order.")
    ap.add_argument("--port-id", type=lambda s: int(s, 0), action="append",
                     default=None,
                     help="hwport_id to probe for KG port-partition + FPM "
                          "FMFP_PS. Repeatable. Default: 0x08 0x09 0x10 0x11.")
    ap.add_argument("--muram-base", type=lambda s: int(s, 0), default=None,
                     help="Physical base address for a raw MURAM region dump "
                          "(mutually exclusive with --follow-rccb).")
    ap.add_argument("--muram-size", type=lambda s: int(s, 0),
                     default=0x1000,
                     help="Size in bytes for --muram-base or --follow-rccb "
                          "(default 0x1000).")
    ap.add_argument("--follow-rccb", type=lambda s: int(s, 0), default=None,
                     help="FMan-internal BMI Rx port offset whose live "
                          "FMBM_RCCB value to read and dump --muram-size "
                          "bytes of MURAM starting there (group-table "
                          "capture, for the word3/index*16 cross-board "
                          "check — see arch/.../§5.3.2).")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("ERROR: must run as root", file=sys.stderr)
        return 2

    port_offs = args.port_off or [0x90000, 0x91000]
    port_labels = args.port_label or []
    if len(port_labels) < len(port_offs):
        port_labels = port_labels + [f"port@0x{o:05x}" for o in
                                      port_offs[len(port_labels):]]
    port_ids = args.port_id or [0x08, 0x09, 0x10, 0x11]

    fr = FmanRegs()
    try:
        doc = {
            "capture_tool": "fman-full-capture.py",
            "board_label": args.board_label,
            "hostname": socket.gethostname(),
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fman_ccsr_base": f"0x{FMAN_CCSR_BASE:08x}",
            "keygen": capture_kg(fr),
            "keygen_port_partitions": capture_kg_port_partitions(fr, port_ids),
            "bmi_rx_ports": {
                lab: {
                    "fman_offset": f"0x{off:06x}",
                    "registers": capture_bmi_port(fr, off),
                }
                for off, lab in zip(port_offs, port_labels)
            },
            "fpm": capture_fpm(fr, port_ids),
        }

        if args.follow_rccb is not None:
            rccb = fr.r32(args.follow_rccb + 0x034)  # fmbm_rccb offset
            data = capture_muram_region(FMAN_CCSR_BASE + rccb, args.muram_size)
            doc["muram_follow_rccb"] = {
                "source_port_offset": f"0x{args.follow_rccb:06x}",
                "fmbm_rccb": f"0x{rccb:08x}",
                "dump_base_phys": f"0x{FMAN_CCSR_BASE + rccb:08x}",
                "dump_size": args.muram_size,
                "data_b64": base64.b64encode(data).decode("ascii"),
            }
        elif args.muram_base is not None:
            data = capture_muram_region(args.muram_base, args.muram_size)
            doc["muram_region"] = {
                "dump_base_phys": f"0x{args.muram_base:08x}",
                "dump_size": args.muram_size,
                "data_b64": base64.b64encode(data).decode("ascii"),
            }
    finally:
        fr.close()

    import json
    print(json.dumps(doc, indent=2, sort_keys=True))
    print(f"### fman-full-capture: done, board_label={args.board_label!r}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

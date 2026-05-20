#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# ask-pcd-regdump.py — PR14z12-B diagnostic v2: read FMan v3 silicon
# state via /dev/mem mmap.  v2 uses register offsets derived directly
# from drivers/net/ethernet/freescale/fman/fman_port.c (struct
# fman_port_rx_bmi_regs) and fman_keygen.c (indirect KGAR access).
#
# v1 (deleted) used speculative RM-derived offsets that turned out to
# be wrong by a wide margin.  v2 is anchored to the kernel's own
# canonical struct layout, so the offsets are by construction the
# offsets that the in-tree FMan driver writes to.
#
# Target: live LS1046A Mono Gateway DK, FLAVOR=ask, /dev/mem unrestricted
# (CONFIG_STRICT_DEVMEM is not set — verified 2026-05-19 via /proc/config.gz).
#

import argparse
import mmap
import os
import struct
import sys
import time

# CCSR window
FMAN_CCSR_BASE = 0x01a00000
FMAN_CCSR_LEN  = 0x000fe000   # /proc/iomem says 0x01a00000-0x01afdfff

# KeyGen block (fman.c: KG_OFFSET 0x000C1000)
KG_OFFSET = 0xC1000

# KG_GCR enable bit (fman_keygen.c FM_KG_KGGCR_EN)
FM_KG_KGGCR_EN = 0x80000000

# Indirect-access protocol (fman_keygen.c)
FM_KG_KGAR_GO              = 0x80000000
FM_KG_KGAR_READ            = 0x40000000
FM_KG_KGAR_WRITE           = 0x00000000
FM_KG_KGAR_ERR             = 0x20000000
FM_KG_KGAR_SEL_SCHEME_ENTRY = 0x00000000
FM_KG_KGAR_SEL_PORT_ENTRY   = 0x02000000
FM_KG_KGAR_SEL_CLS_PLAN     = 0x01000000
FM_KG_KGAR_SEL_PORT_WSEL_SP  = 0x00008000
FM_KG_KGAR_SEL_PORT_WSEL_CPP = 0x00004000
FM_KG_KGAR_SCM_WSEL_UPDATE_CNT = 0x00008000
FM_KG_KGAR_NUM_SHIFT       = 16

# KG_GCR == fmkg_regs.fmkg_gcr at +0x00
# KG_AR  == fmkg_regs.fmkg_ar  at +0x1FC
# Indirect window == fmkg_regs.fmkg_indirect[63] at +0x100..0x1FB (252 B = 63 u32)
KG_GCR_OFF  = 0x000
KG_TPC_OFF  = 0x028   # fmkg_tpc — Total Packet Counter (global)
KG_GSR_OFF  = 0x024   # fmkg_gsr — Global Status
KG_SEER_OFF = 0x01C   # fmkg_seer — Scheme Error Event
KG_AR_OFF   = 0x1FC
KG_IND_OFF  = 0x100   # base of fmkg_indirect[]

# Scheme-entry struct (fman_kg_scheme_regs) — read via KGAR indirect.
# Field offsets within the indirect window (bytes from KG_IND_OFF).
# Derived from the struct layout in fman_keygen.c lines 134-154.
SCHEME_FIELDS = [
    ("kgse_mode",     0x000, "Mode (EN bit31, NIA in low bits)"),
    ("kgse_ekfc",     0x004, "Extract from Frame Command"),
    ("kgse_ekdv",     0x008, "Extract Known Default Value"),
    ("kgse_bmch",     0x00C, "Bitmask Header"),
    ("kgse_bmcl",     0x010, "Bitmask Tail"),
    ("kgse_fqb",      0x014, "FQID Base"),
    ("kgse_hc",       0x018, "Hash Config"),
    ("kgse_ppc",      0x01C, "Policer Profile Config"),
    ("kgse_gec[0]",   0x020, "Generic Extract 0"),
    ("kgse_gec[1]",   0x024, "Generic Extract 1"),
    ("kgse_gec[2]",   0x028, "Generic Extract 2"),
    ("kgse_gec[3]",   0x02C, "Generic Extract 3"),
    ("kgse_gec[4]",   0x030, "Generic Extract 4"),
    ("kgse_gec[5]",   0x034, "Generic Extract 5"),
    ("kgse_gec[6]",   0x038, "Generic Extract 6"),
    ("kgse_gec[7]",   0x03C, "Generic Extract 7"),
    ("kgse_spc",      0x040, "Scheme Packet Counter (LIVE)"),
    ("kgse_dv0",      0x044, "Default Value 0"),
    ("kgse_dv1",      0x048, "Default Value 1"),
    ("kgse_ccbs",     0x04C, "Coarse Classification Bit (CC-tree handle)"),
    ("kgse_mv",       0x050, "Match Vector (which ports route here)"),
    ("kgse_om",       0x054, "Operation Mode bits"),
    ("kgse_vsp",      0x058, "Virtual Storage Profile"),
]

# Port-entry struct (fman_kg_pe_regs):
#   fmkg_pe_sp  at +0x000
#   fmkg_pe_cpp at +0x004
# When KGAR is built with SEL_PORT_ENTRY | hwport_id | WSEL_SP the indirect
# window's first u32 holds the port's Scheme Partition mask (bit s set
# means scheme s is bound to this port).
PORT_SP_OFF  = 0x000
PORT_CPP_OFF = 0x004

# BMI Rx port struct field offsets — derived from
# struct fman_port_rx_bmi_regs (fman_port.c lines 174-230).
RX_BMI_FIELDS = [
    ("fmbm_rcfg",    0x000, "Rx Configuration (EN bit31)"),
    ("fmbm_rst",     0x004, "Rx Status (BSY bit31)"),
    ("fmbm_rda",     0x008, "Rx DMA attributes"),
    ("fmbm_rfp",     0x00c, "Rx FIFO Parameters"),
    ("fmbm_rfed",    0x010, "Rx Frame End Data"),
    ("fmbm_ricp",    0x014, "Rx Internal Context Parameters"),
    ("fmbm_rim",     0x018, "Rx Internal Buffer Margins"),
    ("fmbm_rebm",    0x01c, "Rx External Buffer Margins"),
    ("fmbm_rfne",    0x020, "Rx Frame Next Engine (POST-PARSER)"),
    ("fmbm_rfca",    0x024, "Rx Frame Command Attributes"),
    ("fmbm_rfpne",   0x028, "Rx Frame Parser Next Engine (POST-PARSER routing) **KEY**"),
    ("fmbm_rpso",    0x02c, "Rx Parse Start Offset"),
    ("fmbm_rpp",     0x030, "Rx Policer Profile"),
    ("fmbm_rccb",    0x034, "Rx Coarse Classification Base"),
    ("fmbm_reth",    0x038, "Rx Excessive Threshold"),
    ("fmbm_rfqid",   0x060, "Rx Frame Queue ID (default FQ)"),
    ("fmbm_refqid",  0x064, "Rx Error Frame Queue ID"),
    ("fmbm_rfene",   0x070, "Rx Frame Enqueue Next Engine"),
    ("fmbm_rcmne",   0x07c, "Rx Frame Continuous Mode Next Engine"),
    ("fmbm_rstc",    0x200, "Rx Statistics Counters control"),
    ("fmbm_rfrc",    0x204, "Rx Frame Counter (LIVE)"),
    ("fmbm_rfbc",    0x208, "Rx Bad Frames Counter"),
    ("fmbm_rlfc",    0x20c, "Rx Large Frames Counter"),
    ("fmbm_rffc",    0x210, "Rx Filter Frames Counter"),
    ("fmbm_rfdc",    0x214, "Rx Frame Discard Counter (LIVE)"),
    ("fmbm_rfldec",  0x218, "Rx Frames List DMA Error Counter"),
    ("fmbm_rodc",    0x21c, "Rx Out of Buffers Discard counter"),
    ("fmbm_rbdc",    0x220, "Rx Buffers Deallocate Counter"),
    ("fmbm_rpec",    0x224, "Rx Prepare to enqueue Counter"),
]

# NIA engine field — bits 23..20 in NIA u32 (per fman_port.c NIA_ENG_BMI = 0x00500000)
# We extract via (val & 0x00F00000) >> 20.
# CORRECTED 2026-05-19 after v2 idle dump: engine 4 IS the KG-hash-dispatch
# engine (NIA_ENG_HWK = 0x00440000 in fman_port.c).  Engine 3 doesn't actually
# exist as "KG" in v3 silicon — KG arming is encoded as eng=4 (HWK) with the
# action lower bits selecting hash vs direct.
NIA_ENG = {
    0x0: "DONE",
    0x1: "RES1",
    0x2: "PRS (Parser)",
    0x3: "RES3",
    0x4: "HWK (KG hash dispatch)",   # NIA_ENG_HWK = 0x00440000
    0x5: "BMI",                       # NIA_ENG_BMI = 0x00500000
    0x6: "QMI_ENQ",
    0x7: "QMI_DEQ",
    0x8: "FM_CTL_A",
    0x9: "FM_CTL_B",
    0xA: "PLCR",
    0xB: "FR (Frame Replicator)",
    0xC: "CC (Coarse Classifier)",
    # 0xD..0xF: reserved
}

# Actual constants from fman_port.c:
#   NIA_ENG_BMI = 0x00500000     (engine=5 in 23..20)
#   NIA_ENG_HWK = ?              (KG=3, but symbol not defined here yet)
# So FMBM_RFPNE = NIA_ENG_BMI | NIA_BMI_AC_ENQ_FRAME = 0x00500002 default.
# When fman_port_use_kg_hash() arms KG it writes NIA_ENG_HWK = 0x00440000
# (engine=4, action 4 = "feed to KG"). Let me verify by reading what
# the live FWD port actually contains; the engine code shown in the dump
# becomes the source of truth.

# NIA action code — bits 19..0 (lower 20). Not strictly decoded, just printed.

def nia_decode(nia):
    eng = (nia >> 20) & 0xf
    act = nia & 0x000fffff
    eng_name = NIA_ENG.get(eng, "?")
    return f"eng={eng:#x} ({eng_name}) act={act:#x}"

class FmanRegs:
    def __init__(self):
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.mm = mmap.mmap(self.fd, FMAN_CCSR_LEN,
                            mmap.MAP_SHARED,
                            mmap.PROT_READ | mmap.PROT_WRITE,
                            offset=FMAN_CCSR_BASE)

    def r32(self, off):
        # FMan CCSR is big-endian u32
        return struct.unpack(">I", self.mm[off:off+4])[0]

    def w32(self, off, val):
        self.mm[off:off+4] = struct.pack(">I", val)

    def close(self):
        self.mm.close()
        os.close(self.fd)

# Indirect KG access protocol from fman_keygen.c:keygen_write/read_*().
#   1. Build AR value with GO | READ | SEL | id | WSEL
#   2. Write to KG_AR
#   3. Poll KG_AR until GO bit clears (GO is also the "busy" indicator)
#   4. Read fmkg_indirect[] (KG_IND_OFF)
#
# Indirect read for scheme entry:
#   AR = GO | READ | SEL_SCHEME_ENTRY (=0) | (scheme_id << 16)
#
# Indirect read for port entry (SP):
#   AR = GO | READ | SEL_PORT_ENTRY (=0x02000000) | port_id | WSEL_SP (=0x00008000)

def kg_indirect_op(fr, ar_value, timeout_us=10000):
    fr.w32(KG_OFFSET + KG_AR_OFF, ar_value)
    # Poll for GO bit to clear
    deadline = time.monotonic_ns() + timeout_us * 1000
    while time.monotonic_ns() < deadline:
        ar = fr.r32(KG_OFFSET + KG_AR_OFF)
        if not (ar & FM_KG_KGAR_GO):
            if ar & FM_KG_KGAR_ERR:
                return False, ar
            return True, ar
    return False, fr.r32(KG_OFFSET + KG_AR_OFF)

def kg_read_scheme(fr, scheme_id):
    ar = (FM_KG_KGAR_GO
          | FM_KG_KGAR_READ
          | FM_KG_KGAR_SEL_SCHEME_ENTRY
          | (scheme_id << FM_KG_KGAR_NUM_SHIFT))
    ok, final_ar = kg_indirect_op(fr, ar)
    if not ok:
        return None, final_ar
    # Read the indirect window
    out = {}
    for name, off, _ in SCHEME_FIELDS:
        out[name] = fr.r32(KG_OFFSET + KG_IND_OFF + off)
    return out, final_ar

def kg_read_port_sp(fr, port_id):
    ar = (FM_KG_KGAR_GO
          | FM_KG_KGAR_READ
          | FM_KG_KGAR_SEL_PORT_ENTRY
          | port_id
          | FM_KG_KGAR_SEL_PORT_WSEL_SP)
    ok, final_ar = kg_indirect_op(fr, ar)
    if not ok:
        return None, final_ar
    sp = fr.r32(KG_OFFSET + KG_IND_OFF + PORT_SP_OFF)
    return sp, final_ar

def decode_sp_mask(sp):
    """SP mask bit b set => scheme b is bound to this port."""
    schemes = []
    for b in range(32):
        if sp & (1 << b):
            schemes.append(b)
    return schemes

def dump_kg_global(fr):
    print(f"\n=== KeyGen global @ FMan offset 0x{KG_OFFSET:06x} ===")
    gcr = fr.r32(KG_OFFSET + KG_GCR_OFF)
    gsr = fr.r32(KG_OFFSET + KG_GSR_OFF)
    seer = fr.r32(KG_OFFSET + KG_SEER_OFF)
    tpc = fr.r32(KG_OFFSET + KG_TPC_OFF)
    print(f"  fmkg_gcr   = 0x{gcr:08x}  EN={(gcr>>31)&1}")
    print(f"  fmkg_gsr   = 0x{gsr:08x}")
    print(f"  fmkg_seer  = 0x{seer:08x}   // sticky scheme error events")
    print(f"  fmkg_tpc   = 0x{tpc:08x}  ({tpc} dec)   // total packets through KG")

def dump_kg_scheme(fr, s, force=False):
    data, ar = kg_read_scheme(fr, s)
    if data is None:
        print(f"\n=== KG scheme {s}: KGAR read FAILED (ar=0x{ar:08x}) ===")
        return False
    mode = data["kgse_mode"]
    mv = data["kgse_mv"]
    en = (mode >> 31) & 1
    if not force and en == 0 and mv == 0 and data["kgse_spc"] == 0:
        return False  # empty slot
    print(f"\n=== KG scheme {s} {'[ACTIVE]' if en else '[disabled]'} ===")
    for name, off, doc in SCHEME_FIELDS:
        val = data[name]
        line = f"  {name:14s} = 0x{val:08x}"
        if name == "kgse_mode":
            line += f"  EN={(val>>31)&1}"
        elif name == "kgse_mv":
            ports = [b for b in range(16) if val & (1 << b)]
            line += f"  -> ports-bits={ports}"
        elif name == "kgse_ccbs":
            line += f"  CC-tree-handle"
        elif name == "kgse_spc":
            line += f"  ({val} pkts classified by this scheme)"
        print(line, "  //", doc)
    return True

def dump_all_kg_schemes(fr, force=False):
    found = 0
    for s in range(32):
        if dump_kg_scheme(fr, s, force=force):
            found += 1
    print(f"\n(KG scheme scan: {found} non-empty slot(s))")

def dump_port_sp(fr, port_id):
    """Read the Scheme Partition mask bound to a given HW port id."""
    sp, ar = kg_read_port_sp(fr, port_id)
    if sp is None:
        print(f"\n=== Port hwport_id 0x{port_id:02x} ({port_id}): KGAR read FAILED ar=0x{ar:08x} ===")
        return
    schemes = decode_sp_mask(sp)
    print(f"\n=== KG Port-Partition for hwport_id 0x{port_id:02x} ({port_id}) ===")
    print(f"  fmkg_pe_sp = 0x{sp:08x}  -> bound schemes: {schemes}")

def dump_port_bmi(fr, port_off_in_fman, label):
    print(f"\n=== BMI Rx '{label}' @ FMan offset 0x{port_off_in_fman:06x} (CCSR 0x{FMAN_CCSR_BASE + port_off_in_fman:08x}) ===")
    for name, off, doc in RX_BMI_FIELDS:
        addr = port_off_in_fman + off
        val = fr.r32(addr)
        line = f"  {name:12s} @0x{addr:06x}  = 0x{val:08x}"
        if name == "fmbm_rfpne":
            line += f"  -> {nia_decode(val)}"
            eng = (val >> 20) & 0xf
            if eng == 0x4:
                line += "  ✓ KG armed (HWK)"
            elif eng == 0x5:
                line += "  ✗ falls through to BMI default-FQ (KG NOT armed)"
            else:
                line += f"  ? unexpected engine"
        elif name == "fmbm_rfne":
            line += f"  -> {nia_decode(val)}"
        elif name == "fmbm_rcfg":
            line += f"  EN={(val>>31)&1}"
        elif name in ("fmbm_rfrc", "fmbm_rfdc", "fmbm_rfbc"):
            line += f"  ({val} dec)"
        print(line, "  //", doc)

def main():
    ap = argparse.ArgumentParser(description="PR14z12-B v2 silicon-state dump")
    ap.add_argument("--port-off", type=lambda s: int(s, 0),
                    action="append",
                    help="FMan-internal BMI-port offset to dump. "
                         "Default: 0x90000 (10G RX 0 / eth3) and 0x91000 "
                         "(10G RX 1 / eth4).  Repeatable.")
    ap.add_argument("--port-label", action="append",
                    help="Label for the matching --port-off (matched by order).")
    ap.add_argument("--scheme-force-all", action="store_true",
                    help="Dump all 32 KG scheme slots even if empty.")
    ap.add_argument("--port-id", type=int, action="append",
                    help="Probe KG Port-Partition for this hwport_id. "
                         "Default: probe 0x08, 0x09, 0x10, 0x11 (the four "
                         "candidate values for our two 10G ports).")
    ap.add_argument("-w", "--watch", action="store_true",
                    help="Loop forever at 1 Hz to watch counters change.")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("ERROR: must run as root", file=sys.stderr)
        return 2

    port_offs = args.port_off or [0x90000, 0x91000]
    port_labels = args.port_label or ["10G RX 0 / eth3 (DT cell-index 0x10)",
                                       "10G RX 1 / eth4 (DT cell-index 0x11)"]
    if len(port_labels) < len(port_offs):
        port_labels = port_labels + [f"port@{o:05x}" for o in port_offs[len(port_labels):]]

    port_ids = args.port_id or [0x08, 0x09, 0x10, 0x11]

    fr = FmanRegs()
    try:
        def one_pass():
            dump_kg_global(fr)
            dump_all_kg_schemes(fr, force=args.scheme_force_all)
            for pid in port_ids:
                dump_port_sp(fr, pid)
            for off, lab in zip(port_offs, port_labels):
                dump_port_bmi(fr, off, lab)

        if args.watch:
            while True:
                os.system("clear")
                print(f"ask-pcd-regdump v2  {time.strftime('%Y-%m-%d %H:%M:%S')}")
                one_pass()
                time.sleep(1.0)
        else:
            one_pass()
    finally:
        fr.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
"""F-209 (T-M6-1 IPv6 productization, step 1): encode CCOBASE in the AC_CC
KeyGen scheme branch.

keygen_scheme_setup() sets KGSE_MODE per scheme. The next_engine==2 (CC-graft)
branch already ORs (cc_base_offset << KG_SCH_MODE_CCOBASE_SHIFT), but the
next_engine==3 (AC_CC / FE-VM arm) branch writes ONLY
NIA_ENG_FM_CTL | NIA_FM_CTL_AC_CC and drops cc_base_offset entirely. So an
AC_CC scheme always gets CCOBASE=0 and can only ever dispatch to the ehash
node at FMBM_RCCB+0.

Silicon proof (2026-08-19, dev board .185, eth1 sandbox): the FMan selects the
CC Action Descriptor at `FMBM_RCCB + CCOBASE*16`, CCOBASE = KGSE_MODE[30:24].
Writing table1's en_exthash_node at RCCB+16 and setting a second (IPv6) scheme
to KGSE_MODE=0x81000006 (CCOBASE=1) delivered a clean IPv6 HIT into table1
(pkt_count 0->3, pkt_bytes 282) while the v4 scheme (CCOBASE=0) kept hitting
table0. This is the prerequisite that lets the v6 scheme carry CCOBASE=1.

SAFETY / BYTE-IDENTITY: the production v4 ASK scheme is armed with
cc_base_offset=0, so (0 << 24)=0 and KGSE_MODE stays exactly 0x80000006 — the
v4 path is byte-identical. Only a scheme explicitly armed with a non-zero
cc_base_offset (the future v6 scheme) changes. This edits ONLY the CCOBASE
bits [30:24]; it does NOT touch the NIA engine bits (the F-062c-R1 mistake,
2026-07-14, was adding ENQUEUE_KG_DFLT_NIA here which set engine=BMI and
stalled — explicitly not repeated).

Idempotent (F-209 marker). Count-gated: exactly one anchor. Must run after the
patches that establish the AC_CC branch (0133) — it is late in the sequence, so
place after the other keygen fixups.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
kg_c = os.path.join(kroot, "fman_keygen.c")

if not os.path.exists(kg_c):
    print(f"### F-209: FATAL: {kg_c} not found")
    sys.exit(1)

with open(kg_c) as f:
    src = f.read()

if "F-209" in src:
    print("### F-209 already applied")
    sys.exit(0)

# Anchor: the AC_CC (next_engine==3) branch body. The distinctive line is the
# lone NIA write with no CCOBASE. Match it with its trailing newline so the
# replacement inserts the CCOBASE OR immediately after.
old = "\t\t\ttmp_reg |= NIA_ENG_FM_CTL | NIA_FM_CTL_AC_CC;\n"
new = (
    "\t\t\ttmp_reg |= NIA_ENG_FM_CTL | NIA_FM_CTL_AC_CC;\n"
    "\t\t\t/* F-209: carry the per-scheme CCOBASE row index so an\n"
    "\t\t\t * AC_CC scheme can select its own ehash node at\n"
    "\t\t\t * FMBM_RCCB + CCOBASE*16 (silicon-proven 2026-08-19).\n"
    "\t\t\t * v4 ASK arms cc_base_offset=0 -> byte-identical\n"
    "\t\t\t * 0x80000006; the IPv6 scheme uses 1 -> 0x81000006.\n"
    "\t\t\t * Touch ONLY bits [30:24]; never the NIA engine bits\n"
    "\t\t\t * (the F-062c-R1 BMI-engine stall, 2026-07-14). */\n"
    "\t\t\ttmp_reg |= (scheme->cc_base_offset <<\n"
    "\t\t\t\t    KG_SCH_MODE_CCOBASE_SHIFT);\n"
)

n = src.count(old)
if n != 1:
    print(f"### F-209: FATAL: expected 1 AC_CC-branch anchor, found {n} "
          "-- source drifted. Refusing to guess.")
    sys.exit(1)

src = src.replace(old, new, 1)
with open(kg_c, "w") as f:
    f.write(src)
print("### fman_keygen.c: F-209 CCOBASE encoded in AC_CC branch (v4 byte-identical)")

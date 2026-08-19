"""F-213 (IPv6 silicon research, READ-ONLY diagnostic): extend hash_probe to
also dump the parser result (L3R/L4R/CPID/LCV) of the captured RX frame.

WHY
---
The 2026-08-19 slot-sweep proved slot-based LCV discrimination is invalid: both
v4 and v6 transit frames activate only pmda HXS slot 0, so setting slot5/6 masks
cannot route families to distinct schemes. The vendor cdx_pcd.xml instead selects
a per-protocol scheme via <distribution><protocolref name="tcp/ipv4/ipv6/..."/>,
which the FMC compiler turns into a KeyGen match-vector (kgse_mv) matched against
the parser's per-frame LCV. The open question is the EXACT bits: what LCV (and
L3R/L4R/CPID) does the hard parser actually produce for a v4 frame vs a v6 frame
on this microcode/port? That is the value kgse_mv must match.

The parse result lives in the per-frame Internal Context at IC+0x20 (32 bytes):
  +0x04 l3r  (u16; bit15=IPv4, bit14=IPv6)
  +0x06 l4r  (u8;  bit6=UDP, bit5=TCP)
  +0x07 cpid (u8;  classification-plan id)
  +0x0C lcv  (u32; line-up confirmation vector)  <-- the kgse_mv target
BMI copies the IC into the RX buffer headroom; F-072 already captures the frame
vaddr into fman_pcd_ic_vaddr and the KG hash offset into fman_pcd_hash_off at the
RXHASH anchor (the hash sits at prs_result_offset + 0x28, so the parse result is
at hash_off - 0x28). hash_probe currently prints only the hash; this adds a
read-only dump of the parse-result words at that derived offset.

SCOPE / SAFETY
--------------
READ-ONLY, investigative. Only edits fman_pcd_hash_probe_show() (a debugfs .show
handler) to read a few words from the already-captured frame buffer. No datapath,
register, scheme, LCV, or MURAM writes. No production behavior change. Reading a
frame buffer the CPU already owns at dequeue; bounded, no scanning past the known
offset. Gate: skip if hash_off < 0x28 or vaddr NULL. This is the exact tool to
harvest the real per-family LCV so a correct protocol-match-vector v6 selection
can be designed (replacing the invalid slot-based F-212 premise).

Idempotent (F-213 marker). Must run AFTER F-071 (creates hash_probe_show) and
F-072 (captures fman_pcd_ic_vaddr / fman_pcd_hash_off). Place after F-170.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
if not os.path.exists(path):
    print("### F-213: fman_pcd.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

if "F-213" in src:
    print("### F-213 already applied")
    sys.exit(0)

# Anchor on the hash_probe_show print statement's tail. Support both the
# F-071 form (hash_off + hash) and the F-170 form (adds ifname, 3-line split).
candidates = [
    # F-170 canonical (3-line split, ternary ifname)
    "\tseq_printf(m, \"hash_off=%u captured=%016llx if=%s\\n\",\n"
    "\t\tfman_pcd_hash_off, fman_pcd_kg_hash,\n"
    "\t\tfman_pcd_hash_ifname[0] ? fman_pcd_hash_ifname : \"?\");\n",
    # F-170 single-line variant
    "\t\tfman_pcd_hash_off, fman_pcd_kg_hash, fman_pcd_hash_ifname);\n",
    # F-071 base (no ifname)
    "\t\tfman_pcd_hash_off, fman_pcd_kg_hash);\n",
]
anchor = next((c for c in candidates if c in src), None)
if anchor is None:
    print("### F-213: FATAL: hash_probe_show print anchor not found "
          "(F-071/F-170 changed) -- refusing to guess.")
    sys.exit(1)

dump = (
    "\t/* F-213 (read-only): dump the parser result of the captured frame so\n"
    "\t * the real per-family LCV/L3R/L4R/CPID can be harvested. The parse\n"
    "\t * result sits at (hash_off - 0x28) in the RX buffer headroom; the KG\n"
    "\t * hash is at parse_result + 0x28 (F-069 v5 finding). Read-only. */\n"
    "\tif (fman_pcd_ic_vaddr && fman_pcd_hash_off >= 0x28) {\n"
    "\t\tconst u8 *pr = (const u8 *)fman_pcd_ic_vaddr +\n"
    "\t\t\t       (fman_pcd_hash_off - 0x28);\n"
    "\t\tu16 l3r = ((u16)pr[0x04] << 8) | pr[0x05];\n"
    "\t\tu8  l4r = pr[0x06];\n"
    "\t\tu8  cpid = pr[0x07];\n"
    "\t\tu32 lcv = ((u32)pr[0x0c] << 24) | ((u32)pr[0x0d] << 16) |\n"
    "\t\t\t  ((u32)pr[0x0e] << 8) | pr[0x0f];\n"
    "\t\tu32 pr0 = ((u32)pr[0x00] << 24) | ((u32)pr[0x01] << 16) |\n"
    "\t\t\t  ((u32)pr[0x02] << 8) | pr[0x03];\n"
    "\t\tu32 pr1 = ((u32)pr[0x04] << 24) | ((u32)pr[0x05] << 16) |\n"
    "\t\t\t  ((u32)pr[0x06] << 8) | pr[0x07];\n"
    "\t\tseq_printf(m,\n"
    "\t\t\t   \"parse_result: l3r=0x%04x l4r=0x%02x cpid=0x%02x lcv=0x%08x\"\n"
    "\t\t\t   \" pr[0..3]=0x%08x pr[4..7]=0x%08x\\n\",\n"
    "\t\t\t   l3r, l4r, cpid, lcv, pr0, pr1);\n"
    "\t\tseq_printf(m,\n"
    "\t\t\t   \"  hint: l3r bit15=IPv4 bit14=IPv6; l4r bit6=UDP bit5=TCP;\"\n"
    "\t\t\t   \" lcv is the kgse_mv target\\n\");\n"
    "\t}\n"
)

src = src.replace(anchor, anchor + dump, 1)
with open(path, "w") as f:
    f.write(src)
print("### fman_pcd.c: F-213 hash_probe parse-result (L3R/L4R/CPID/LCV) dump added")

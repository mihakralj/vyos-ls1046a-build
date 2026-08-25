"""F-234 (T-M6-8): MURAM frag-info block + VLAN-record bpid/word2 for the
FE-VM L2-rebuild path.

Evidence (qdrant ask2-vlan-analysis, 2026-08-25): ASK2 VLAN offload forwards
only ~20 packets then silently freezes (no ErrFD, no FMFP_PS stall, no FE
workspace depletion). The vendor cdx_ehash.c create_enque_hm() ALWAYS sets the
ENQUEUE param's `bpid` (a real BMan pool) AND `word2` (a MURAM "frag-info"
block pointer) on every flow; ASK2 sets both to 0. Plain-routed HITs work with
bpid=0/word2=0 because they do not STRIP_ETH(0x11)+INSERT_L2(0x41) rebuild the
L2 header and never need to acquire a rebuild buffer. A live /dev/mem patch of
bpid alone did NOT fix the freeze -- word2 was still 0 and MURAM cannot be
written from userspace. So we do it in-kernel: allocate+init a cdx-style
frag-info block in MURAM once, and write bpid+word2 into VLAN ehash records
ONLY (routed/NAT records stay byte-identical).

The cdx frag-info block (cdx_ucode_frag_info_t, 24 B, packed, big-endian):
  u16 frag_options; u16 pad; u32 alloc_buff_failures;
  u32 v4_frames_counter; u32 v6_frames_counter;
  u32 v4_frags_counter;  u32 v6_frags_counter;  u32 v6_identification;
Init: frag_options = BPID_ENABLE(0x08) | OPT_COUNTER_EN(0x04) = 0x000c;
all counters 0; v6_identification = 1. 32-byte aligned MURAM allocation;
word2 in the ENQUEUE param = the MURAM OFFSET returned by fman_pcd_muram_alloc
(same convention as CC tables; create_enque_hm: word |= MURAM_VIRT_TO_PHYS_ADDR
(muram_addr); param->word2 = cpu_to_be32(word)).

The frag_muram_off is filled kernel-side in the production flow_add path
(fman_pcd_get_frag_muram_off(pcd)) so ask.ko never needs a MURAM handle -- it
only supplies frag_bpid (the egress port's seeded DPAA RX pool bpid). Both are
gated on vlan->flags != 0 && frag_bpid != 0; else the record keeps bpid=0/
word2=0 exactly as before -> routed and NAT records BYTE-IDENTICAL.

Layout note: iowrite32be(0x000c0000, vb + 0) puts 0x000c in bytes[0:2]
(frag_options, be16) and 0x0000 in bytes[2:4] (pad, be16) -- correct.

Depends on F-198 (ENQUEUE param at enqueue_off) and F-233 (vlan params struct
+ VLAN emitter branch + production caller build). Count-gated, idempotent
(marker "F-234"); hard-fail on drift. Runs after F-233.
"""

import sys

SRC = "drivers/net/ethernet/freescale/fman/fman_pcd.c"


def replace(path, desc, old, new):
    with open(path) as f:
        s = f.read()
    if new in s:
        print(f"### F-234: already applied ({desc})")
        return
    n = s.count(old)
    if n != 1:
        print(f"### F-234: FATAL: {desc}: expected 1 match, got {n}")
        sys.exit(1)
    with open(path, "w") as f:
        f.write(s.replace(old, new, 1))
    print(f"### F-234: {desc} applied")


with open(SRC) as f:
    _src = f.read()

# Ordering guards: F-198 ENQUEUE param and F-233 VLAN params must be present.
if "param_end = enqueue_off + 16;" not in _src:
    print("### F-234: FATAL: F-198 ENQUEUE param block absent (F-198 must precede F-234)")
    sys.exit(1)
if "struct fman_pcd_vlan_params *vlan" not in _src:
    print("### F-234: FATAL: F-233 VLAN params absent (F-233 must precede F-234)")
    sys.exit(1)
if "F-234" in _src:
    print("### F-234: already applied")
    sys.exit(0)

# ---- 1. Static frag-info-block offset + lazy allocator/init helper ----------
# Inserted immediately before the base-tree fman_pcd_fe_pool_alloc().
replace(
    SRC, "frag-info MURAM allocator helper",
    "static int fman_pcd_fe_pool_alloc(struct fman_pcd *pcd)\n"
    "{\n",
    "/*\n"
    " * F-234 (T-M6-8): cdx-style MURAM frag-info block for the FE-VM VLAN\n"
    " * L2-rebuild path. Allocated+initialised once on first VLAN flow; the\n"
    " * cached MURAM offset becomes the ENQUEUE param.word2 of VLAN records.\n"
    " * 0 = not yet allocated (or allocation failed -> VLAN record keeps\n"
    " * word2=0, no worse than before).\n"
    " */\n"
    "static unsigned long fman_pcd_frag_muram_off;\n"
    "\n"
    "static u32 fman_pcd_get_frag_muram_off(struct fman_pcd *pcd)\n"
    "{\n"
    "\tstruct muram_info *muram;\n"
    "\tvoid __iomem *vb;\n"
    "\tunsigned long off;\n"
    "\n"
    "\tif (fman_pcd_frag_muram_off)\n"
    "\t\treturn (u32)fman_pcd_frag_muram_off;\n"
    "\n"
    "\tmuram = fman_get_muram(pcd->fman);\n"
    "\tif (!muram)\n"
    "\t\treturn 0;\n"
    "\n"
    "\t/* 32-byte aligned, cdx_ucode_frag_info_t is 24 B. */\n"
    "\toff = fman_pcd_muram_alloc(pcd, 32);\n"
    "\tif (IS_ERR_VALUE(off))\n"
    "\t\treturn 0;\n"
    "\n"
    "\tvb = (void __iomem *)fman_muram_offset_to_vbase(muram, off);\n"
    "\tif (!vb) {\n"
    "\t\tfman_pcd_muram_free(pcd, off, 32);\n"
    "\t\treturn 0;\n"
    "\t}\n"
    "\n"
    "\t/* frag_options(be16 0x000c) | pad(be16 0) packed as one be32. */\n"
    "\tiowrite32be(0x000c0000, vb + 0);\n"
    "\tiowrite32be(0, vb + 4);\t\t/* alloc_buff_failures */\n"
    "\tiowrite32be(0, vb + 8);\t\t/* v4_frames_counter */\n"
    "\tiowrite32be(0, vb + 12);\t/* v6_frames_counter */\n"
    "\tiowrite32be(0, vb + 16);\t/* v4_frags_counter */\n"
    "\tiowrite32be(0, vb + 20);\t/* v6_frags_counter */\n"
    "\tiowrite32be(1, vb + 24);\t/* v6_identification = 1 */\n"
    "\n"
    "\tfman_pcd_frag_muram_off = off;\n"
    "\treturn (u32)off;\n"
    "}\n"
    "\n"
    "static int fman_pcd_fe_pool_alloc(struct fman_pcd *pcd)\n"
    "{\n",
)

# ---- 2. VLAN-only bpid + word2 override of the shared ENQUEUE param ---------
# The F-198 shared block wrote bpid=0 @enqueue_off+3 and word2=0 @enqueue_off+12.
# After it, override BOTH on VLAN records carrying a valid egress bpid + frag
# block. `vlan` is in scope (F-233 signature param). Routed/NAT: vlan==NULL or
# vlan->flags==0 -> untouched (byte-identical).
replace(
    SRC, "VLAN bpid/word2 override after ENQUEUE param",
    "\t\t*(__be32 *)(r + enqueue_off + 12) = cpu_to_be32(0);\n"
    "\t\tparam_end = enqueue_off + 16;\n",
    "\t\t*(__be32 *)(r + enqueue_off + 12) = cpu_to_be32(0);\n"
    "\t\t/* F-234 (T-M6-8): VLAN L2-rebuild records need a real BMan pool\n"
    "\t\t * (bpid) plus a MURAM frag-info block pointer (word2) so the\n"
    "\t\t * FE-VM STRIP_ETH+INSERT_L2 path can acquire rebuild buffers.\n"
    "\t\t * Routed/NAT (vlan NULL or vlan->flags==0) keep bpid=0/word2=0\n"
    "\t\t * -> byte-identical. Both must be non-zero or neither is written. */\n"
    "\t\tif (vlan && vlan->flags && vlan->frag_bpid &&\n"
    "\t\t    vlan->frag_muram_off) {\n"
    "\t\t\t*(r + enqueue_off + 3) = vlan->frag_bpid;\n"
    "\t\t\t*(__be32 *)(r + enqueue_off + 12) =\n"
    "\t\t\t\tcpu_to_be32(vlan->frag_muram_off);\n"
    "\t\t}\n"
    "\t\tparam_end = enqueue_off + 16;\n",
)

# NOTE (reversibility): the 32-byte frag-info block is allocated lazily on the
# first VLAN flow and intentionally persists for the FMan-PCD instance lifetime
# (one instance, module lifetime). It is NOT freed on per-port disengage because
# the fman_pcd_fe_singletons_free() anchor is non-unique (two definitions, four
# matches) and a fragile count-gated edit there is not worth it for this
# diagnostic build. Net effect: pcd-snapshot "used" shows +32 B after the first
# VLAN engage until module unload. If the datapath validates, productize by
# folding the alloc into the FE singleton build/free lifecycle. This does not
# affect routed/NAT reversibility (they never allocate it).

print("### F-234: MURAM frag-info block + VLAN bpid/word2 applied (routed/NAT byte-identical)")

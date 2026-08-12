"""F-182: E20 corrected track, step 1 — ehash DDR record bug fixes.

CONTEXT (2026-08-12, E20 / decomp/experiments.md): F-181's first silicon
test (image 2026.08.11-1752-rolling, .185, cold-booted) stalled port 0x11
on the first dispatched frame — the stall was a DISPATCH confound (bare
FE_ENTER root + AC_CC engage, fixed by F-183), but the DDR record dump
@0x81c42000 exposed THREE record bugs independent of dispatch:

1. OPCODE CLOBBER: F-175's per-flow ctx pointer write (8B be64 at
   8+align8(keysize)) lands at the SAME offset as F-181's opcode slot
   (8+ALIGN(keysize,4)) — for keysize 14 both are 24. F-175's block sits
   AFTER F-181's block in source order, so the ctx be64 overwrote the 0x01
   ENQUEUE_PKT opcode byte with the DMA pointer's MSB (dump: opcode[24]=
   0x00, [24..31]=0x0000000081c43000). The FE-VM HIT walk would read
   opcode 0x00 — no action script to execute. FIX: relocate the ctx
   pointer past the opcode slot (16B area) and the param block (16B):
   fe_ptr_off = FMAN_EHASH_FLOW_KEY_OFF + ALIGN(keysize,4) + 32 (= 56 for
   keysize 14; the record is 256B and the FE-VM walks nothing past the
   param block, so the new home is invisible to the walker).

2. WRONG fqid SOURCE: F-181's param.fqid field carried the ENQ FE MURAM
   offset (dump: [44..47]=0x00055f00) — the fixup's param-name fallback
   resolved __FQID_SRC__ to enq_off. Vendor writes the flow's actual
   target FQID there (cdx create_enque_hm: param->fqid =
   cpu_to_be32(info->l2_info.fqid)). FIX: write the add_key fqid
   parameter (5th argument since F-175's signature retype).

3. SET_STATS_ENABLE on a 256B record: vendor sets STATS_EN only with the
   320B ext entry (stats block lives at +256) AND UPDATE_STATS in the
   hashfe word; we have neither, so the bit can only ever be inert-or-
   overflow. pkt_count was never a valid discriminator (E20 confound #3;
   the M3 gate mandates the fe_obs canary, patch 0169). FIX: clear it.

Anchored on the exact derived state (F-175 + F-181 outputs). Idempotent
(marker "F-182:"). CI-only build.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

if "F-182:" in src:
    print("### F-182 already applied")
    sys.exit(0)

changes = 0


def apply_block(name, old, new):
    global src, changes
    if old not in src:
        print(f"### F-182: FATAL: '{name}' text not found verbatim -- "
              "source drifted (F-175/F-181 outputs changed?). Refusing to guess.")
        sys.exit(1)
    src = src.replace(old, new, 1)
    changes += 1
    print(f"### fman_pcd.c: F-182 {name} applied")


# --- 1. Clear SET_STATS_ENABLE (256B record; vendor sets it only with the
#        320B ext entry + UPDATE_STATS). Anchor spans F-181's block comment
#        through the flags declaration so the comment stays accurate.
old_flags = (
    "\t/* F-181: vendor opcode-script record -- flags carry opc/param offsets\n"
    "\t * (SET_OPC_OFFSET/SET_PARAM_OFFSET); STATS_EN bit preserves F-176.  The\n"
    "\t * FE-VM reads opc_offset on a HIT, walks the opcode list, and ENQUEUEs\n"
    "\t * to enqueue_param.fqid -- without this there is no action script to\n"
    "\t * walk and the comparator can never complete a HIT.\n"
    "\t */\n"
    "\t{\n"
    "\t\tsize_t opc_off = FMAN_EHASH_FLOW_KEY_OFF + ALIGN(key_size, sizeof(u32));\n"
    "\t\tsize_t param_off = opc_off + 16;\t/* MAX_OPCODES */\n"
    "\t\tu16 flags = (1U << 12);\t\t/* SET_STATS_ENABLE */\n"
)
new_flags = (
    "\t/* F-181: vendor opcode-script record -- flags carry opc/param offsets\n"
    "\t * (SET_OPC_OFFSET/SET_PARAM_OFFSET).  The FE-VM reads opc_offset on a\n"
    "\t * HIT, walks the opcode list, and ENQUEUEs to enqueue_param.fqid --\n"
    "\t * without this there is no action script to walk and the comparator\n"
    "\t * can never complete a HIT.\n"
    "\t * F-182: STATS_EN cleared -- vendor sets it only with the 320B ext\n"
    "\t * entry (stats at +256) plus UPDATE_STATS in the hashfe word; we have\n"
    "\t * neither, and pkt_count was never a valid discriminator anyway (E20\n"
    "\t * confound #3; the M3 gate is the fe_obs canary, patch 0169).\n"
    "\t */\n"
    "\t{\n"
    "\t\tsize_t opc_off = FMAN_EHASH_FLOW_KEY_OFF + ALIGN(key_size, sizeof(u32));\n"
    "\t\tsize_t param_off = opc_off + 16;\t/* MAX_OPCODES */\n"
    "\t\tu16 flags = 0;\t/* F-182: no STATS_EN (256B record) */\n"
)
apply_block("STATS_EN cleared", old_flags, new_flags)

# --- 2. param.fqid = the flow's target FQID, not the ENQ FE MURAM offset.
old_fqid = (
    "\t\t*(__be32 *)(r + param_off + 4) = cpu_to_be32((u32)enq_off & 0x00ffffff);\t/* fqid */\n"
)
new_fqid = (
    "\t\t/* F-182: param.fqid = the flow's target FQID (vendor cdx\n"
    "\t\t * create_enque_hm: param->fqid = cpu_to_be32(l2_info.fqid)) --\n"
    "\t\t * NOT the ENQ FE MURAM offset F-181 v1 wrote here by mistake.\n"
    "\t\t */\n"
    "\t\t*(__be32 *)(r + param_off + 4) = cpu_to_be32((u32)fqid & 0x00ffffff);\t/* fqid */\n"
)
apply_block("param.fqid = target FQID", old_fqid, new_fqid)

# --- 3. Relocate F-175's ctx pointer off the opcode slot.
old_ctx = (
    "\t\tsize_t fe_ptr_off = FMAN_EHASH_FLOW_KEY_OFF +\n"
    "\t\t\t\t     ((key_size + 7U) & ~7U);\n"
)
new_ctx = (
    "\t\t/* F-182: relocate the ctx pointer past F-181's opcode slot\n"
    "\t\t * (ALIGN(keysize,4) after the key) + the 16B opcode area + the\n"
    "\t\t * 16B param block. The old 8+align8(keysize) offset EQUALLED\n"
    "\t\t * opc_off for keysize 14 (both 24) and this be64 write clobbered\n"
    "\t\t * the ENQUEUE_PKT opcode byte (E20 DDR dump: opcode[24]=0x00,\n"
    "\t\t * [24..31]=ctx_dma). New home (56 for keysize 14, 80 for the\n"
    "\t\t * 37B IPv6 key) stays inside the 256B record, past everything\n"
    "\t\t * the FE-VM walks.\n"
    "\t\t */\n"
    "\t\tsize_t fe_ptr_off = FMAN_EHASH_FLOW_KEY_OFF +\n"
    "\t\t\t\t     ALIGN(key_size, sizeof(u32)) + 16 + 16;\n"
)
apply_block("ctx pointer relocated past opcode+param", old_ctx, new_ctx)

with open(path, "w") as f:
    f.write(src)

print(f"### fman_pcd.c: F-182 complete ({changes} blocks)")

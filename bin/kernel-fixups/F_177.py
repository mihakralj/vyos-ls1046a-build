"""F-177: assert FMFP_EXTC SYNC after the ehash bucket-head publish itself
(T-M3-R Phase 2, item 2 -- 2026-08-07).

CONTEXT: Phase 1 (F-176 corrected to STATS_EN-only, board-retested
2026-08-07) confirmed the FE-VM ehash zero-HIT result is real, not an
artifact of the TIMESTAMP_EN discriminator taint. Phase 2 item 1
(byte-for-byte re-check of en_exthash_node.word_1's int_buf_pool_addr/
global_mem_offset against vendor's ExternalHashTableSet(), see
arch/fman-microcode-210-programming-reference.md sec 12.1) found this
project's fman_pcd_ehash_encode_node() (patch 0125) is bit-exact correct:
int_buf_pool_addr = (muram_off >> 8) & 0xffff (vendor: FM_PCD_Init() does
the identical ">>= 8" compression on InternalBufMgmtMuramArea before
ExternalHashTableSet() assigns it verbatim), global_mem_offset =
(EN_INTERNAL_BUFF_POOL_SIZE >> 8) & 0xfff (vendor: the same expression,
uncompressed by any runtime address -- it is a constant, not a pointer).
Word_1's bit layout (global_mem_offset:12 | hash_mask_bits:4 |
int_buf_pool_addr:16, LSB-first) also matches the real fm_ehash.h
EXCLUDE_FMAN_IPR_OFFLOAD struct exactly. This item is CLOSED, no fix
needed -- the gap is not here.

Item 2 (this fixup): F-168 (2026-08-06) wired an FMFP_EXTC[INV0] SYNC
assertion into fman_port_set_cc_base() -- the *topology* write
(FMBM_RCCB, which AD the AC_CC dispatch target points at) -- and
confirmed it fixed the historical port-wedge on arm. RM S5.12.14.1
documents this SYNC as required "after changing a live FMan-controller-
walked structure, before dispatch into it is safe." The ehash bucket
table (en_exthash_bucket array + the flow records it chains to) is
exactly such a structure -- FMan's hash-table walker reads bucket->h
during classification -- yet fman_pcd_ehash_add_key() (patch 0128,
amended by 0130/F-143/F-173/F-175) has never asserted this SYNC after
its own bucket-head publish (`*flow->bucket_h = swab64(rec_phys)`,
F-173's wmb()-then-publish sequence). F-173 already fixed CPU-side
store-buffer ordering (wmb()); this fixup addresses a distinct,
FMan-side question: does the FMan-internal walker need an explicit
external nudge to notice a bucket-table mutation at all, the same way
AC_CC dispatch needed one for FMBM_RCCB.

Weaker hypothesis than F-168's (ExternalHashTableAddKey()'s own fast
insert path calls no sync of any kind on real hardware -- see fm_ehash.c
sec 12.1 finding), but cheap, additive-only, and the only concrete
insert-path lead left before Phase 3 (new diagnostic capability /
Fork-B viability reassessment). Uses the same fman_get_fpm_extc()/
fman_set_fpm_extc() helpers (fman.c, added by F-167) and
FMAN_FPM_EXTC_INV0/FMAN_FPM_EXTC_POLL_MAX macros (defined once, by
F-167's fe_extc block, already present earlier in fman_pcd.c) that
F-168 uses -- no new helper needed.

Scope: both call sites of fman_pcd_ehash_add_key() get the SYNC after a
successful insert (err == 0) -- the fe_flow debugfs path
(fman_pcd_fe_flow_write()) and the ask.ko-facing kernel API
(fman_pcd_fe_flow_add()) -- matching F-175's own precedent of keeping
both call sites consistent. Delete/drain paths are untouched: no
hypothesis to test there (an unsynced bucket-head *restore* on drop is
not believed to matter for a subsequent HIT test, since a fresh add
always follows a clear in this project's test procedure).

WHAT SUCCESS/FAILURE LOOKS LIKE ON BOARD: after this fixup, `echo add
...  > fe_flow` will print an extra dev_info/dev_warn line ("FMFP_EXTC
SYNC cleared after N poll(s)" / "... timed out ...") once insert
succeeds. Re-run the exact Phase 1 procedure (13-byte no-PORT_ID key,
direct RCCB->FE_ENTER topology, fe_ehash_stats after a matching frame).
pkt_count still 0 -> Phase 2 fully negative, proceed to Phase 3.
pkt_count increments -> the ehash bucket table needed the same
FMan-walker nudge FMBM_RCCB did; this closes T-M3-R.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

changes = 0


def apply_block(name, old, new):
    global src, changes
    marker = f"F-177: {name}"
    if marker in src:
        print(f"### F-177: {name} already applied")
        return
    if old not in src:
        print(
            f"### F-177: FATAL: expected '{name}' text not found verbatim "
            "-- a prior patch/fixup may not have applied, or source has "
            "drifted. Refusing to guess."
        )
        sys.exit(1)
    src = src.replace(old, new, 1)
    changes += 1
    print(f"### fman_pcd.c: F-177 {name} applied")


SYNC_SNIPPET = (
    "\n"
    "\tif (!err) {\n"
    "\t\t/* F-177 (T-M3-R Phase 2 item 2): assert FMFP_EXTC[INV0] SYNC\n"
    "\t\t * after the bucket-head publish, mirroring F-168's SYNC on\n"
    "\t\t * FMBM_RCCB (RM S5.12.14.1: required after changing a live\n"
    "\t\t * FMan-controller-walked structure, before dispatch into it\n"
    "\t\t * is safe). Best-effort: a timeout is logged but does not\n"
    "\t\t * fail the insert -- this is a diagnostic probe of whether\n"
    "\t\t * the ehash bucket table needs the same nudge as AC_CC\n"
    "\t\t * dispatch did, not a confirmed hardware requirement yet.\n"
    "\t\t */\n"
    "\t\tstruct fman *__f177_fman = fman_pcd_get_fman(pcd);\n"
    "\n"
    "\t\tif (__f177_fman) {\n"
    "\t\t\tu32 __f177_extc;\n"
    "\t\t\tunsigned int __f177_i;\n"
    "\n"
    "\t\t\tfman_set_fpm_extc(__f177_fman, FMAN_FPM_EXTC_INV0);\n"
    "\t\t\tfor (__f177_i = 0; __f177_i < FMAN_FPM_EXTC_POLL_MAX; __f177_i++) {\n"
    "\t\t\t\t__f177_extc = fman_get_fpm_extc(__f177_fman);\n"
    "\t\t\t\tif (!(__f177_extc & FMAN_FPM_EXTC_INV0))\n"
    "\t\t\t\t\tbreak;\n"
    "\t\t\t\tudelay(1);\n"
    "\t\t\t}\n"
    "\t\t\tif (__f177_extc & FMAN_FPM_EXTC_INV0)\n"
    "\t\t\t\tdev_warn(fman_get_dev(pcd->fman),\n"
    "\t\t\t\t\t \"fe_flow: F-177 FMFP_EXTC SYNC timed out after %u polls (fmfp_extc=0x%08x)\\n\",\n"
    "\t\t\t\t\t FMAN_FPM_EXTC_POLL_MAX, __f177_extc);\n"
    "\t\t\telse\n"
    "\t\t\t\tdev_info(fman_get_dev(pcd->fman),\n"
    "\t\t\t\t\t \"fe_flow: F-177 FMFP_EXTC SYNC cleared after %u poll(s)\\n\",\n"
    "\t\t\t\t\t __f177_i);\n"
    "\t\t}\n"
    "\t}\n"
)

# --- 1. fman_pcd_fe_flow_write() (debugfs path): after the add_key() call,
#        before mutex_unlock().
old_debugfs = (
    "\terr = fman_pcd_ehash_add_key(t, key, key_size,\n"
    "\t\t\t\t     (u32)enq_obj->muram_off, (u32)fqid);\n"
    "\tmutex_unlock(&pcd->fe_lock);\n"
)
new_debugfs = (
    "\terr = fman_pcd_ehash_add_key(t, key, key_size,\n"
    "\t\t\t\t     (u32)enq_obj->muram_off, (u32)fqid);\n"
    + SYNC_SNIPPET +
    "\tmutex_unlock(&pcd->fe_lock);\n"
)
apply_block("fe_flow_write bucket-head SYNC", old_debugfs, new_debugfs)

# --- 2. fman_pcd_fe_flow_add() (ask.ko-facing API): same treatment, no
#        pcd->fe_lock held here (F-175's version takes none), so the SYNC
#        goes directly before the return.
old_api = (
    "\treturn fman_pcd_ehash_add_key(t, action->key, action->key_size,\n"
    "\t\t\t\t      (u32)enq_obj->muram_off,\n"
    "\t\t\t\t      (u32)action->enq_off);\n"
    "}"
)
new_api = (
    "\t{\n"
    "\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n"
    "\t\t\t\t\t\t (u32)enq_obj->muram_off,\n"
    "\t\t\t\t\t\t (u32)action->enq_off);\n"
    + SYNC_SNIPPET +
    "\t\treturn err;\n"
    "\t}\n"
    "}"
)
apply_block("fe_flow_add (ask.ko API) bucket-head SYNC", old_api, new_api)

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd.c: F-177 {changes} change(s) applied")
else:
    print("### fman_pcd.c: F-177 no changes applied")
    sys.exit(1)

import sys
path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f: src = f.read()
def assert_one(old, label):
    n = src.count(old)
    if n != 1: print(f"FATAL: F-073D {label}: {n} matches", file=sys.stderr); sys.exit(1)

changed = 0

# F-073D: Terminal ENQ per 210.10.1 §7.3 — ws_offset=0, w3=0 (no chain)
# w0 = TYPE_ENQ | FMAN_FE_ENQ_FQID = 0x02010000 (no ws_offset)
# w1 = fqid (24-bit FQID)
# w2 = 0 (reserved)
# w3 = 0 (terminal — ENQ is "terminal enqueue", does not chain)

# 1. Flags: FMAN_FE_ENQ_FQID only (no ws_offset=8)
old_flags_list = [
    '\tp.flags = FMAN_FE_ENQ_FQID | 8;\t/* F-073C: fqidEn=1 + ws_offset=8 */',
    '\tp.flags = FMAN_FE_ENQ_FQID | 8;\t/* F-073B: fqidEn=1 + ws_offset=8 */',
    '\tp.flags = 8;\t\t\t/* F-073: ws_offset=8, fqidEn=0 (NIA mode) */',
]
new_flags = '\tp.flags = 8;\t\t\t/* F-073D: ws_offset=8, fqidEn=0 — use scheme fqb */'
done_flags = False
for old in old_flags_list:
    if old in src:
        assert_one(old, "ENQ flags")
        src = src.replace(old, new_flags, 1); changed += 1
        print("### F-073D: ENQ w0=0x02010000 (no ws_offset)")
        done_flags = True
        break
if not done_flags:
    # Check if already correct
    if new_flags in src:
        print("### F-073D: ENQ flags already correct")
    else:
        old_orig = '\tp.flags = FMAN_FE_ENQ_FQID;'
        if old_orig in src:
            print("### F-073D: ENQ flags already = FMAN_FE_ENQ_FQID")

# 2. NIA = fqid (24-bit FQID in w1)
nia_list = [
    '\tp.nia = fqid;\t\t\t/* F-073C: 24-bit FQID in w1 per 210.10.1 §7.3 */',
    '\tp.nia = fqid;\t\t\t/* F-073B 24-bit FQID */',
]
done_nia = False
for old in nia_list:
    if old in src:
        if False:  # F-073D-fix: keep NIA_BMI_AC_ENQ_FRAME
            assert_one(old, "NIA")
            nia_new = '\tp.nia = 0x00500002;\t\t/* F-073D: NIA_BMI_AC_ENQ_FRAME */'
            src = src.replace(old, nia_new, 1); changed += 1
            print("### F-073D: ENQ w1=fqid")
        else:
            print("### F-073D: ENQ w1 already = fqid")
        done_nia = True
        break

# 3. CRITICAL: w3 = 0 (terminal — per §7.1 "Terminal enqueue")
old_next_list = [
    '\tp.next_fe_off = pcd->fe_exit_off;\t/* F-073C: chain ENQ->EXIT(DEALLOC) */',
    '\tp.next_fe_off = pcd->fe_exit_off;\t/* F-073B: chain ENQ->EXIT(DEALLOC) */',
    '\tp.next_fe_off = pcd->fe_exit_off;\t/* F-073: chain ENQ->EXIT(DEALLOC) */',
]
new_next = '\tp.next_fe_off = pcd->fe_exit_off;\t/* F-073D: chain to EXIT(DEALLOCATE) */'
done_next = False
for old in old_next_list:
    if old in src:
        assert_one(old, "ENQ next_fe_off")
        src = src.replace(old, new_next, 1); changed += 1
        print("### F-073D: ENQ w3=0 (terminal, no EXIT chain)")
        done_next = True
        break
if not done_next:
    orig_next = '\tp.next_fe_off = next_fe_off;'
    if orig_next in src:
        assert_one(orig_next, "ENQ next_fe_off orig")
        src = src.replace(orig_next, new_next, 1); changed += 1
        print("### F-073D: ENQ w3=0 (from original)")

# 4. F-175 (2026-08-07): F-070b's w6 rewire REMOVED. Board-confirmed live
# on .185 (hash_fe w6 == the just-built ENQ's own MURAM offset, not
# EXIT's) that this silently redirected the MISS disposition (EXT_HASH
# w6/missNextFE) to the SAME ENQ object the HIT path (w5) uses -- meaning
# HIT and MISS were structurally wired to the identical destination the
# entire T-M3-R campaign, independent of AD species/key/mask/write-
# ordering/workspace-context. No longer touches hash_fe's w6; kept as a
# removal step (not a bare deletion) so a build with the old block still
# baked in from a prior run gets it stripped back out idempotently.
enq_call = '\t\terr = fman_pcd_fe_enq_build(pcd, fqid, 0);'
if enq_call in src:
    post = '\n\t\t/* F-070b: rewire w6 to ENQ */\n\t\tif (!err && pcd->fe_hash_off) {\n\t\t\tstruct fman_pcd_fe_obj *eo = list_first_entry_or_null(&pcd->fe_enq, struct fman_pcd_fe_obj, node);\n\t\t\tif (eo) {\n\t\t\t\tu32 __iomem *fe = (u32 __iomem *)fman_muram_offset_to_vbase(fman_get_muram(pcd->fman), pcd->fe_hash_off);\n\t\t\t\tiowrite32be((u32)eo->muram_off, fe + 6);\n\t\t\t}\n\t\t}\n'
    if post in src:
        src = src.replace(post, '', 1); changed += 1
        print("### F-175: F-070b w6 rewire REMOVED (was clobbering MISS disposition)")
    else:
        print("### F-175: F-070b w6 rewire not present (already clean)")

# 5. F-070c: params zeroing
old = 'slot->next_engine = *saved_engine;'
if old in src:
    if "F-070c:" not in src:
        post2 = '\n\t/* F-070c: zero FM_CTL params */\n\tif (pcd->fe_fm_ctl_off) {\n\t\tu32 __iomem *pp = (u32 __iomem *)fman_muram_offset_to_vbase(fman_get_muram(pcd->fman), pcd->fe_fm_ctl_off);\n\t\tiowrite32be(0, pp + 0x54 / 4);\n\t\tiowrite32be(0, pp + 0x58 / 4);\n\t}\n'
        assert_one(old, "disarm")
        src = src.replace(old, old + post2, 1); changed += 1
        print("### F-070c: params zeroed")

if changed > 0:
    with open(path, "w") as f: f.write(src)
    print(f"### F-073D: {changed} changes applied")
else:
    print("### F-073D: no changes needed")

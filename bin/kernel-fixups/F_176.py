"""F-176: ehash per-entry hardware-writeback stats/timestamp readback.

Phase 1 of plans/EHASH-DUAL-FIX-VERIFICATION-PLAN.md, following Phase 0's
CORRECTED verdict (2026-08-07): this project's ehash bucket/record DDR
format is already bit-exact correct against the real vendor mechanism
(`ExternalHashTableAddKey()` / `en_exthash_bucket` / `en_exthash_node` /
`en_ehash_entry`, we-are-mono/ASK mt-6.12.y). No format fix is needed. What
IS new: `en_ehash_entry` is a union whose second view exposes
hardware-writeback `packet_count`(8B)/`packet_bytes`(8B)/`timestamp`(4B)
counters starting at offset 256, gated by `SET_STATS_ENABLE`(bit12)/
`SET_TIMESTAMP_ENABLE`(bit13) on the entry's own 16-bit `flags` word, with
entries sized 320B (`MAX_EN_EHASH_EXT_ENTRY_SIZE`) instead of 256B
(`MAX_EN_EHASH_ENTRY_SIZE`) to hold them.

Phase 1 correction (2026-08-07, same day as the first version): the
original version of this fixup set flags = 0x3000 (STATS_EN|TIMESTAMP_EN).
A full read of the real vendor source (arch/fman-microcode-210-programming-
reference.md §12.1) found vendor forces TIMESTAMP_EN on every key
unconditionally too -- but backed by a live, periodically-refreshed 4-slot
MURAM pool (`extHashTsInfo`, FM_PCD_Init()) that a userspace timer
(cdx/cdx_timer.c) keeps alive, entirely outside sdk_fman. This branch
implements neither the pool nor the timer. Setting TIMESTAMP_EN without
that backing infrastructure may have tainted the first HIT test run with
this fixup (plans/ASK2-MASTER-PLAN.md T-M3-R attempt 8, 2026-08-07): a
clean pkt_count=0 result is not trustworthy if the flag itself corrupts the
record/comparator rather than merely failing to update stats. This version
sets flags = 0x1000 (STATS_EN only) instead -- packet_count/packet_bytes
are inline in the entry at fixed offsets and, per the same vendor read, do
not depend on any pool the way timestamp_counter does.

This fixup:
  1. Bumps FMAN_EHASH_FLOW_REC_SIZE 256 -> 320 so every allocated flow
     record has room for the stats/timestamp region (unconditional —
     this is a diagnostic build; there is no reason to keep two record
     sizes for a debugfs-driven single/few-flow test harness).
  2. Sets flags = 0x1000 (STATS_EN bit only) instead of 0 on every inserted
     record, unconditionally, for the same reason. TIMESTAMP_EN (bit 13)
     is deliberately NOT set — see the Phase 1 correction above.
  3. Adds a new debugfs node "fe_ehash_stats" (0444) dumping, per inserted
     flow: bucket index, key size, the record's DMA address, the bucket
     head pointer AS IT STANDS NOW (re-read at dump time, not cached —
     this is Phase 3.2's "is the head pointer still what we wrote"
     sanity check), and the three hardware-writeback fields. This is a
     dispatch/FQID-independent discriminator: if packet_count increments
     after a matching test frame, hardware genuinely performed a compare
     at this entry regardless of what happens downstream.

Anchors are chosen for whitespace-tolerance (regex, not exact multi-tab
literal matches) because the current merged state of fman_pcd.c cannot be
verified by compiling locally (standing constraint: CI is the only compile
path) — see plans/EHASH-DUAL-FIX-VERIFICATION-PLAN.md Phase 1.2. Every
anchor is a short, semantically unique substring confirmed present, verbatim
or in obviously-stable form, in patches 0125/0128/0130 (the base ehash
patches, never touched by any later fixup's diff hunks per full audit of
F-057/F-142/F-143/F-145/F-149/F-172/F-173/F-175).
"""
import re
import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

changed = 0


def sub_one(pattern, repl, text, label, flags=0):
    new_text, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if n != 1:
        print(f"FATAL: F-176 {label}: pattern not found (expected 1 match)",
              file=sys.stderr)
        sys.exit(1)
    return new_text


# ── 1. Bump FMAN_EHASH_FLOW_REC_SIZE 256 -> 320 ──
if "F-176: 320B" in src:
    print("### F-176: FMAN_EHASH_FLOW_REC_SIZE already bumped")
else:
    src = sub_one(
        r'(#define\s+FMAN_EHASH_FLOW_REC_SIZE\s+)256(\s*/\*[^\n]*\*/)',
        r'\g<1>320\2\t/* F-176: 320B = MAX_EN_EHASH_EXT_ENTRY_SIZE, room for stats */',
        src, "FMAN_EHASH_FLOW_REC_SIZE 256->320",
    )
    changed += 1
    print("### F-176: FMAN_EHASH_FLOW_REC_SIZE bumped 256 -> 320")

# ── 2. flags = 0 -> flags = 0x1000 (STATS_EN only, NOT TIMESTAMP_EN) ──
# Phase 1 correction: TIMESTAMP_EN (bit 13) requires a backing MURAM pool
# (vendor's extHashTsInfo) this branch doesn't implement -- see docstring.
if "F-176: STATS_EN only" in src:
    print("### F-176: flags already set to STATS_EN only")
elif "0x3000" in src and "STATS_EN|TIMESTAMP_EN (bits 12/13)" in src:
    # An earlier build of this fixup already ran and set 0x3000 -- correct
    # it in place rather than skipping (idempotent re-run on a tree that
    # already has the old, tainted value).
    src = sub_one(
        r'(\*\(__be16 \*\)\(r \+ 0\) = cpu_to_be16\()0x3000(\);\t/\* F-176: STATS_EN\|TIMESTAMP_EN \(bits 12/13\) \*/)',
        r'\g<1>0x1000);\t/* F-176: STATS_EN only (bit 12) -- TIMESTAMP_EN dropped, Phase 1 correction */',
        src, "record flags 0x3000 -> 0x1000 (correcting prior taint)",
    )
    changed += 1
    print("### F-176: record flags corrected 0x3000 -> 0x1000 (STATS_EN only)")
else:
    src = sub_one(
        r'(\*\(__be16 \*\)\(r \+ 0\) = cpu_to_be16\()0(\);[^\n]*)',
        r'\g<1>0x1000\2\t/* F-176: STATS_EN only (bit 12) */',
        src, "record flags 0 -> 0x1000",
    )
    changed += 1
    print("### F-176: record flags set to 0x1000 (STATS_EN only)")

# ── 3. New fe_ehash_stats debugfs node ──
STATS_SHOW_BLOCK = '''
/*
 * F-176: dispatch/FQID-independent HIT discriminator. Dumps, per inserted
 * ehash flow, the hardware-writeback stats fields (packet_count/
 * packet_bytes valid once F-176's flags=0x1000/STATS_EN has been armed and
 * a matching frame has been sent -- timestamp intentionally not populated,
 * see the Phase 1 correction in this file's module docstring) plus the
 * bucket head pointer re-read live (not cached) so a stale/overwritten
 * head is visible without a separate probe.
 */
static int fman_pcd_fe_ehash_stats_show(struct seq_file *s, void *unused)
{
	struct fman_pcd *pcd = s->private;
	struct fman_pcd_ehash_table *t;
	struct fman_pcd_ehash_flow *flow;
	unsigned int ti = 0;

	mutex_lock(&pcd->fe_lock);
	list_for_each_entry(t, &pcd->fe_ehash_tables, node) {
		list_for_each_entry(flow, &t->flows, node) {
			const u8 *r = flow->record;
			u64 head_now = *flow->bucket_h;
			u64 pkt_count = be64_to_cpu(*(const __be64 *)(r + 256));
			u64 pkt_bytes = be64_to_cpu(*(const __be64 *)(r + 264));
			u32 ts = be32_to_cpu(*(const __be32 *)(r + 272));

			seq_printf(s,
				   "tbl[%u] idx=%u keysz=%u record_dma=0x%llx bucket_head_now=0x%016llx pkt_count=%llu pkt_bytes=%llu timestamp=0x%08x\\n",
				   ti, flow->index, flow->key_size,
				   (unsigned long long)flow->record_dma,
				   (unsigned long long)head_now,
				   (unsigned long long)pkt_count,
				   (unsigned long long)pkt_bytes,
				   ts);
		}
		ti++;
	}
	mutex_unlock(&pcd->fe_lock);
	return 0;
}

static int fman_pcd_fe_ehash_stats_open(struct inode *inode, struct file *file)
{
	return single_open(file, fman_pcd_fe_ehash_stats_show, inode->i_private);
}

static const struct file_operations fman_pcd_fe_ehash_stats_fops = {
	.owner		= THIS_MODULE,
	.open		= fman_pcd_fe_ehash_stats_open,
	.read		= seq_read,
	.llseek		= seq_lseek,
	.release	= single_release,
};

'''

if "fman_pcd_fe_ehash_stats_show" in src:
    print("### F-176: fe_ehash_stats show/open/fops already present")
else:
    anchor_re = r'static int fman_pcd_fe_flow_open\(struct inode \*inode, struct file \*file\)'
    m = re.search(anchor_re, src)
    if not m:
        print("FATAL: F-176: fman_pcd_fe_flow_open anchor not found",
              file=sys.stderr)
        sys.exit(1)
    src = src[:m.start()] + STATS_SHOW_BLOCK + src[m.start():]
    changed += 1
    print("### F-176: fe_ehash_stats show/open/fops inserted")

# ── 4. Register the new debugfs file right after fe_flow's ──
if 'debugfs_create_file("fe_ehash_stats"' in src:
    print("### F-176: fe_ehash_stats debugfs registration already present")
else:
    reg_re = (
        r'([ \t]*)debugfs_create_file\(\s*"fe_flow"\s*,\s*0644\s*,\s*\n'
        r'[ \t]*pcd->debugfs_dir\s*,\s*pcd\s*,\s*\n'
        r'[ \t]*&fman_pcd_fe_flow_fops\)\s*;\n'
    )
    m = re.search(reg_re, src)
    if not m:
        print("FATAL: F-176: fe_flow debugfs_create_file registration not found",
              file=sys.stderr)
        sys.exit(1)
    indent = m.group(1)
    new_reg = (
        f'{indent}debugfs_create_file("fe_ehash_stats", 0444,\n'
        f'{indent}\t\t\t    pcd->debugfs_dir, pcd,\n'
        f'{indent}\t\t\t    &fman_pcd_fe_ehash_stats_fops);\n'
    )
    src = src[:m.end()] + new_reg + src[m.end():]
    changed += 1
    print("### F-176: fe_ehash_stats debugfs node registered")

if changed > 0:
    with open(path, "w") as f:
        f.write(src)
    print(f"### F-176: {changed} change(s) applied")
else:
    print("### F-176: no changes needed")

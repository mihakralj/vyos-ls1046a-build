#!/usr/bin/env python3
"""F-307: lab-only ehash walker probe for the delete SYNC barrier.

FMan 210.10.1 writes packet_count at record+256 on every HIT of a
320-byte ehash record, independent of STATS_EN (qdrant T-M8-3,
2026-08-22). With the root-only countdown ehash_walker_probe > 0,
fman_pcd_ehash_del_key() reads that counter right after unlinking the
record (c0) and again after its SYNC completes (c1), then retains the
record and the replaced cumulative nodes instead of freeing them. No
record or register is written; only reclamation is skipped.

  ehash_walker_probe_inflight  sum(c1 - c0): HITs by lookups that held the
                               old head while the SYNC ran (positive control)
  ehash_walker_probe_late      sum(now - c1), computed on read: HITs on an
                               unlinked record after its SYNC completed;
                               anything but 0 means INV0 is not a barrier

Retained blocks are never freed; a busy dma_pool_destroy() leaks its pages,
so late reads stay valid. Reboot after an experiment. Default 0 = off.
"""

from pathlib import Path
import sys

PCD = Path("drivers/net/ethernet/freescale/fman/fman_pcd.c")
MARK = "F-307"

PARAM_ANCHOR = (
    "module_param_named(ehash_sync_injected, ehash_sync_injected, uint, 0444);\n")
PARAM_NEW = PARAM_ANCHOR + r"""
/* F-307: lab-only walker probe, see bin/kernel-fixups/F_307.py. */
#define FMAN_EHASH_WALKER_PROBE_MAX	4096
#define FMAN_EHASH_REC_PKT_COUNT_OFF	256	/* en_ehash_entry packet_count */
static unsigned int ehash_walker_probe;
static unsigned int ehash_walker_probe_kept;
static unsigned long long ehash_walker_probe_inflight;
static struct {
	const u8 *rec;
	u64 c1;
} ehash_walker_probe_rec[FMAN_EHASH_WALKER_PROBE_MAX];
module_param_named(ehash_walker_probe, ehash_walker_probe, uint, 0600);
module_param_named(ehash_walker_probe_kept, ehash_walker_probe_kept, uint, 0444);
module_param_named(ehash_walker_probe_inflight, ehash_walker_probe_inflight,
		   ullong, 0444);

static u64 fman_pcd_ehash_rec_hits(const u8 *rec)
{
	dma_rmb();
	return be64_to_cpu(READ_ONCE(*(const __be64 *)
				     (rec + FMAN_EHASH_REC_PKT_COUNT_OFF)));
}

/* Caller holds fe_lock and has completed the delete SYNC. */
static bool fman_pcd_ehash_walker_probe_keep(const u8 *rec, u64 c0)
{
	unsigned int n = ehash_walker_probe_kept;
	u64 c1 = fman_pcd_ehash_rec_hits(rec);

	if (n >= FMAN_EHASH_WALKER_PROBE_MAX)
		return false;
	ehash_walker_probe_rec[n].rec = rec;
	ehash_walker_probe_rec[n].c1 = c1;
	ehash_walker_probe_inflight += c1 - c0;
	WRITE_ONCE(ehash_walker_probe, ehash_walker_probe - 1);
	smp_store_release(&ehash_walker_probe_kept, n + 1);
	return true;
}

static int fman_pcd_ehash_walker_probe_late_get(char *buf,
						const struct kernel_param *kp)
{
	unsigned int i, n = smp_load_acquire(&ehash_walker_probe_kept);
	u64 late = 0;

	for (i = 0; i < n; i++)
		late += fman_pcd_ehash_rec_hits(ehash_walker_probe_rec[i].rec) -
			ehash_walker_probe_rec[i].c1;
	return sysfs_emit(buf, "%llu\n", late);
}

static const struct kernel_param_ops fman_pcd_ehash_walker_probe_late_ops = {
	.get = fman_pcd_ehash_walker_probe_late_get,
};
module_param_cb(ehash_walker_probe_late, &fman_pcd_ehash_walker_probe_late_ops,
		NULL, 0444);
"""

DECL_OLD = "\tstruct fman_pcd_ehash_cu *old_cu = NULL;\n\tu32 extc;\n\tint err;\n"
DECL_NEW = DECL_OLD + "\tbool f307_probe = false;\t/* F-307 */\n\tu64 f307_c0 = 0;\n"

UNLINK_OLD = """\
	hlist_del(&x->bnode);	/* F-264 */

	/* F-254: a lookup that read the old head may still be in flight. */
"""
UNLINK_NEW = """\
	hlist_del(&x->bnode);	/* F-264 */

	/* F-307: lab walker probe, HITs counted from the unlink onwards. */
	if (READ_ONCE(ehash_walker_probe)) {
		f307_probe = true;
		f307_c0 = fman_pcd_ehash_rec_hits(x->record);
	}

	/* F-254: a lookup that read the old head may still be in flight. */
"""

TAIL_OLD = """\
	/* The block holds the record and its FE context (F-267). */
	fman_pcd_ehash_cu_free(t, old_cu);
	dma_pool_free(t->rec_pool, x->record, x->record_dma);
	kfree(x);
	return 0;
"""
TAIL_NEW = """\
	/* F-307: retain the record and the replaced nodes; never freed. */
	if (f307_probe && fman_pcd_ehash_walker_probe_keep(x->record, f307_c0)) {
		kfree(old_cu);
		kfree(x);
		return 0;
	}

""" + TAIL_OLD

EDITS = ((DECL_OLD, DECL_NEW), (UNLINK_OLD, UNLINK_NEW), (TAIL_OLD, TAIL_NEW))
HEAD = "static int fman_pcd_ehash_del_key(struct fman_pcd_ehash_table *t,\n\t\t\t\t  const u8 *key, u8 key_size)\n{\n"


def main():
    try:
        src = PCD.read_text()
        if src.count(HEAD) != 1:
            raise ValueError(f"del_key definition: found {src.count(HEAD)}, expected 1")
        start = src.index(HEAD)
        end = src.index("\n}\n", start) + 3
        body = src[start:end]
        done = [new in body for _, new in EDITS] + [PARAM_NEW in src]
        if all(done):
            print(f"### {MARK}: already applied")
            return 0
        if any(done):
            raise ValueError("partially applied")
        if src.count(PARAM_ANCHOR) != 1:
            raise ValueError("F-282 parameter anchor missing or ambiguous")
        for old, new in EDITS:
            if body.count(old) != 1:
                raise ValueError(f"del_key anchor count {body.count(old)}: {old.strip()[:50]!r}")
            body = body.replace(old, new, 1)
        src = src[:start] + body + src[end:]
        src = src.replace(PARAM_ANCHOR, PARAM_NEW, 1)
        PCD.write_text(src)
    except (OSError, ValueError) as error:
        print(f"### {MARK}: FATAL: {error}", file=sys.stderr)
        return 1
    print(f"### {MARK}: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())

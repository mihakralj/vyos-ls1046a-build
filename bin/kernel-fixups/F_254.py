"""F-254 (Phase 1 churn gate, 2026-10-06): make fman_pcd_ehash_del_key() safe
against a concurrent FMan bucket walk.

Symptom: with idle flow aging (2d7c8268) records are deleted while the FE is
walking/inserting under load. The churn gate on .185 stalled twice in 91
cycles (once FMan-wide incl. eth0, once eth3 only: FMFP_PS[0x10] STL set,
MAC receiving, BMI rfrc frozen, no kernel error, cold boot only). The same
churn without aging (no deletes under traffic) ran 141 cycles clean.

Three hazards in the delete, versus RM S5.12.14.1 (arch/fman-microcode-210-
programming-reference.md S5.3.4: update a live controller-walked structure,
SYNC via FMFP_EXTC[INV0], only then free the old structure) and the vendor
ExternalHashTableDeleteKey() (single 64-bit next_entry store, then
FmPcdHcSync before the caller frees):

1. A mid-chain unlink rewrote the predecessor's 48-bit next pointer as two
   stores (be16 @+2, be32 @+4): a walker could read it torn. Now one 64-bit
   store of the whole first word (flags kept, next replaced). Records are
   dma_alloc_coherent, so 8-byte aligned.
2. No SYNC after the unlink. Now FMFP_EXTC[INV0] is asserted and polled
   clear (same primitive F-177 uses after insert), so every FE task that
   could still reference the record has finished.
3. The record was freed immediately; the next insert could reuse the memory
   while an in-flight lookup still read it. Now freed after the SYNC.

The vendor's invalid-bit marking is deliberately not copied: the vendor
build never defines NO_CUMULATIVE_ENTRY, so it was never exercised on plain
records on this silicon.

Covers every caller (ask.ko DESTROY via fman_pcd_fe_flow_del, 0219's
duplicate-key eviction, debugfs). All run under pcd->fe_lock (mutex), so the
bounded poll may sleep-free udelay. Idempotent.
"""

import sys

SRC = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
MARK = "F-254"

with open(SRC) as f:
    src = f.read()

if MARK in src:
    print("### F-254: already applied")
    sys.exit(0)

old_mid = """			/* y now chains to x's next; keep the drain invariant. */
			*(__be16 *)((u8 *)y->record + 2) =
				cpu_to_be16((u16)((x_next >> 32) & 0xffff));
			*(__be32 *)((u8 *)y->record + 4) =
				cpu_to_be32((u32)(x_next & 0xffffffff));
			y->prev_head = x->prev_head;"""
new_mid = """			/* y now chains to x's next; keep the drain invariant.
			 * F-254: one 64-bit store (y's flags kept, next replaced)
			 * so a concurrent FMan walk never sees a torn pointer.
			 */
			{
				u64 y_word = be64_to_cpu(READ_ONCE(*(__be64 *)y->record));

				y_word = (y_word & 0xffff000000000000ULL) |
					 (x_next & 0x0000ffffffffffffULL);
				WRITE_ONCE(*(__be64 *)y->record, cpu_to_be64(y_word));
			}
			y->prev_head = x->prev_head;"""

old_free = """	list_del(&x->node);
	/* record is dma_alloc_coherent (0130) — free it the same way
	 * fman_pcd_ehash_flow_drain() does, NOT kfree (which trips
	 * free_large_kmalloc and corrupts the coherent allocator). */
	dma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,
			  x->record, x->record_dma);"""
new_free = """	list_del(&x->node);

	/* F-254: RM S5.12.14.1 -- after unlinking from a live,
	 * controller-walked structure, SYNC (FMFP_EXTC[INV0]) before the old
	 * record may be freed: a lookup that read the old pointer may still be
	 * in flight. Same primitive as F-177 on insert.
	 */
	dma_wmb();
	{
		struct fman *f254_fman = t->pcd ? fman_pcd_get_fman(t->pcd) : NULL;

		if (f254_fman) {
			const u32 f254_inv0 = 0x80000000U;
			const unsigned int f254_poll_max = 100000U;
			unsigned int f254_i;
			u32 f254_extc = 0;

			fman_set_fpm_extc(f254_fman, f254_inv0);
			for (f254_i = 0; f254_i < f254_poll_max; f254_i++) {
				f254_extc = fman_get_fpm_extc(f254_fman);
				if (!(f254_extc & f254_inv0))
					break;
				udelay(1);
			}
			if (f254_extc & f254_inv0)
				pr_warn_ratelimited("fman_pcd: F-254 ehash delete SYNC timed out (fmfp_extc=0x%08x)\\n",
						    f254_extc);
		}
	}

	/* record is dma_alloc_coherent (0130) — free it the same way
	 * fman_pcd_ehash_flow_drain() does, NOT kfree (which trips
	 * free_large_kmalloc and corrupts the coherent allocator). */
	dma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,
			  x->record, x->record_dma);"""

for name, old in (("mid-chain store", old_mid), ("free", old_free)):
    n = src.count(old)
    if n != 1:
        print(f"### F-254: FATAL: {name} anchor found {n} times (expected 1)")
        sys.exit(1)

src = src.replace(old_mid, new_mid).replace(old_free, new_free)

with open(SRC, "w") as f:
    f.write(src)
print("### F-254: ehash delete: atomic predecessor store + FMFP_EXTC SYNC before free")

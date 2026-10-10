"""F-257 (Phase 1 stability, 2026-10-08): free the per-flow FE context buffer
when an ehash record is deleted.

fman_pcd_ehash_add_key() gives every FE flow a 16-byte DMA-coherent context
(F-175: the MUX/ENQ words that the record's pointer at fe_ptr_off refers to)
and keeps it in flow->ctx. fman_pcd_ehash_flow_drain() frees it, but
fman_pcd_ehash_del_key() never did, so every individually deleted flow leaked
one coherent allocation: ask.ko DESTROY, idle aging, and (since 0219) the
same-key eviction that runs before every insert.

The context now shares the record's lifetime: it is freed right after the
record, i.e. only once the FMFP_EXTC[INV0] SYNC has completed and nothing in
flight can still reference either. The SYNC-timeout path (F-256) returns
earlier and keeps the record, so it keeps the context as well.

Resource hygiene only: no register, descriptor or record-format change. The
FMan RX stalls (#3/#4/#5) are NOT attributed to this leak.

Must run after F-256 (it anchors on F-256's version of the delete tail).
Idempotent; exits non-zero unless the anchor matches exactly once.
"""

import sys

SRC = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
MARK = "F-257"

OLD = """	dma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,
			  x->record, x->record_dma);
	kfree(x);
	return 0;
"""
NEW = """	dma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,
			  x->record, x->record_dma);
	/* F-257: the F-175 context goes with the record, after the SYNC.
	 * (The F-256 timeout path returned above and keeps both.) */
	if (x->ctx)
		dma_free_coherent(t->dev, FMAN_FE_CTX_SIZE, x->ctx,
				  x->ctx_dma);
	kfree(x);
	return 0;
"""

with open(SRC) as f:
    src = f.read()

if MARK in src:
    print("### F-257: already applied")
    sys.exit(0)

n = src.count(OLD)
if n != 1:
    print(f"### F-257: FATAL: delete-tail anchor found {n} times (expected 1); "
          "F-254 and F-256 must have run first")
    sys.exit(1)

with open(SRC, "w") as f:
    f.write(src.replace(OLD, NEW))
print("### F-257: ehash delete frees the F-175 flow context after the SYNC")

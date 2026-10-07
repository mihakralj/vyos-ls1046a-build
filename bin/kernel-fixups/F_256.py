"""F-256 (Phase 1 churn gate, 2026-10-07): make the FMan RX stall diagnosable.

Stall #3 (.185, cycle 97, captured by oracle/fmstall.py): an eth4 RX task
(FMDM_TCID 0x11570000 = PortID 0x11, TNUM 87) took a DMA bus error at
0x2e_000008f7 (FMDM_TAH/TAL; not DDR on LS1046A), and 14 eth4 RX tasks froze.
Two gaps hid it:

1. fman.c fman_bus_error() only does dev_dbg(): the ISR (dma_err_event)
   clears FMDM_SR and the error never reaches the log, so the onset time is
   unknown. Now dev_err_ratelimited with address, PortID, TNUM and LIODN.
2. F-254 freed a deleted record even when its FMFP_EXTC[INV0] SYNC timed
   out, i.e. while a stuck task may still reference it (use-after-free risk)
   and destroying the evidence. Now the record is kept (leaked; the FMan is
   already wedged and needs a cold boot) and its DMA address, 64-byte
   header/key and the stats block at +256 are logged, so a bus-error address
   can be matched against record contents.

Logging/leak only: no register, descriptor or record-format change.
Must run after F-254. Idempotent.
"""

import sys

FM = "drivers/net/ethernet/freescale/fman/"
MARK = "F-256"

EDITS = [
    (FM + "fman.c",
     """	dev_dbg(fman->dev, "%s: FMan[%d] bus error: port_id[%d]\\n",
		__func__, fman->state->fm_id, port_id);
""",
     """	/* F-256: was dev_dbg -- a DMA bus error wedges the issuing task and
	 * was silent in production (churn stall #3). */
	dev_err_ratelimited(fman->dev,
			    "FMan[%d] DMA bus error: addr 0x%010llx port_id %u tnum %u liodn %u (F-256)\\n",
			    fman->state->fm_id, (unsigned long long)addr,
			    port_id, tnum, liodn);
"""),
    (FM + "fman_pcd.c",
     """			if (f254_extc & f254_inv0)
				pr_warn_ratelimited("fman_pcd: F-254 ehash delete SYNC timed out (fmfp_extc=0x%08x)\\n",
						    f254_extc);
		}
	}

	/* record is dma_alloc_coherent (0130) — free it the same way
	 * fman_pcd_ehash_flow_drain() does, NOT kfree (which trips
	 * free_large_kmalloc and corrupts the coherent allocator). */
	dma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,
			  x->record, x->record_dma);
	kfree(x);
	return 0;
""",
     """			if (f254_extc & f254_inv0) {
				const u8 *f256_r = x->record;

				/* F-256: a stuck task may still reference this
				 * record -- keep it (leak; the FMan is wedged
				 * and needs a cold boot) and log what it held. */
				pr_warn("fman_pcd: F-256 delete SYNC timed out (fmfp_extc=0x%08x): record %pad kept, not freed\\n",
					f254_extc, &x->record_dma);
				pr_warn("fman_pcd: F-256 record %pad hdr+key %64phN\\n",
					&x->record_dma, f256_r);
				pr_warn("fman_pcd: F-256 record %pad stats@256 %24phN\\n",
					&x->record_dma, f256_r + 256);
				kfree(x);
				return 0;
			}
		}
	}

	/* record is dma_alloc_coherent (0130) — free it the same way
	 * fman_pcd_ehash_flow_drain() does, NOT kfree (which trips
	 * free_large_kmalloc and corrupts the coherent allocator). */
	dma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,
			  x->record, x->record_dma);
	kfree(x);
	return 0;
"""),
]

src = {}
for path, _, _ in EDITS:
    if path not in src:
        with open(path) as f:
            src[path] = f.read()

if all(MARK in s for s in src.values()):
    print("### F-256: already applied")
    sys.exit(0)

for path, old, new in EDITS:
    n = src[path].count(old)
    if n != 1:
        print(f"### F-256: FATAL: anchor in {path} found {n} times (expected 1): {old[:60]!r}")
        sys.exit(1)
    src[path] = src[path].replace(old, new)

for path, s in src.items():
    with open(path, "w") as f:
        f.write(s)
print("### F-256: DMA bus errors logged; F-254 keeps (and logs) records whose delete SYNC timed out")

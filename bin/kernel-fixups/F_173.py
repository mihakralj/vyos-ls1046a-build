"""F-173: add a write barrier before the ehash bucket-head publish.

CONTEXT (2026-08-06): a deep read of vendor's own original, unmodified
source (github.com/we-are-mono/ASK) found their `fix/security-hardening`
branch fixing a bug of exactly this project's own long-standing symptom
class ("a byte-verified-correct key/chain that still never produces a
genuine hardware HIT") -- commit cb8fc275, "fix IPv4 multicast offload".
Their fix, verbatim rationale from the code comment immediately before
their ExternalHashTableAddKey() call:

    "On weak-ordered ARM64 [the record's field writes] can sit in the
    CPU's store buffer when AddKey installs the bucket->head pointer
    that FMAN microcode walks. Without a barrier, FMAN can read the new
    bucket head and dereference a listener entry whose opcode/chain
    bytes are still stale, hitting CC stats but failing to enqueue...
    wmb() drains the store buffer, ordering all prior writes before
    AddKey's bucket-head publication."

This branch's own fman_pcd_ehash_add_key() (patch 0128, amended by patch
0130 and F-143) writes the flow record's header/key/next-FE-pointer
fields, then publishes the bucket head pointer
(`*flow->bucket_h = swab64(...)`) with NO barrier in between. Patch 0130
(2026-07-?, static patch, applied before any Python fixups run) already
fixed the adjacent half of this bug class -- switched the flow record
allocation from kzalloc() to dma_alloc_coherent() (cache COHERENCY),
superseding the earlier F-142 fixup attempt at the same thing (F-142's
own anchor text no longer matches patch 0130's slightly different
wording/variable names, so F-142 is now a harmless no-op in the fixup
pipeline). But coherent allocation does not by itself guarantee WRITE
ORDERING on a weak-ordered CPU: the record's field writes and the
separate bucket-head pointer write target different memory locations,
and FMan (an independent DMA-capable hardware walker) can observe the
pointer write before the pointee's field writes are globally visible,
exactly as vendor's own bug report describes.

Fix: add wmb() immediately before the bucket-head publish, matching
vendor's exact fix location and rationale. Purely additive -- no other
fe_* verb, struct field, or write path touched.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

changes = 0

old = (
    "\t/* Head-insert: bucket head = swab64(dma_addr(record)). */\n"
    "\trec_phys = (u64)rdma;\n"
    "\t*flow->bucket_h = swab64(rec_phys);\n"
)
new = (
    "\t/* Head-insert: bucket head = swab64(dma_addr(record)). */\n"
    "\trec_phys = (u64)rdma;\n"
    "\n"
    "\t/* F-173: order the record's field writes above (header, key,\n"
    "\t * next-FE pointer) before this bucket-head publish becomes\n"
    "\t * visible to FMan's independent DMA-capable hash-table walker.\n"
    "\t * Without this, a weak-ordered CPU can let the pointer write\n"
    "\t * reach visibility before the pointee's field writes do, so\n"
    "\t * FMan dereferences a still-stale record -- same bug class and\n"
    "\t * fix as vendor's own we-are-mono/ASK commit cb8fc275.\n"
    "\t */\n"
    "\twmb();\n"
    "\t*flow->bucket_h = swab64(rec_phys);\n"
)

if "F-173: order the record's field writes" in src:
    print("### F-173: barrier already present")
elif old in src:
    src = src.replace(old, new, 1)
    changes += 1
    print("### fman_pcd.c: F-173 wmb() added before bucket-head publish")
else:
    print(
        "### F-173: FATAL: expected bucket-head publish sequence not "
        "found verbatim -- patch 0130 may not have applied, or source "
        "has drifted. Refusing to guess."
    )
    sys.exit(1)

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd.c: F-173 {changes} change(s) applied")
else:
    print("### fman_pcd.c: F-173 no changes applied")
    sys.exit(1)

"""F_117 (Fix B, part 1/3): per-key FE-VM ehash delete.

The FE-VM flow-delete path only had clear-ALL (fman_pcd_ehash_flow_clear_all,
a LIFO drain).  ask_flow_offload's FLOW_CLS_DESTROY must remove exactly ONE
flow, so wiring a real fm to the clear-all delete would wipe every offloaded
flow whenever any single flow closes.

This fixup:
 1. Adds fman_pcd_ehash_del_key(t, key, key_size) — removes the one flow whose
    key matches, maintaining BOTH the live silicon collision chain and the
    prev_head LIFO-drain invariant so a later clear-all / disengage stays
    correct:
      - HEAD case  (bucket head == this record): restore bucket_h = prev_head.
      - MID-chain  (a newer record y chains to x): rewrite y's en_ehash_entry
        next-pointer (r+2..r+7, 48-bit native phys) to x's next, and set
        y->prev_head = x->prev_head so the LIFO drain still unwinds cleanly.
    Records are DMA-coherent (patch 0130); we touch only the bucket head u64
    and record header words, matching fman_pcd_ehash_add_key's access shape.
 2. Rewrites fman_pcd_fe_flow_del() to delete by key when a key is supplied,
    and to keep the clear-all behaviour when key==NULL (admin flush /
    disengage).  Supersedes F-116's fe_flow_del guard (the new body has its
    own NULL checks); F-116's clear_all guard stays.

Pairs with the ask_hw_get_fman() accessor + ask_flow_offload.c wiring (real
fm + built key) — see the ask.ko OOT source changes in the same commit.

Upstream-Status: Inappropriate [LS1046A DPAA1 FMan FE-VM ehash]
Risk-Tier: C (edits the silicon ehash collision-chain hot path)
"""

import os, sys

KROOT = "drivers/net/ethernet/freescale/fman"
PCD_C = os.path.join(KROOT, "fman_pcd.c")

if not os.path.exists(PCD_C):
    print("### F_117: fman_pcd.c not found")
    sys.exit(0)

with open(PCD_C) as f:
    src = f.read()

changes = 0

# ── 1. Insert fman_pcd_ehash_del_key() just before clear_all ───────────────
del_key_fn = (
    "/*\n"
    " * F-117: delete ONE flow whose key matches @key, keeping the live silicon\n"
    " * collision chain AND the prev_head LIFO-drain invariant intact. O(n).\n"
    " * Caller holds pcd->fe_lock (same contract as add_key).\n"
    " */\n"
    "static int fman_pcd_ehash_del_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size)\n"
    "{\n"
    "\tstruct fman_pcd_ehash_flow *x = NULL, *y, *f;\n"
    "\tu64 x_phys, x_next;\n"
    "\n"
    "\tif (!t || !key || key_size == 0 || key_size != t->key_size)\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\tlist_for_each_entry(f, &t->flows, node) {\n"
    "\t\tif (f->key_size == key_size &&\n"
    "\t\t    !memcmp((u8 *)f->record + FMAN_EHASH_FLOW_KEY_OFF,\n"
    "\t\t\t    key, key_size)) {\n"
    "\t\t\tx = f;\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\t}\n"
    "\tif (!x)\n"
    "\t\treturn -ENOENT;\n"
    "\n"
    "\tx_phys = (u64)virt_to_phys(x->record);\n"
    "\t/* x's own next-pointer: native phys of the record x chains to. */\n"
    "\tx_next = ((u64)be16_to_cpu(*(__be16 *)((u8 *)x->record + 2)) << 32) |\n"
    "\t\t be32_to_cpu(*(__be32 *)((u8 *)x->record + 4));\n"
    "\n"
    "\tif (swab64(*x->bucket_h) == x_phys) {\n"
    "\t\t/* x is the bucket head: restore head to x's saved prev_head. */\n"
    "\t\t*x->bucket_h = x->prev_head;\n"
    "\t} else {\n"
    "\t\t/* mid-chain: find predecessor y (same bucket, next == x). */\n"
    "\t\tlist_for_each_entry(y, &t->flows, node) {\n"
    "\t\t\tu64 y_next;\n"
    "\n"
    "\t\t\tif (y == x || y->index != x->index)\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\ty_next = ((u64)be16_to_cpu(*(__be16 *)((u8 *)y->record + 2)) << 32) |\n"
    "\t\t\t\t be32_to_cpu(*(__be32 *)((u8 *)y->record + 4));\n"
    "\t\t\tif (y_next != x_phys)\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\t/* y now chains to x's next; keep the drain invariant. */\n"
    "\t\t\t*(__be16 *)((u8 *)y->record + 2) =\n"
    "\t\t\t\tcpu_to_be16((u16)((x_next >> 32) & 0xffff));\n"
    "\t\t\t*(__be32 *)((u8 *)y->record + 4) =\n"
    "\t\t\t\tcpu_to_be32((u32)(x_next & 0xffffffff));\n"
    "\t\t\ty->prev_head = x->prev_head;\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\tlist_del(&x->node);\n"
    "\tkfree(x->record);\n"
    "\tkfree(x);\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static void fman_pcd_ehash_flow_clear_all(struct fman_pcd *pcd)"
)
clear_anchor = "static void fman_pcd_ehash_flow_clear_all(struct fman_pcd *pcd)"
if "fman_pcd_ehash_del_key" in src:
    print("### F_117: ehash_del_key already present")
elif clear_anchor in src:
    src = src.replace(clear_anchor, del_key_fn, 1)
    changes += 1
    print("### F_117: inserted fman_pcd_ehash_del_key()")
else:
    print("### F_117: WARNING — clear_all anchor not found (layout drift?)")

# ── 2. Rewrite fman_pcd_fe_flow_del() to per-key (NULL key => clear-all) ────
# Match the post-F-116 body (F-116 added the fman_get_pcd NULL guard).
del_body_f116 = (
    "\t(void)hw_port_id;\n"
    "\t(void)key;\n"
    "\t(void)key_size;\n"
    "\tif (!fman_get_pcd(fm))\n"
    "\t\treturn -ENODEV;\n"
    "\tfman_pcd_ehash_flow_clear_all(fman_get_pcd(fm));\n"
    "\treturn 0;\n"
    "}"
)
del_body_new = (
    "\tstruct fman_pcd *pcd = fman_get_pcd(fm);\n"
    "\tstruct fman_pcd_ehash_table *t;\n"
    "\n"
    "\t(void)hw_port_id;\n"
    "\tif (!pcd)\n"
    "\t\treturn -ENODEV;\n"
    "\t/* No key => legacy clear-all (admin flush / disengage). */\n"
    "\tif (!key || key_size == 0) {\n"
    "\t\tfman_pcd_ehash_flow_clear_all(pcd);\n"
    "\t\treturn 0;\n"
    "\t}\n"
    "\tt = fman_pcd_ehash_table_by_index(pcd, 0);\n"
    "\tif (!t)\n"
    "\t\treturn -ENODEV;\n"
    "\treturn fman_pcd_ehash_del_key(t, key, key_size);\n"
    "}"
)
if "fman_pcd_ehash_del_key(t, key, key_size);" in src:
    print("### F_117: fe_flow_del already per-key")
elif del_body_f116 in src:
    src = src.replace(del_body_f116, del_body_new, 1)
    changes += 1
    print("### F_117: rewrote fman_pcd_fe_flow_del() to per-key delete")
else:
    print("### F_117: WARNING — fe_flow_del post-F-116 body not found (order/layout drift?)")

if changes:
    with open(PCD_C, "w") as f:
        f.write(src)
    print("### F_117: %d change(s) applied" % changes)
else:
    print("### F_117: no changes applied")

"""F-142: Convert ehash flow records from kzalloc to dma_alloc_coherent.

The FE-VM ehash path (Fork-B) has never produced a HIT on silicon because
flow records were allocated with kzalloc() (cacheable kernel memory).  The
FMan DMA engine reads these records and may see stale data if the CPU cache
hasn't been flushed.  The code comment in patch 0128 line 84 explicitly
acknowledges this: "DDR records use kzalloc (RAM); the armed path needs
dma_alloc_coherent".

Changes:
1. Add dma_addr_t record_dma to struct fman_pcd_ehash_flow
2. Add struct device *dev to struct fman_pcd_ehash_table (set at table creation)
3. Replace kzalloc with dma_alloc_coherent in fman_pcd_ehash_add_key()
4. Replace kfree with dma_free_coherent in fman_pcd_ehash_flow_drain()
5. Use the dma_addr_t directly instead of virt_to_phys()

Must run AFTER 0128 (which defines the functions being modified).
"""

import sys, os, re

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-142: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Add record_dma to struct fman_pcd_ehash_flow ──
flow_struct = "struct fman_pcd_ehash_flow {"
if flow_struct in src:
    # Find the record field and add record_dma after it
    record_field = "\tvoid *record;\t\t\t/* DDR 256-B flow record (kzalloc) */"
    if record_field in src:
        new_fields = "\tvoid *record;\t\t\t/* DDR 256-B flow record (dma_alloc_coherent) */\n\tdma_addr_t record_dma;\t\t/* F-142: bus address for DMA */"
        if "record_dma" not in src:
            src = src.replace(record_field, new_fields, 1)
            changes += 1
            print("### F-142: added record_dma to struct fman_pcd_ehash_flow")
        else:
            print("### F-142: record_dma already present")
    else:
        print("### F-142: record field not found in flow struct")
else:
    print("### F-142: flow struct not found")

# ── 2. Add dev to struct fman_pcd_ehash_table ──
table_struct = "struct fman_pcd_ehash_table {"
if table_struct in src:
    # Find the ad field and add dev before it
    ad_field = "\tu32 ad[4];\t\t\t/* en_exthash_node DDR template */"
    if ad_field in src:
        new_ad = "\tstruct device *dev;\t\t/* F-142: for dma_alloc_coherent */\n" + ad_field
        if "struct device *dev" not in src:
            src = src.replace(ad_field, new_ad, 1)
            changes += 1
            print("### F-142: added dev to struct fman_pcd_ehash_table")
        else:
            print("### F-142: dev already in table struct")
    else:
        print("### F-142: ad field not found in table struct")
else:
    print("### F-142: table struct not found")

# ── 3. Set t->dev in fman_pcd_ehash_table_set ──
# Find the line after INIT_LIST_HEAD(&t->flows) and add dev assignment
init_flows = "\tINIT_LIST_HEAD(&t->flows);"
if init_flows in src:
    dev_assign = "\tINIT_LIST_HEAD(&t->flows);\n\tt->dev = fman_get_dev(pcd->fman);\t/* F-142 */"
    if "t->dev = fman_get_dev" not in src:
        src = src.replace(init_flows, dev_assign, 1)
        changes += 1
        print("### F-142: set t->dev in ehash_table_set")
    else:
        print("### F-142: t->dev already set")
else:
    print("### F-142: INIT_LIST_HEAD not found in table_set")

# ── 4. Replace kzalloc with dma_alloc_coherent in add_key ──
old_alloc = "\tr = kzalloc(FMAN_EHASH_FLOW_REC_SIZE, GFP_KERNEL);"
if old_alloc in src:
    new_alloc = "\tr = dma_alloc_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,\n\t\t\t       &flow->record_dma, GFP_KERNEL);"
    if "dma_alloc_coherent" not in src:
        src = src.replace(old_alloc, new_alloc, 1)
        changes += 1
        print("### F-142: replaced kzalloc with dma_alloc_coherent in add_key")
    else:
        print("### F-142: dma_alloc_coherent already in add_key")
else:
    print("### F-142: kzalloc not found in add_key")

# ── 5. Replace virt_to_phys(r) with flow->record_dma ──
old_v2p = "\trec_phys = (u64)virt_to_phys(r);"
if old_v2p in src:
    new_v2p = "\trec_phys = (u64)flow->record_dma;\t/* F-142: bus address from dma_alloc_coherent */"
    if "flow->record_dma" not in src:
        src = src.replace(old_v2p, new_v2p, 1)
        changes += 1
        print("### F-142: replaced virt_to_phys with record_dma")
    else:
        print("### F-142: record_dma already used for rec_phys")
else:
    print("### F-142: virt_to_phys not found in add_key")

# ── 6. Replace kfree with dma_free_coherent in drain ──
old_free = "\t\tkfree(flow->record);"
if old_free in src:
    new_free = "\t\tdma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,\n\t\t\t\t  flow->record, flow->record_dma);\t/* F-142 */"
    if "dma_free_coherent" not in src:
        src = src.replace(old_free, new_free, 1)
        changes += 1
        print("### F-142: replaced kfree with dma_free_coherent in drain")
    else:
        print("### F-142: dma_free_coherent already in drain")
else:
    print("### F-142: kfree(flow->record) not found in drain")

# ── 7. Update fe_flow_show to use record_dma instead of virt_to_phys ──
old_show = "\t\t\tu64 rec_phys = (u64)virt_to_phys(flow->record);"
if old_show in src:
    new_show = "\t\t\tu64 rec_phys = (u64)flow->record_dma;\t/* F-142 */"
    if "flow->record_dma" not in src:
        src = src.replace(old_show, new_show, 1)
        changes += 1
        print("### F-142: updated fe_flow_show to use record_dma")
    else:
        print("### F-142: fe_flow_show already uses record_dma")
else:
    print("### F-142: virt_to_phys not found in fe_flow_show")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-142: {changes} change(s) applied")
else:
    print("### F-142: no changes — may already be present")
    sys.exit(0)
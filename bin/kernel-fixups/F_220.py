"""F-220 (Phase 1: per-port IPv4 ehash table foundation).

Design: specs/ask2-shared-table-multi-protocol-design.md — move routed IPv4 from
one FMan-global ehash table shared by all ports to ONE table instance per
engaged port (the vendor replicateHtNodes ownership model). This removes
cross-port key aliasing and cross-port teardown ambiguity WITHOUT a comparison-
key PORT_ID byte, and without any exported ABI change.

This fixup lays the foundation; F-185/F-204/F-218 are then repointed (separately)
to consume the per-port table for the v4 (table_idx 0 / 14-byte key) path. IPv6
(table1) and the default-OFF v6 path are untouched here.

WHAT IT ADDS
------------
1. struct fman_pcd_ehash_table forward declaration before struct
   fman_pcd_fe_port, and a `struct fman_pcd_ehash_table *table_v4;` field on the
   per-port struct (the natural owner, alongside the F-139 scaffold offsets).
2. `struct fman_pcd_ehash_table *fe_arming_v4_table;` on struct fman_pcd — the
   transient hand-off slot, mirroring the proven F-139 fe_scaffold_* idiom
   (arm writes it; fe_port_set moves it into fp; both serialized by the PCD
   engage path so one-at-a-time is safe).
3. Forward declarations for fman_pcd_ehash_table_set/_free/_by_index and a new
   helper fman_pcd_ehash_table_for_port(pcd, hw_port_id) that returns this
   port's v4 table, falling back to global table index 0 when no per-port table
   exists (keeps debugfs/diagnostic and transition-safe).
4. In __fman_pcd_fe_arm_engage: after the shared VM-chain guard has ensured the
   int-buf pool + singletons exist, allocate a fresh per-port v4 table
   (mask 0x7FFF, key_size 14, shift 0 — identical to the current global v4
   table) and stash it in pcd->fe_arming_v4_table. list_last_entry after
   ehash_table_set yields the just-appended table (list_add_tail; serialized).
5. In fman_pcd_fe_port_set: hand pcd->fe_arming_v4_table into fp->table_v4 and
   clear the transient (same pattern as the scaffold handoff two lines above).
6. In fman_pcd_fe_port_del and _drain: free fp->table_v4 via
   fman_pcd_ehash_table_free (list_del + flow drain + dma_free + int_buf_put),
   so per-port teardown is symmetric.
7. Teardown reorder in fman_pcd_free(): run fman_pcd_fe_port_drain() (frees +
   list-removes each fp->table_v4) BEFORE fman_pcd_ehash_drain() (which then
   frees only the remaining template/v6 tables). Without this, ehash_drain would
   free per-port tables that fe_port_drain then double-frees (they share the
   fe_ehash_tables list).
8. Engage-failure rollback: if arm_engage succeeds but fe_port_set fails, the
   pending table would leak; free pcd->fe_arming_v4_table in the arm-disengage
   cleanup path (guarded).

SHARED vs REPLICATED (unchanged invariants)
-------------------------------------------
- The 33 KB MURAM internal-buffer pool stays SHARED + refcounted. Each per-port
  ehash_table_set bumps the refcount (fman_pcd_ehash_int_buf_get); each free
  drops it. The global template v4 table built once by __fman_pcd_fe_build_vm_chain
  keeps one ref, holding the pool warm (F-136), and remains as the by_index(0)
  fallback. Do NOT allocate an int-buf pool per port.
- Only the 512 KB DDR bucket array + per-flow DDR records replicate per port
  (DDR is not scarce; MURAM node rows are 16 B each).

SAFETY / S0
-----------
No exported ABI change (fman_pcd_fe_flow_add/_del already take hw_port_id; the
arm path already has port_id). The per-port table uses the identical
mask/key_size/shift as today's global v4 table, so the DDR node encoding and the
14-byte key are byte-identical. v6 table1 and the v6 gate are untouched. Qdrant
gate: cross-checked against fman-fe-ehash.md ehash_table_set (DDR buckets, shared
int-buf pool) and the F-139 scaffold-handoff reversibility idiom.

Must run AFTER F-140 (builds the shared chain + global tables) and BEFORE the
F-185/F-204/F-218 repoint edits that consume table_v4. Idempotent via F-220
markers.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
if not os.path.exists(path):
    print("### F-220: fman_pcd.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

changes = 0


def fatal(msg):
    print(f"### F-220: FATAL: {msg}")
    sys.exit(1)


def apply_one(name, marker, old, new):
    global src, changes
    if marker not in new:
        fatal(f"marker {marker} missing from replacement '{name}'")
    if marker in src:
        print(f"### F-220: {name} already applied")
        return
    if old not in src:
        fatal(f"'{name}' anchor not found verbatim — source drifted.")
    if src.count(old) != 1:
        fatal(f"'{name}' anchor is not unique ({src.count(old)} matches).")
    src = src.replace(old, new, 1)
    changes += 1
    print(f"### fman_pcd.c: F-220 {name} applied")


# ── 1. Forward decls + table_v4 field on struct fman_pcd_fe_port ──
apply_one(
    "fe_port struct table_v4 field",
    "F-220(fe-port-table-field)",
    "struct fman_pcd_fe_port {\n"
    "\tstruct list_head node;\n"
    "\tu8 port_id;\n",
    "/* F-220(fe-port-table-field): per-port routed-IPv4 ehash table ownership.\n"
    " * Forward-declare the table struct (defined lower in the file) and the free\n"
    " * helper (defined after fe_port_del) so the per-port lifecycle can reference\n"
    " * them. */\n"
    "struct fman_pcd_ehash_table;\n"
    "static void fman_pcd_ehash_table_free(struct fman_pcd *pcd,\n"
    "\t\t\t\t      struct fman_pcd_ehash_table *t);\n"
    "\n"
    "struct fman_pcd_fe_port {\n"
    "\tstruct list_head node;\n"
    "\tu8 port_id;\n"
    "\tstruct fman_pcd_ehash_table *table_v4;\t/* F-220: this port's v4 table */\n",
)

# ── 2. Transient hand-off slot on struct fman_pcd (next to fe_scaffold_*) ──
apply_one(
    "pcd arming-table slot",
    "F-220(pcd-arming-table)",
    "\tunsigned long fe_scaffold_gro;\t/* group table (256 B) */\n"
    "\tunsigned long fe_scaffold_mto;\t/* match table (16 B) */\n"
    "\tunsigned long fe_scaffold_ato;\t/* AD table    (32 B) */\n",
    "\tunsigned long fe_scaffold_gro;\t/* group table (256 B) */\n"
    "\tunsigned long fe_scaffold_mto;\t/* match table (16 B) */\n"
    "\tunsigned long fe_scaffold_ato;\t/* AD table    (32 B) */\n"
    "\t/* F-220(pcd-arming-table): per-port v4 table pending hand-off from\n"
    "\t * __fman_pcd_fe_arm_engage() to fman_pcd_fe_port_set(), mirroring the\n"
    "\t * fe_scaffold_* idiom. Serialized by the engage path. */\n"
    "\tstruct fman_pcd_ehash_table *fe_arming_v4_table;\n",
)

# ── 3. table_for_port() helper + forward decls, placed right after the
#       existing fman_pcd_ehash_table_by_index() definition. ──
apply_one(
    "table_for_port helper",
    "F-220(table-for-port)",
    "static struct fman_pcd_ehash_table *\n"
    "fman_pcd_ehash_table_by_index(struct fman_pcd *pcd, unsigned int idx)\n"
    "{\n"
    "\tstruct fman_pcd_ehash_table *t;\n"
    "\tunsigned int i = 0;\n"
    "\n"
    "\tlist_for_each_entry(t, &pcd->fe_ehash_tables, node)\n"
    "\t\tif (i++ == idx)\n"
    "\t\t\treturn t;\n"
    "\treturn NULL;\n"
    "}\n",
    "static struct fman_pcd_ehash_table *\n"
    "fman_pcd_ehash_table_by_index(struct fman_pcd *pcd, unsigned int idx)\n"
    "{\n"
    "\tstruct fman_pcd_ehash_table *t;\n"
    "\tunsigned int i = 0;\n"
    "\n"
    "\tlist_for_each_entry(t, &pcd->fe_ehash_tables, node)\n"
    "\t\tif (i++ == idx)\n"
    "\t\t\treturn t;\n"
    "\treturn NULL;\n"
    "}\n"
    "\n"
    "/* F-220(table-for-port): resolve the routed-IPv4 table instance owned by\n"
    " * @hw_port_id. Falls back to the global template table (index 0) when the\n"
    " * port has no per-port table yet (debugfs/diagnostic and transition-safe).\n"
    " */\n"
    "static struct fman_pcd_ehash_table *\n"
    "fman_pcd_ehash_table_for_port(struct fman_pcd *pcd, u8 hw_port_id)\n"
    "{\n"
    "\tstruct fman_pcd_fe_port *fp = fman_pcd_fe_port_find(pcd, hw_port_id);\n"
    "\n"
    "\tif (fp && fp->table_v4)\n"
    "\t\treturn fp->table_v4;\n"
    "\treturn fman_pcd_ehash_table_by_index(pcd, 0);\n"
    "}\n",
)

# ── 4. Allocate the per-port v4 table in __fman_pcd_fe_arm_engage, right after
#       the F-185 table0 node-write block's opening resolves. We anchor on the
#       F-185 list_first_entry lookup and PRECEDE it with the per-port alloc so
#       F-185 (repointed later) can consume pcd->fe_arming_v4_table. Here we only
#       allocate + stash; the node write still uses the global table until the
#       F-185 repoint fixup lands. ──
apply_one(
    "arm-engage per-port alloc",
    "F-220(arm-alloc)",
    "\t\t\t\t\tstruct fman_pcd_ehash_table *et =\n"
    "\t\t\t\t\t\tlist_first_entry_or_null(&pcd->fe_ehash_tables,\n"
    "\t\t\t\t\t\t\tstruct fman_pcd_ehash_table, node);\n",
    "\t\t\t\t\t/* F-220(arm-alloc): allocate THIS port's own routed-IPv4\n"
    "\t\t\t\t\t * table (identical mask/key_size/shift to the global\n"
    "\t\t\t\t\t * template) and stash it for hand-off to fe_port_set.\n"
    "\t\t\t\t\t * list_last_entry is the just-appended table (list_add_tail;\n"
    "\t\t\t\t\t * engage-serialized). Failure falls back to template table0.\n"
    "\t\t\t\t\t * Declared-after-nothing: this reuses the existing et decl\n"
    "\t\t\t\t\t * line as the anchor and inserts the alloc immediately after\n"
    "\t\t\t\t\t * it, keeping all declarations before statements.\n"
    "\t\t\t\t\t */\n"
    "\t\t\t\t\tstruct fman_pcd_ehash_table *et =\n"
    "\t\t\t\t\t\tlist_first_entry_or_null(&pcd->fe_ehash_tables,\n"
    "\t\t\t\t\t\t\tstruct fman_pcd_ehash_table, node);\n"
    "\n"
    "\t\t\t\t\tif (!pcd->fe_arming_v4_table &&\n"
    "\t\t\t\t\t    fman_pcd_ehash_table_set(pcd, 0x7FFF, 14, 0) == 0) {\n"
    "\t\t\t\t\t\tpcd->fe_arming_v4_table =\n"
    "\t\t\t\t\t\t\tlist_last_entry(&pcd->fe_ehash_tables,\n"
    "\t\t\t\t\t\t\t\tstruct fman_pcd_ehash_table, node);\n"
    "\t\t\t\t\t\tdev_info(fman_get_dev(pcd->fman),\n"
    "\t\t\t\t\t\t\t \"fman_pcd: F-220 per-port v4 table for port 0x%02x DDR %pad\\n\",\n"
    "\t\t\t\t\t\t\t (unsigned int)port_id,\n"
    "\t\t\t\t\t\t\t &pcd->fe_arming_v4_table->table_dma);\n"
    "\t\t\t\t\t}\n",
)

# ── 5. Hand off pending table into fp->table_v4 in fman_pcd_fe_port_set,
#       alongside the scaffold handoff. ──
apply_one(
    "fe_port_set handoff",
    "F-220(port-set-handoff)",
    "\tfp->scaffold_gro = pcd->fe_scaffold_gro;\n"
    "\tfp->scaffold_mto = pcd->fe_scaffold_mto;\n"
    "\tfp->scaffold_ato = pcd->fe_scaffold_ato;\n"
    "\tpcd->fe_scaffold_gro = 0;\n"
    "\tpcd->fe_scaffold_mto = 0;\n"
    "\tpcd->fe_scaffold_ato = 0;\n",
    "\tfp->scaffold_gro = pcd->fe_scaffold_gro;\n"
    "\tfp->scaffold_mto = pcd->fe_scaffold_mto;\n"
    "\tfp->scaffold_ato = pcd->fe_scaffold_ato;\n"
    "\tpcd->fe_scaffold_gro = 0;\n"
    "\tpcd->fe_scaffold_mto = 0;\n"
    "\tpcd->fe_scaffold_ato = 0;\n"
    "\t/* F-220(port-set-handoff): take ownership of this port's v4 table. */\n"
    "\tfp->table_v4 = pcd->fe_arming_v4_table;\n"
    "\tpcd->fe_arming_v4_table = NULL;\n",
)

# ── 6a. Free per-port table in fe_port_del (before list_del of fp). ──
apply_one(
    "fe_port_del free",
    "F-220(port-del-free)",
    "\tif (fp->scaffold_gro)\n"
    "\t\tfman_pcd_muram_free(pcd, fp->scaffold_gro, 256);\n"
    "\tlist_del(&fp->node);\n"
    "\tdev_info(fman_get_dev(pcd->fman),\n"
    "\t\t \"fman_pcd: FE support removed on port 0x%x\\n\", port_id);\n",
    "\tif (fp->scaffold_gro)\n"
    "\t\tfman_pcd_muram_free(pcd, fp->scaffold_gro, 256);\n"
    "\t/* F-220(port-del-free): release this port's v4 table instance\n"
    "\t * (list_del + flow drain + dma_free + int-buf refcount put). */\n"
    "\tif (fp->table_v4) {\n"
    "\t\tfman_pcd_ehash_table_free(pcd, fp->table_v4);\n"
    "\t\tfp->table_v4 = NULL;\n"
    "\t}\n"
    "\tlist_del(&fp->node);\n"
    "\tdev_info(fman_get_dev(pcd->fman),\n"
    "\t\t \"fman_pcd: FE support removed on port 0x%x\\n\", port_id);\n",
)

# ── 6b. Free per-port table in fe_port_drain. ──
apply_one(
    "fe_port_drain free",
    "F-220(port-drain-free)",
    "\t\tif (fp->scaffold_gro)\n"
    "\t\t\tfman_pcd_muram_free(pcd, fp->scaffold_gro, 256);\n"
    "\t\tlist_del(&fp->node);\n"
    "\t\tkfree(fp);\n",
    "\t\tif (fp->scaffold_gro)\n"
    "\t\t\tfman_pcd_muram_free(pcd, fp->scaffold_gro, 256);\n"
    "\t\tif (fp->table_v4) {\n"
    "\t\t\tfman_pcd_ehash_table_free(pcd, fp->table_v4);\t/* F-220(port-drain-free) */\n"
    "\t\t\tfp->table_v4 = NULL;\n"
    "\t\t}\n"
    "\t\tlist_del(&fp->node);\n"
    "\t\tkfree(fp);\n",
)

# ── 6c. Teardown ordering: fman_pcd_free() currently drains the ehash tables
#       BEFORE the per-port structs. With per-port table ownership, fp->table_v4
#       lives on the same fe_ehash_tables list, so ehash_drain would free it and
#       fe_port_drain would then double-free (list_del + dma_free on freed node).
#       Reorder so per-port drain (which frees + list-removes each fp->table_v4)
#       runs FIRST; ehash_drain then frees only the remaining template/v6 tables.
apply_one(
    "teardown reorder (port_drain before ehash_drain)",
    "F-220(teardown-order)",
    "\tfman_pcd_ehash_drain(pcd);\n"
    "\tWARN_ON(!list_empty(&pcd->fe_ehash_tables));\n"
    "\tfman_pcd_fe_port_drain(pcd);\n"
    "\tWARN_ON(!list_empty(&pcd->fe_ports));\n",
    "\t/* F-220(teardown-order): drain per-port structs (frees + list-removes\n"
    "\t * each fp->table_v4) BEFORE ehash_drain, which then frees only the\n"
    "\t * remaining template/v6 tables. Prevents a double-free of per-port\n"
    "\t * tables that share the fe_ehash_tables list. */\n"
    "\tfman_pcd_fe_port_drain(pcd);\n"
    "\tWARN_ON(!list_empty(&pcd->fe_ports));\n"
    "\tfman_pcd_ehash_drain(pcd);\n"
    "\tWARN_ON(!list_empty(&pcd->fe_ehash_tables));\n",
)

# ── 6d. Board-validation observable: show each port's v4 table DMA address in
#       the fe_port debugfs node, so eth3/eth4 owning DISTINCT tables is directly
#       verifiable (the Phase-1 acceptance signal). ──
apply_one(
    "fe_port debugfs shows table_v4",
    "F-220(port-show-table)",
    "\t\tlist_for_each_entry(fp, &pcd->fe_ports, node)\n"
    "\t\t\tseq_printf(s,\n"
    "\t\t\t\t   \"port 0x%02x pool 0x%lx/%zu B mgmt 0x%lx/%zu B\\n\",\n"
    "\t\t\t\t   fp->port_id, fp->pool_raw_off,\n"
    "\t\t\t\t   fp->pool_raw_size, fp->mgmt_off,\n"
    "\t\t\t\t   fp->mgmt_size);\n",
    "\t\tlist_for_each_entry(fp, &pcd->fe_ports, node)\n"
    "\t\t\tseq_printf(s,\n"
    "\t\t\t\t   \"port 0x%02x pool 0x%lx/%zu B mgmt 0x%lx/%zu B v4_table %px\\n\",\n"
    "\t\t\t\t   fp->port_id, fp->pool_raw_off,\n"
    "\t\t\t\t   fp->pool_raw_size, fp->mgmt_off,\n"
    "\t\t\t\t   fp->mgmt_size, fp->table_v4);\t/* F-220(port-show-table) */\n",
)

# ── 7. Rollback: free a pending (un-handed-off) table on arm-disengage. This
#       covers the engage path where fe_port_set failed after a successful arm. ──
apply_one(
    "arm-disengage rollback",
    "F-220(disarm-rollback)",
    "\tclear_bit(port_id, pcd->fe_port_armed);\n"
    "\tfsleep(5000);\n"
    "\tfman_pcd_fe_port_del(pcd, (u8)port_id);\n",
    "\tclear_bit(port_id, pcd->fe_port_armed);\n"
    "\t/* F-220(disarm-rollback): if a per-port v4 table was allocated during\n"
    "\t * arm but never handed off (fe_port_set failed), free it now so it does\n"
    "\t * not leak or hold a stale int-buf ref. fe_port_del frees the handed-off\n"
    "\t * table for the normal path. */\n"
    "\tif (pcd->fe_arming_v4_table) {\n"
    "\t\tfman_pcd_ehash_table_free(pcd, pcd->fe_arming_v4_table);\n"
    "\t\tpcd->fe_arming_v4_table = NULL;\n"
    "\t}\n"
    "\tfsleep(5000);\n"
    "\tfman_pcd_fe_port_del(pcd, (u8)port_id);\n",
)

with open(path, "w") as f:
    f.write(src)

if changes:
    print(f"### F-220 complete ({changes} change(s))")
else:
    print("### F-220 no changes (already present)")
    sys.exit(0)

"""F-285: optional HC SYNC after the production ehash INV0 barrier.

The parameter defaults off and is selectable only at boot. This avoids
changing the barrier while an ehash operation or HC teardown is in flight.
Lab operators must initialize the F-284 HC port before any ehash mutation;
an enabled but unavailable HC port fails closed so F-269 retains uncertain
records and generations. Recovery from a latched HC failure requires a reboot.
Missing FMan ownership prevents insertion and fails the barrier closed,
regardless of the HC setting.
"""

import sys

MARK = "F-285"
BASE = "drivers/net/ethernet/freescale/fman/"

INCLUDE_OLD = "#include <linux/mutex.h>\n"
INCLUDE_NEW = "#include <linux/mutex.h>\n#include <linux/module.h>\n"

STATE_OLD = "static struct fman_pcd_hc *hc_state;\n"
STATE_NEW = STATE_OLD + (
    "\n"
    "/* F-285: boot-only opt-in for the ehash retirement barrier. */\n"
    "static bool ehash_hc_sync_enable;\n"
    "module_param_named(ehash_hc_sync_enable, ehash_hc_sync_enable,\n"
    "\t\t   bool, 0400);\n"
    'MODULE_PARM_DESC(ehash_hc_sync_enable, "Require HC SYNC after ehash INV0 (boot-only)");\n'
)

HC_SYNC_OLD = (
    "\treturn failed || hc->last_src == HC_SRC_ERR || hc->last_status ?\n"
    "\t\t-EIO : 0;\n"
    "}\n\n"
    "static int hc_sync(struct fman_pcd_hc *hc, unsigned int n)\n"
)
HC_SYNC_NEW = (
    "\treturn failed || hc->last_src == HC_SRC_ERR || hc->last_status ?\n"
    "\t\t-EIO : 0;\n"
    "}\n\n"
    "int fman_pcd_hc_ehash_sync(struct fman_pcd *pcd)\n"
    "{\n"
    "\tstruct fman_pcd_hc *hc;\n"
    "\tint err;\n"
    "\n"
    "\tif (!READ_ONCE(ehash_hc_sync_enable))\n"
    "\t\treturn 0;\n"
    "\thc = smp_load_acquire(&hc_state);\n"
    "\tif (!hc || hc->pcd != pcd)\n"
    "\t\treturn -ENODEV;\n"
    "\n"
    "\tmutex_lock(&hc->lock);\n"
    "\tif (READ_ONCE(hc->failed))\n"
    "\t\terr = -EIO;\n"
    "\telse if (!hc->enabled)\n"
    "\t\terr = -ENODEV;\n"
    "\telse\n"
    "\t\terr = hc_sync_one(hc);\n"
    "\tmutex_unlock(&hc->lock);\n"
    "\tif (err)\n"
    "\t\tpr_err(\"fman_pcd: ehash HC SYNC barrier failed (%d)\\n\", err);\n"
    "\treturn err;\n"
    "}\n\n"
    "static int hc_sync(struct fman_pcd_hc *hc, unsigned int n)\n"
)

FINI_OLD = (
    "static int hc_fini(struct fman_pcd_hc *hc)\n"
    "{\n"
    "\tint err;\n"
    "\n"
    "\tif (READ_ONCE(hc->failed))\n"
)
FINI_NEW = (
    "static int hc_fini(struct fman_pcd_hc *hc)\n"
    "{\n"
    "\tint err;\n"
    "\n"
    "\tif (READ_ONCE(ehash_hc_sync_enable))\n"
    "\t\treturn -EBUSY;\n"
    "\tif (READ_ONCE(hc->failed))\n"
)

SHOW_OLD = '\tseq_printf(s, "hwport 0x%02x chan 0x%03x conf_chan 0x%03x\\n",\n'
SHOW_NEW = (
    '\tseq_printf(s, "ehash_hc_sync_enable %u\\n",\n'
    '\t\t   READ_ONCE(ehash_hc_sync_enable));\n'
    + SHOW_OLD
)

PROTO_OLD = (
    "void fman_pcd_hc_debugfs_init(struct dentry *parent,\n"
    "\t\t\t      struct fman_pcd *pcd);\n"
)
PROTO_NEW = PROTO_OLD + (
    "/* F-285 opt-in production ehash barrier. */\n"
    "int fman_pcd_hc_ehash_sync(struct fman_pcd *pcd);\n"
)

SYNC_DECL_OLD = "\tu32 v = 0;\n\n\t*extc = 0;"
SYNC_DECL_NEW = "\tu32 v = 0;\n\tint err;\n\n\t*extc = 0;"

NO_FMAN_OLD = (
    "\tif (!fman) {\n"
    "\t\tdma_wmb();\n"
    "\t\treturn 0;\n"
    "\t}\n"
)
NO_FMAN_NEW = (
    "\tif (!fman) {\n"
    '\t\tpr_warn_ratelimited("fman_pcd: ehash SYNC has no FMan owner; retaining state\\n");\n'
    "\t\treturn -ENODEV;\n"
    "\t}\n"
)

SYNC_SUCCESS_OLD = (
    "\t\tif (!(v & inv0)) {\n"
    "\t\t\tif (attempt)\n"
    "\t\t\t\tehash_sync_recovered++;\n"
    "\t\t\treturn 0;\n"
    "\t\t}\n"
)
SYNC_SUCCESS_NEW = (
    "\t\tif (!(v & inv0)) {\n"
    "\t\t\tif (attempt)\n"
    "\t\t\t\tehash_sync_recovered++;\n"
    "\t\t\t/* F-285: the opt-in HC helper may sleep. */\n"
    "\t\t\terr = fman_pcd_hc_ehash_sync(t->pcd);\n"
    "\t\t\tif (err)\n"
    "\t\t\t\treturn err;\n"
    "\t\t\treturn 0;\n"
    "\t\t}\n"
)

RECOVER_OLD = (
    "static int fman_pcd_ehash_recover(struct fman_pcd_ehash_table *t)\n"
    "{\n"
    "\tstruct fman_pcd_ehash_cu *cu, *cu_tmp;\n"
    "\tstruct fman_pcd_ehash_flow *flow, *flow_tmp;\n"
    "\tu32 extc;\n"
    "\n"
    "\tif (!t->poisoned)\n"
    "\t\treturn 0;\n"
    "\tif (fman_pcd_ehash_sync(t, &extc))\n"
    "\t\treturn -ETIMEDOUT;\n"
)
RECOVER_NEW = (
    "static int fman_pcd_ehash_recover(struct fman_pcd_ehash_table *t)\n"
    "{\n"
    "\tstruct fman_pcd_ehash_cu *cu, *cu_tmp;\n"
    "\tstruct fman_pcd_ehash_flow *flow, *flow_tmp;\n"
    "\tu32 extc;\n"
    "\tint err;\n"
    "\n"
    "\tif (!t->poisoned)\n"
    "\t\treturn 0;\n"
    "\terr = fman_pcd_ehash_sync(t, &extc);\n"
    "\tif (err)\n"
    "\t\treturn err;\n"
)

ADD_GUARD_OLD = (
    "\tif (t->draining)\n"
    "\t\treturn -ESHUTDOWN;\n"
    "\tif (fman_pcd_ehash_recover(t))\n"
    "\t\treturn -ETIMEDOUT;\n"
    "\tif (key_size > FMAN_EHASH_FLOW_KEY_MAX)\n"
)
ADD_GUARD_NEW = (
    "\tif (t->draining)\n"
    "\t\treturn -ESHUTDOWN;\n"
    "\tif (!t->pcd || !fman_pcd_get_fman(t->pcd)) {\n"
    '\t\tpr_warn_ratelimited("fman_pcd: ehash insert has no FMan owner\\n");\n'
    "\t\treturn -ENODEV;\n"
    "\t}\n"
    "\tf269_err = fman_pcd_ehash_recover(t);\n"
    "\tif (f269_err)\n"
    "\t\treturn f269_err;\n"
    "\tif (key_size > FMAN_EHASH_FLOW_KEY_MAX)\n"
)

INSERT_SYNC_OLD = (
    "\t\tif (fman_pcd_ehash_sync(t, &extc)) {\n"
    "\t\t\tlist_add_tail(&old_cu->quarantine_node,\n"
    "\t\t\t\t      &t->quarantined_cu);\n"
    "\t\t\tt->poisoned = true;\n"
    '\t\t\tpr_warn_ratelimited("fman_pcd: F-269 insert SYNC timed out '
    '(fmfp_extc=0x%08x): bucket %u generation quarantined\\n",\n'
    "\t\t\t\t\t    extc, index);\n"
)
INSERT_SYNC_NEW = (
    "\t\tf269_err = fman_pcd_ehash_sync(t, &extc);\n"
    "\t\tif (f269_err) {\n"
    "\t\t\tlist_add_tail(&old_cu->quarantine_node,\n"
    "\t\t\t\t      &t->quarantined_cu);\n"
    "\t\t\tt->poisoned = true;\n"
    '\t\t\tpr_warn_ratelimited("fman_pcd: F-269 insert SYNC failed '
    '(%d, fmfp_extc=0x%08x): bucket %u generation quarantined\\n",\n'
    "\t\t\t\t\t    f269_err, extc, index);\n"
)

DRAIN_DECL_OLD = (
    "static int fman_pcd_ehash_flow_drain(struct fman_pcd_ehash_table *t)\n"
    "{\n"
    "\tstruct fman_pcd_ehash_flow *flow, *flow_tmp;\n"
    "\tstruct fman_pcd_ehash_cu *cu, *cu_tmp;\n"
    "\tu32 i;\n"
    "\tu32 extc;\n"
)
DRAIN_DECL_NEW = DRAIN_DECL_OLD + "\tint err;\n"

DRAIN_SYNC_OLD = (
    "\tdma_wmb();\n"
    "\tif (fman_pcd_ehash_sync(t, &extc))\n"
    "\t\treturn -ETIMEDOUT;\n"
)
DRAIN_SYNC_NEW = (
    "\tdma_wmb();\n"
    "\terr = fman_pcd_ehash_sync(t, &extc);\n"
    "\tif (err)\n"
    "\t\treturn err;\n"
)

DELETE_DECL_OLD = (
    "static int fman_pcd_ehash_del_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size)\n"
    "{\n"
    "\tstruct fman_pcd_ehash_flow *x = NULL, *f;\n"
    "\tstruct fman_pcd_ehash_cu *old_cu = NULL;\n"
    "\tu32 extc;\n"
)
DELETE_DECL_NEW = DELETE_DECL_OLD + "\tint err;\n"

DELETE_GUARD_OLD = (
    "\tif (t->draining)\n"
    "\t\treturn -ESHUTDOWN;\n"
    "\tif (fman_pcd_ehash_recover(t))\n"
    "\t\treturn -ETIMEDOUT;\n"
    "\n"
    "\t/* F-264: only the key's own bucket can hold it. */\n"
)
DELETE_GUARD_NEW = (
    "\tif (t->draining)\n"
    "\t\treturn -ESHUTDOWN;\n"
    "\terr = fman_pcd_ehash_recover(t);\n"
    "\tif (err)\n"
    "\t\treturn err;\n"
    "\n"
    "\t/* F-264: only the key's own bucket can hold it. */\n"
)

DELETE_SYNC_OLD = (
    "\t/* F-254: a lookup that read the old head may still be in flight. */\n"
    "\tif (fman_pcd_ehash_sync(t, &extc)) {\n"
    "\t\tconst u8 *f256_r = x->record;\n"
    "\n"
    "\t\t/* F-256: a stuck task may still reference the record or the\n"
    "\t\t * replaced nodes -- keep them (leak; the FMan is wedged and\n"
    "\t\t * needs a cold boot) and log what the record held. */\n"
    '\t\tpr_warn("fman_pcd: F-256 delete SYNC timed out '
    '(fmfp_extc=0x%08x): record %pad kept, not freed\\n",\n'
    "\t\t\textc, &x->record_dma);\n"
)
DELETE_SYNC_NEW = (
    "\t/* F-254: a lookup that read the old head may still be in flight. */\n"
    "\terr = fman_pcd_ehash_sync(t, &extc);\n"
    "\tif (err) {\n"
    "\t\tconst u8 *f256_r = x->record;\n"
    "\n"
    "\t\t/* Keep the record and replaced nodes until a later barrier. */\n"
    '\t\tpr_warn("fman_pcd: F-256 delete SYNC failed '
    '(%d, fmfp_extc=0x%08x): record %pad kept, not freed\\n",\n'
    "\t\t\terr, extc, &x->record_dma);\n"
)

DELETE_RETURN_OLD = (
    "\t\tlist_add_tail(&x->node, &t->quarantined_flows);\n"
    "\t\tt->poisoned = true;\n"
    "\t\treturn -ETIMEDOUT;\n"
)
DELETE_RETURN_NEW = (
    "\t\tlist_add_tail(&x->node, &t->quarantined_flows);\n"
    "\t\tt->poisoned = true;\n"
    "\t\treturn err;\n"
)

EDITS = (
    (BASE + "fman_pcd_hc.c", INCLUDE_OLD, INCLUDE_NEW, "module include"),
    (BASE + "fman_pcd_hc.c", STATE_OLD, STATE_NEW, "barrier parameter"),
    (BASE + "fman_pcd_hc.c", HC_SYNC_OLD, HC_SYNC_NEW, "HC ehash helper"),
    (BASE + "fman_pcd_hc.c", FINI_OLD, FINI_NEW, "fini guard"),
    (BASE + "fman_pcd_hc.c", SHOW_OLD, SHOW_NEW, "barrier status"),
    (BASE + "fman_pcd_internal.h", PROTO_OLD, PROTO_NEW, "HC helper prototype"),
    (BASE + "fman_pcd.c", SYNC_DECL_OLD, SYNC_DECL_NEW, "SYNC local"),
    (BASE + "fman_pcd.c", NO_FMAN_OLD, NO_FMAN_NEW, "missing-FMan path"),
    (BASE + "fman_pcd.c", SYNC_SUCCESS_OLD, SYNC_SUCCESS_NEW, "INV0 success path"),
    (BASE + "fman_pcd.c", RECOVER_OLD, RECOVER_NEW, "recovery error"),
    (BASE + "fman_pcd.c", ADD_GUARD_OLD, ADD_GUARD_NEW, "insert recovery error"),
    (BASE + "fman_pcd.c", INSERT_SYNC_OLD, INSERT_SYNC_NEW, "insert barrier error"),
    (BASE + "fman_pcd.c", DRAIN_DECL_OLD, DRAIN_DECL_NEW, "drain local"),
    (BASE + "fman_pcd.c", DRAIN_SYNC_OLD, DRAIN_SYNC_NEW, "drain error"),
    (BASE + "fman_pcd.c", DELETE_DECL_OLD, DELETE_DECL_NEW, "delete local"),
    (BASE + "fman_pcd.c", DELETE_GUARD_OLD, DELETE_GUARD_NEW, "delete recovery error"),
    (BASE + "fman_pcd.c", DELETE_SYNC_OLD, DELETE_SYNC_NEW, "delete barrier error"),
    (BASE + "fman_pcd.c", DELETE_RETURN_OLD, DELETE_RETURN_NEW, "delete result"),
)

sources = {}
for path, _old, _new, _name in EDITS:
    try:
        with open(path) as f:
            sources[path] = f.read()
    except FileNotFoundError:
        print(f"### F-285: FATAL: {path} not found")
        sys.exit(1)

marked = [MARK in sources[path] for path, _old, _new, _name in EDITS]
if any(marked):
    if not all(marked):
        print("### F-285: FATAL: partial application detected")
        sys.exit(1)
    for path, _old, new, name in EDITS:
        count = sources[path].count(new)
        if count != 1:
            print(f"### F-285: FATAL: applied {name} found {count} times (expected 1)")
            sys.exit(1)
    print("### F-285: already applied")
    sys.exit(0)

for path, old, _new, name in EDITS:
    count = sources[path].count(old)
    if count != 1:
        print(f"### F-285: FATAL: {name} anchor found {count} times (expected 1)")
        sys.exit(1)

for path, old, new, _name in EDITS:
    sources[path] = sources[path].replace(old, new, 1)

for path, source in sources.items():
    with open(path, "w") as f:
        f.write(source)
print("### F-285: default-off HC SYNC barrier after production ehash INV0")

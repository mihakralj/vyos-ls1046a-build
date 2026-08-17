"""F-202: serialize production FE ehash flow add/delete with pcd->fe_lock.

Silicon panic (2026-08-17, .185, image 2026.08.17-0114):

    list_del corruption ... was dead000000000122 (LIST_POISON2)
    __list_del_entry_valid_or_report
    fman_pcd_ehash_del_key
    fman_pcd_fe_flow_del
    ask_flow_offload_setup_tc_block_cb [ask]
    nf_flow_offload_tuple / nf_ft_offload_del workqueue

Root cause is a direct lock-contract violation. fman_pcd_ehash_del_key() states
"Caller holds pcd->fe_lock (same contract as add_key)", and every debugfs
add/del/clear path obeys it, but the exported production APIs
fman_pcd_fe_flow_add() and fman_pcd_fe_flow_del() did not take fe_lock. Thus
nft's asynchronous FLOW_CLS_DESTROY worker could race another per-key delete,
admin clear-all, or flow drain. Both operations could select the same flow;
one list_del() poisoned x->node and the other hit the observed LIST_POISON2.

Fix:
  * take pcd->fe_lock after fman_get_pcd() validation in production flow_add;
    unlock on no-table, no-ENQ, and the final return;
  * take the same lock in production flow_del around clear-all/table lookup/
    per-key deletion;
  * treat -ENOENT after a serialized per-key delete as idempotent success. A
    delayed duplicate DESTROY or an admin clear that won ownership has nothing
    left to remove; surfacing it cannot restore state, while re-entering list
    surgery must never occur.

No internal helper lock changes, no lock nesting changes, no descriptor/register
changes. Production engage/disengage do not hold fe_lock; debugfs callers do not
call these exported wrappers. Count-gated, idempotent marker F-202; runs after
F-117/F-188/F-198/F-200 derived state.

S0 QDRANT GATE: cross-checked F-117 per-key unlink history, CR-003/CR-004
lifecycle findings, and the live panic. The existing helper contract and debugfs
precedent require fe_lock; no authoritative source conflicts.
"""

import sys

SRC = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

with open(SRC) as f:
    src = f.read()

if "F-202(flow-api-lock)" in src:
    print("### F-202 already applied")
    sys.exit(0)

blocks = [
    (
        "flow-add lock acquisition",
        "\tpcd = fman_get_pcd(fm);\n"
        "\tif (!pcd) {\n"
        "\t\tpr_warn_ratelimited(\"fman_pcd: F-194 flow-add no-pcd fm=%px hw_port=0x%02x key_size=%u\\n\",\n"
        "\t\t\t\t    fm, hw_port_id, action->key_size);\n"
        "\t\treturn -ENXIO;\n"
        "\t}\n\n"
        "\tt = fman_pcd_ehash_table_by_index(pcd, 0);",
        "\tpcd = fman_get_pcd(fm);\n"
        "\tif (!pcd) {\n"
        "\t\tpr_warn_ratelimited(\"fman_pcd: F-194 flow-add no-pcd fm=%px hw_port=0x%02x key_size=%u\\n\",\n"
        "\t\t\t\t    fm, hw_port_id, action->key_size);\n"
        "\t\treturn -ENXIO;\n"
        "\t}\n\n"
        "\t/* F-202(flow-api-lock): serialize production add/delete/clear.\n"
        "\t * fman_pcd_ehash_{add,del}_key require pcd->fe_lock. */\n"
        "\tmutex_lock(&pcd->fe_lock);\n\n"
        "\tt = fman_pcd_ehash_table_by_index(pcd, 0);",
    ),
    (
        "flow-add no-table unlock",
        "\t\treturn -ENODEV;\n"
        "\t}\n\n"
        "\t/* F-193(prod-flow-add-diag)",
        "\t\tmutex_unlock(&pcd->fe_lock);\n"
        "\t\treturn -ENODEV;\n"
        "\t}\n\n"
        "\t/* F-193(prod-flow-add-diag)",
    ),
    (
        "flow-add no-enq unlock",
        "\tif (!enq_obj)\n"
        "\t\treturn -ENOENT;\n\n"
        "\t{\n"
        "\t\t/* F-188(prod-flow-target)",
        "\tif (!enq_obj) {\n"
        "\t\tmutex_unlock(&pcd->fe_lock);\n"
        "\t\treturn -ENOENT;\n"
        "\t}\n\n"
        "\t{\n"
        "\t\t/* F-188(prod-flow-target)",
    ),
    (
        "flow-add final unlock",
        "\t}\n\t\treturn err;\n\t}\n}\nEXPORT_SYMBOL_GPL(fman_pcd_fe_flow_add);",
        "\t}\n"
        "\t\tmutex_unlock(&pcd->fe_lock);\n"
        "\t\treturn err;\n"
        "\t}\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_add);",
    ),
    (
        "flow-del serialized body",
        "\t/* No key => legacy clear-all (admin flush / disengage). */\n"
        "\tif (!key || key_size == 0) {\n"
        "\t\tfman_pcd_ehash_flow_clear_all(pcd);\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tt = fman_pcd_ehash_table_by_index(pcd, 0);\n"
        "\tif (!t)\n"
        "\t\treturn -ENODEV;\n"
        "\treturn fman_pcd_ehash_del_key(t, key, key_size);",
        "\t/* F-202(flow-api-lock): the helper contract requires fe_lock.\n"
        "\t * Serialize nft async DESTROY against duplicate delete/clear/drain. */\n"
        "\tmutex_lock(&pcd->fe_lock);\n"
        "\t/* No key => legacy clear-all (admin flush / disengage). */\n"
        "\tif (!key || key_size == 0) {\n"
        "\t\tfman_pcd_ehash_flow_clear_all(pcd);\n"
        "\t\tmutex_unlock(&pcd->fe_lock);\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tt = fman_pcd_ehash_table_by_index(pcd, 0);\n"
        "\tif (!t) {\n"
        "\t\tmutex_unlock(&pcd->fe_lock);\n"
        "\t\treturn -ENODEV;\n"
        "\t}\n"
        "\t{\n"
        "\t\tint err = fman_pcd_ehash_del_key(t, key, key_size);\n\n"
        "\t\tmutex_unlock(&pcd->fe_lock);\n"
        "\t\t/* A delayed duplicate DESTROY or clear-all winner is a clean\n"
        "\t\t * idempotent no-op: no silicon record remains to remove. */\n"
        "\t\treturn err == -ENOENT ? 0 : err;\n"
        "\t}",
    ),
]

for name, old, new in blocks:
    count = src.count(old)
    if count != 1:
        print(f"### F-202: FATAL: {name}: expected 1 anchor, got {count}")
        sys.exit(1)
    src = src.replace(old, new, 1)

with open(SRC, "w") as f:
    f.write(src)

print(f"### fman_pcd.c: F-202 production FE flow API serialization applied ({len(blocks)} blocks)")

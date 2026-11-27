"""F-326: Use sleepable SRCU for dpaa_setup_tc_flow_block() instead of rcu_read_lock().

Board patch 0145-dpaa-flow-offload-backend-slot.patch wrapped the
ops->setup_tc_block(net_dev, f) callout inside rcu_read_lock()/rcu_read_unlock().
dpaa_setup_tc_flow_block() only runs in process context (TC_SETUP_BLOCK /
TC_SETUP_FT under rtnl_lock or nft_net->commit_mutex + flow_block_lock), and
the ASK2 backend (ask_flow_offload_setup_tc_common / ask_flow_block_withdraw)
sleeps on mutex_lock(&ask_flow_lifecycle_lock), GFP_KERNEL allocations, and
synchronize_rcu().

Under CONFIG_PROVE_LOCKING=y (observed on cold-booted KUnit image
2026.11.25-1737-rolling during __nft_unregister_flowtable_net_hooks), holding
rcu_read_lock() across setup_tc_block() triggers:
    [ BUG: Invalid wait context ]
    nft is trying to lock: ask_flow_lifecycle_lock (mutex)
    holding rcu_read_lock at dpaa_setup_tc_flow_block+0x8/0x208

Replace plain RCU in dpaa_setup_tc_flow_block() with a dedicated sleepable
SRCU domain (DEFINE_STATIC_SRCU(dpaa_fo_srcu)):
  - dpaa_setup_tc_flow_block() uses srcu_read_lock() / srcu_dereference() /
    srcu_read_unlock(), permitting sleeping inside ops->setup_tc_block().
  - dpaa_get_offload_stats() (atomic ndo_get_stats64 path from patch 0171)
    stays on plain rcu_read_lock() / rcu_dereference().
  - dpaa_unregister_flow_offload_handler() drains BOTH synchronize_rcu() and
    synchronize_srcu(&dpaa_fo_srcu) before module_put(ops->owner).

Idempotent; count-gated anchors.
"""

import sys

DPAA_ETH_C = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
MARK = "F-326"

DPAA_EDITS = [
    (
        "include linux/srcu.h",
        "#include <linux/fsl/dpaa_flow_offload.h>\n",
        "#include <linux/fsl/dpaa_flow_offload.h>\n"
        "#include <linux/srcu.h>\n",
    ),
    (
        "define dpaa_fo_srcu domain",
        "static const struct dpaa_flow_offload_ops __rcu *dpaa_fo_ops_slot;\n"
        "static DEFINE_SPINLOCK(dpaa_fo_ops_lock);\n",
        "static const struct dpaa_flow_offload_ops __rcu *dpaa_fo_ops_slot;\n"
        "static DEFINE_SPINLOCK(dpaa_fo_ops_lock);\n"
        "/* F-326: sleepable SRCU for process-context TC/FT block setup;\n"
        " * atomic ndo_get_stats64() (dpaa_get_offload_stats) stays on RCU.\n"
        " */\n"
        "DEFINE_STATIC_SRCU(dpaa_fo_srcu);\n",
    ),
    (
        "drain synchronize_srcu in dpaa_unregister_flow_offload_handler",
        "\trcu_assign_pointer(dpaa_fo_ops_slot, NULL);\n"
        "\tspin_unlock(&dpaa_fo_ops_lock);\n"
        "\tsynchronize_rcu();\n",
        "\trcu_assign_pointer(dpaa_fo_ops_slot, NULL);\n"
        "\tspin_unlock(&dpaa_fo_ops_lock);\n"
        "\tsynchronize_rcu();\n"
        "\tsynchronize_srcu(&dpaa_fo_srcu);\n",
    ),
    (
        "use srcu_read_lock in dpaa_setup_tc_flow_block",
        "static int dpaa_setup_tc_flow_block(struct net_device *net_dev,\n"
        "\t\t\t\tstruct flow_block_offload *f)\n"
        "{\n"
        "\tconst struct dpaa_flow_offload_ops *ops;\n"
        "\tint err;\n"
        "\n"
        "\trcu_read_lock();\n"
        "\tops = rcu_dereference(dpaa_fo_ops_slot);\n"
        "\tif (!ops) {\n"
        "\t\trcu_read_unlock();\n"
        "\t\treturn -EOPNOTSUPP;\n"
        "\t}\n"
        "\n"
        "\terr = ops->setup_tc_block(net_dev, f);\n"
        "\trcu_read_unlock();\n"
        "\n"
        "\treturn err;\n"
        "}\n",
        "static int dpaa_setup_tc_flow_block(struct net_device *net_dev,\n"
        "\t\t\t\tstruct flow_block_offload *f)\n"
        "{\n"
        "\tconst struct dpaa_flow_offload_ops *ops;\n"
        "\tint idx, err;\n"
        "\n"
        "\tidx = srcu_read_lock(&dpaa_fo_srcu);\n"
        "\tops = srcu_dereference(dpaa_fo_ops_slot, &dpaa_fo_srcu);\n"
        "\tif (!ops) {\n"
        "\t\tsrcu_read_unlock(&dpaa_fo_srcu, idx);\n"
        "\t\treturn -EOPNOTSUPP;\n"
        "\t}\n"
        "\n"
        "\terr = ops->setup_tc_block(net_dev, f);\n"
        "\tsrcu_read_unlock(&dpaa_fo_srcu, idx);\n"
        "\n"
        "\treturn err;\n"
        "}\n",
    ),
]


def apply_one(path: str, edits: list[tuple[str, str, str]]) -> None:
    try:
        with open(path) as f:
            s = f.read()
    except FileNotFoundError:
        print(f"### {MARK}: FATAL: {path} not found")
        sys.exit(1)

    if MARK in s:
        for name, _old, new in edits:
            if new not in s:
                print(f"### {MARK}: FATAL: marker present in {path} but '{name}' missing")
                sys.exit(1)
        print(f"### {MARK}: {path} already applied")
        return

    for name, old, _new in edits:
        n = s.count(old)
        if n != 1:
            print(f"### {MARK}: FATAL: {path} anchor '{name}' found {n} times (expected 1)")
            sys.exit(1)

    for _name, old, new in edits:
        s = s.replace(old, new, 1)

    with open(path, "w") as f:
        f.write(s)
    print(f"### {MARK}: patched {path} ({len(edits)} anchor(s))")


apply_one(DPAA_ETH_C, DPAA_EDITS)

"""F-321: strict per-port ehash table isolation for port disengage (T1(f)).

F-291's fman_pcd_fe_disarm_preflight_locked() conservatively scanned every
table on pcd->fe_ehash_tables and returned -EBUSY whenever ANY port had live
or uncertain ehash records, because fman_pcd_ehash_table_for_port() still had
a fallback to global template table 0 and fman_pcd_fe_flow_{add,del,stats}()
still allowed non-zero table_idx / key_size==38 global table selection.

Every production ehash producer (ask.ko, dpaa_eth tc-flower, dpaa_ethtool
ntuple, dpaa_wgfp) uses table_idx=0 on an armed port's dedicated fp->table_v4.
Close the global-table bypasses so each armed port's table is strictly owned
by that port:
  1. Mark each published fp->table_v4 with table->per_port = true in
     fman_pcd_fe_port_set().
  2. Remove the global table-0 fallback in fman_pcd_ehash_table_for_port()
     (return NULL if the port has no fp->table_v4).
  3. Reject action->table_idx != 0 with -EINVAL in fman_pcd_fe_flow_add(),
     and remove the dormant key_size==38 global table-1 bypass in
     fman_pcd_fe_flow_del_checked() and fman_pcd_fe_flow_get_stats_checked().
  4. In fman_pcd_fe_disarm_preflight_locked(), skip other ports' per_port
     tables while still checking fp->table_v4 and any shared (!per_port)
     template tables.
  5. In dpaa_ethtool.c dpaa_cls_ins(), engage FE (after ring_cookie and slot
     capacity validation) before querying fman_pcd_fe_ehash_keysize(), so
     ethtool -N on an unengaged port still works without a table-0 fallback.

Applies after F-315. Idempotent; count-gated anchors.
"""

import sys

PCD_PATH = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
ETH_PATH = "drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c"
MARK = "F-321"

PCD_EDITS = [
    (
        "struct fman_pcd_ehash_table per_port",
        "\tbool poisoned;\t/* F-269 timeout ownership quarantine */\n"
        "\tbool draining;\n"
        "};\n",
        "\tbool poisoned;\t/* F-269 timeout ownership quarantine */\n"
        "\tbool draining;\n"
        "\tbool per_port;\t/* F-321: owned by one armed port's fp->table_v4 */\n"
        "};\n",
    ),
    (
        "fe_arm mark per_port",
        "\t\t\t\t\tpcd->fe_arming_port_id = (u8)port_id;\n"
        "\t\t\t\t\tet = pcd->fe_arming_v4_table;\n",
        "\t\t\t\t\tpcd->fe_arming_port_id = (u8)port_id;\n"
        "\t\t\t\t\tet = pcd->fe_arming_v4_table;\n"
        "\t\t\t\t\tet->per_port = true;\t/* F-321 */\n",
    ),
    (
        "table_for_port no global fallback",
        "static struct fman_pcd_ehash_table *\n"
        "fman_pcd_ehash_table_for_port(struct fman_pcd *pcd, u8 hw_port_id)\n"
        "{\n"
        "\tstruct fman_pcd_fe_port *fp = fman_pcd_fe_port_find(pcd, hw_port_id);\n"
        "\n"
        "\tif (fp && fp->table_v4)\n"
        "\t\treturn fp->table_v4;\n"
        "\treturn fman_pcd_ehash_table_by_index(pcd, 0);\n"
        "}\n",
        "static struct fman_pcd_ehash_table *\n"
        "fman_pcd_ehash_table_for_port(struct fman_pcd *pcd, u8 hw_port_id)\n"
        "{\n"
        "\tstruct fman_pcd_fe_port *fp = fman_pcd_fe_port_find(pcd, hw_port_id);\n"
        "\n"
        "\t/* F-321: strict per-port ownership; never fall back to template table 0. */\n"
        "\tif (fp && fp->table_v4)\n"
        "\t\treturn fp->table_v4;\n"
        "\treturn NULL;\n"
        "}\n",
    ),
    (
        "disarm_preflight per-port isolation",
        "\t/* F-291: conservatively require every ehash table to be idle. */\n"
        "\tlist_for_each_entry(table, &pcd->fe_ehash_tables, node) {\n"
        "\t\tif (table == fp->table_v4)\n"
        "\t\t\towner_listed = true;\n"
        "\t\tif (!list_empty(&table->flows) ||\n"
        "\t\t    !list_empty(&table->quarantined_flows) ||\n"
        "\t\t    !list_empty(&table->quarantined_cu) ||\n"
        "\t\t    table->poisoned || table->draining) {\n"
        "\t\t\tpr_warn_ratelimited(\"fman_pcd: FE disarm port 0x%02x has live or uncertain ehash owners\\n\",\n"
        "\t\t\t\t\t    port_id);\n"
        "\t\t\treturn -EBUSY;\n"
        "\t\t}\n"
        "\t}\n",
        "\t/* F-321: require this port's table and any shared (!per_port) table\n"
        "\t * to be idle; unrelated armed ports' per-port tables do not block. */\n"
        "\tlist_for_each_entry(table, &pcd->fe_ehash_tables, node) {\n"
        "\t\tif (table == fp->table_v4)\n"
        "\t\t\towner_listed = true;\n"
        "\t\tif (table != fp->table_v4 && table->per_port)\n"
        "\t\t\tcontinue;\n"
        "\t\tif (!list_empty(&table->flows) ||\n"
        "\t\t    !list_empty(&table->quarantined_flows) ||\n"
        "\t\t    !list_empty(&table->quarantined_cu) ||\n"
        "\t\t    table->poisoned || table->draining) {\n"
        "\t\t\tpr_warn_ratelimited(\"fman_pcd: FE disarm port 0x%02x has live or uncertain ehash owners\\n\",\n"
        "\t\t\t\t\t    port_id);\n"
        "\t\t\treturn -EBUSY;\n"
        "\t\t}\n"
        "\t}\n",
    ),
    (
        "fe_flow_add reject nonzero table_idx",
        "\tif (action->table_idx == 0)\n"
        "\t\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);\n"
        "\telse\n"
        "\t\tt = fman_pcd_ehash_table_by_index(pcd, action->table_idx);\n",
        "\t/* F-321: production callers only address this port's own table_v4. */\n"
        "\tif (action->table_idx != 0) {\n"
        "\t\tmutex_unlock(&pcd->fe_lock);\n"
        "\t\treturn -EINVAL;\n"
        "\t}\n"
        "\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);\n",
    ),
    (
        "fe_flow_del_checked per-port only",
        "\t/* F-221(del-v4-perport): v6 (38-byte key) -> global table 1; v4 ->\n"
        "\t * this ingress port's own table instance. */\n"
        "\tif (key_size == 38)\n"
        "\t\tt = fman_pcd_ehash_table_by_index(pcd, 1);\n"
        "\telse\n"
        "\t\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);\n",
        "\t/* F-321: strict per-port table lookup only. */\n"
        "\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);\n",
    ),
    (
        "fe_flow_get_stats_checked per-port only",
        "\t/* Same table selection as fman_pcd_fe_flow_del(): v6 (38-byte key) ->\n"
        "\t * global table 1; everything else -> this ingress port's table. */\n"
        "\tif (key_size == 38)\n"
        "\t\tt = fman_pcd_ehash_table_by_index(pcd, 1);\n"
        "\telse\n"
        "\t\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);\n",
        "\t/* F-321: strict per-port table lookup only. */\n"
        "\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);\n",
    ),
]

ETH_OLD = (
    "\t/* F-242: record keys must match the port's own ehash-table key size\n"
    "\t * (14 single-lane, 46 dual-lane) or the walker never compares them. */\n"
    "\terr = fman_pcd_fe_ehash_keysize(fm, port_id);\n"
    "\tif (err < 0)\n"
    "\t\treturn err;\n"
    "\tif (err != 14 && err != DPAA_CLS_KEY_MAX)\n"
    "\t\treturn -EOPNOTSUPP;\n"
    "\tkey_size = err;\n"
    "\n"
    "\tif (fs->ring_cookie != RX_CLS_FLOW_DISC) {\n"
    "\t\terr = dpaa_cls_queue_to_fqid(priv, fs->ring_cookie, &fqid);\n"
    "\t\tif (err)\n"
    "\t\t\treturn err;\n"
    "\t} else {\n"
    "\t\tfqid = 0;\t/* F-247: the drop record needs no queue */\n"
    "\t}\n"
    "\n"
    "\treplaced = dpaa_cls_find(priv, fs->location);\n"
    "\tif (!replaced && priv->cls_rule_cnt >= FMAN_CC_MAX_STATIC_KEYS)\n"
    "\t\treturn -ENOSPC;\n"
    "\n"
    "\tif (!priv->cls_fe_engaged) {\n"
    "\t\terr = fman_pcd_fe_engage(fm, port_id, 0);\n"
    "\t\tif (err)\n"
    "\t\t\treturn err;\n"
    "\t\tpriv->cls_fe_engaged = true;\n"
    "\t}\n"
)

ETH_NEW = (
    "\tif (fs->ring_cookie != RX_CLS_FLOW_DISC) {\n"
    "\t\terr = dpaa_cls_queue_to_fqid(priv, fs->ring_cookie, &fqid);\n"
    "\t\tif (err)\n"
    "\t\t\treturn err;\n"
    "\t} else {\n"
    "\t\tfqid = 0;\t/* F-247: the drop record needs no queue */\n"
    "\t}\n"
    "\n"
    "\treplaced = dpaa_cls_find(priv, fs->location);\n"
    "\tif (!replaced && priv->cls_rule_cnt >= FMAN_CC_MAX_STATIC_KEYS)\n"
    "\t\treturn -ENOSPC;\n"
    "\n"
    "\t/* F-321: engage the port's own table before querying its key size. */\n"
    "\tif (!priv->cls_fe_engaged) {\n"
    "\t\terr = fman_pcd_fe_engage(fm, port_id, 0);\n"
    "\t\tif (err)\n"
    "\t\t\treturn err;\n"
    "\t\tpriv->cls_fe_engaged = true;\n"
    "\t}\n"
    "\n"
    "\t/* F-242: record keys must match the port's own ehash-table key size\n"
    "\t * (14 single-lane, 46 dual-lane) or the walker never compares them. */\n"
    "\terr = fman_pcd_fe_ehash_keysize(fm, port_id);\n"
    "\tif (err < 0)\n"
    "\t\treturn err;\n"
    "\tif (err != 14 && err != DPAA_CLS_KEY_MAX)\n"
    "\t\treturn -EOPNOTSUPP;\n"
    "\tkey_size = err;\n"
)


def apply_one(path: str, edits: list[tuple[str, str, str]]) -> None:
    try:
        with open(path) as f:
            s = f.read()
    except FileNotFoundError:
        print(f"### F-321: FATAL: {path} not found")
        sys.exit(1)

    if MARK in s:
        for name, _old, new in edits:
            if new not in s:
                print(f"### F-321: FATAL: marker present in {path} but '{name}' missing")
                sys.exit(1)
        print(f"### F-321: {path} already applied")
        return

    for name, old, _new in edits:
        n = s.count(old)
        if n != 1:
            print(f"### F-321: FATAL: {path} anchor '{name}' found {n} times (expected 1)")
            sys.exit(1)

    for _name, old, new in edits:
        s = s.replace(old, new, 1)

    with open(path, "w") as f:
        f.write(s)
    print(f"### F-321: patched {path} ({len(edits)} anchor(s))")


apply_one(PCD_PATH, PCD_EDITS)
apply_one(ETH_PATH, [("dpaa_cls_add engage before keysize", ETH_OLD, ETH_NEW)])

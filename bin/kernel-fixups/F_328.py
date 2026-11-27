"""F-328 (G10 Phase 3): Single-port per-packet MURAM DSCP->CEETM CQ map.

Extends FMan 210.10.1 ehash ENQUEUE_PKT and DPAA clsact egress hooks for
dynamic per-packet IPv4/IPv6 DSCP -> CEETM CQ steering:

  1. include/linux/fsl/fman_pcd.h:
       - add `bool dscp_fq_map` to `struct fman_pcd_fe_flow_action` and
         `struct fman_pcd_vlan_params`.
       - declare `fman_pcd_dscp_map_set(struct fman *fm, u8 dscp, u32 fqid)`
         and `fman_pcd_dscp_map_clear_all(struct fman *fm)`.
  2. drivers/net/ethernet/freescale/fman/fman_pcd.c:
       - expand F-262's owned FE MURAM block (`FMAN_EHASH_FRAG_INFO_SIZE`)
         from 32 B to 288 B so it holds vendor `cdx_muram_memory_cmn_db_t`:
         28 B `cdx_ucode_frag_info_t` (+0..+27) immediately followed by
         64 x be32 `dscp_fqid_map[64]` (+28..+283, `FMAN_EHASH_DSCP_MAP_OFF = 28`).
       - keep a 64-entry shadow array `fe_dscp_map[64]` inside `struct fman_pcd`
         synced into MURAM on `fman_pcd_ehash_int_buf_get()` so DSCP filters
         installed before ASK engage (or across disengage/re-engage) populate
         MURAM cleanly with zero leak on S1->S0.
       - implement & export `fman_pcd_dscp_map_set()` and
         `fman_pcd_dscp_map_clear_all()`.
       - in `fman_pcd_ehash_add_key()` / `fman_pcd_fe_flow_add()`: when
         `dscp_fq_map` is set, require `fe_frag_off != 0` and L2/TX terminal,
         and write `en_ehash_enqueue_param.word2 = (0x01 << 24) | (fe_frag_off & 0x00ffffff)`
         (`DSCP_FQ_MAP_ENABLE`, vendor `cdx_ehash.c create_enque_hm()`).
  3. drivers/net/ethernet/freescale/dpaa/dpaa_eth.c:
       - in `dpaa_setup_tc_block()`, dispatch `FLOW_BLOCK_BINDER_TYPE_CLSACT_EGRESS`
         to `dpaa_ceetm_setup_dscp_block(net_dev, f)`.

Must run after F-262 and F-327. Idempotent; count-gated anchors.
"""

import sys

FMAN_PCD_H = "include/linux/fsl/fman_pcd.h"
FMAN_PCD_C = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
DPAA_ETH_C = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
MARK = "F-328"

FMAN_PCD_H_EDITS = [
    (
        "add dscp_fq_map to fman_pcd_fe_flow_action",
        "\tu16  egress_mtu;\n"
        "};\n",
        "\tu16  egress_mtu;\n"
        "\t/* F-328 (G10 Phase 3): enable 210.10.1 MURAM DSCP->FQID lookup\n"
        "\t * (word2[31:24] |= DSCP_FQ_MAP_ENABLE 0x01, word2[23:0] = fe_frag_off)\n"
        "\t * on unmarked flows egressing the port owning the DSCP map. */\n"
        "\tbool dscp_fq_map;\n"
        "};\n",
    ),
    (
        "add dscp_fq_map to fman_pcd_vlan_params",
        "\tu16 egress_mtu;\t\t/* F-262: 05 + ENQ mtu/bpid/word2; 0 = none */\n"
        "};\n",
        "\tu16 egress_mtu;\t\t/* F-262: 05 + ENQ mtu/bpid/word2; 0 = none */\n"
        "\tbool dscp_fq_map;\t/* F-328: ENQ word2 |= DSCP_FQ_MAP_ENABLE */\n"
        "};\n",
    ),
    (
        "declare fman_pcd_dscp_map_set and clear_all",
        "void fman_pcd_offload_disengage(struct fman *fman, u8 hw_port_id);\n",
        "void fman_pcd_offload_disengage(struct fman *fman, u8 hw_port_id);\n"
        "\n"
        "/* F-328 (G10 Phase 3): program 210.10.1 MURAM DSCP->FQID lookup table. */\n"
        "int fman_pcd_dscp_map_set(struct fman *fm, u8 dscp, u32 fqid);\n"
        "void fman_pcd_dscp_map_clear_all(struct fman *fm);\n",
    ),
]

FMAN_PCD_C_EDITS = [
    (
        "add fe_dscp_map shadow array to struct fman_pcd",
        "\tunsigned long fe_frag_off;\t  /* F-262: frag-info block, 0 if none */\n",
        "\tunsigned long fe_frag_off;\t  /* F-262: frag-info block, 0 if none */\n"
        "\tu32 fe_dscp_map[64];\t\t  /* F-328: shadow of MURAM dscp_fqid_map */\n",
    ),
    (
        "expand FMAN_EHASH_FRAG_INFO_SIZE for cdx_muram_memory_cmn_db_t",
        "#define FMAN_EHASH_FRAG_INFO_SIZE\t32\n",
        "/* F-328: vendor cdx_muram_memory_cmn_db_t = 28 B cdx_ucode_frag_info_t\n"
        " * followed immediately at +28 by 64 x be32 dscp_fqid_map (256 B) = 284 B.\n"
        " * Reserve 288 B inside the owned, refcounted FE internal-buffer block. */\n"
        "#define FMAN_EHASH_DSCP_MAP_OFF\t\t28\n"
        "#define FMAN_EHASH_DSCP_MAP_COUNT\t64\n"
        "#define FMAN_EHASH_FRAG_INFO_SIZE\t288\n",
    ),
    (
        "sync shadow fe_dscp_map into MURAM on int_buf allocation",
        "\tiowrite32be(1, v + FMAN_EHASH_INT_BUF_TOTAL +\n"
        "\t\t    FMAN_EHASH_STATS_SCRATCH_SIZE + 24);\n",
        "\tiowrite32be(1, v + FMAN_EHASH_INT_BUF_TOTAL +\n"
        "\t\t    FMAN_EHASH_STATS_SCRATCH_SIZE + 24);\n"
        "\t/* F-328: replay any active DSCP->FQID shadow entries into MURAM. */\n"
        "\t{\n"
        "\t\tvoid __iomem *dscp_v = v + FMAN_EHASH_INT_BUF_TOTAL +\n"
        "\t\t\t\t       FMAN_EHASH_STATS_SCRATCH_SIZE +\n"
        "\t\t\t\t       FMAN_EHASH_DSCP_MAP_OFF;\n"
        "\t\tunsigned int di;\n"
        "\n"
        "\t\tfor (di = 0; di < FMAN_EHASH_DSCP_MAP_COUNT; di++)\n"
        "\t\t\tiowrite32be(pcd->fe_dscp_map[di] & 0x00ffffffU,\n"
        "\t\t\t\t    dscp_v + di * 4);\n"
        "\t}\n",
    ),
    (
        "validate dscp_fq_map preconditions in fman_pcd_ehash_add_key",
        "\tif (vlan && vlan->egress_mtu) {\n"
        "\t\tif (!(l2_dst && l2_src && eth_type) || !t->pcd->fe_frag_off)\n"
        "\t\t\treturn -EOPNOTSUPP;\n"
        "\t\tfrag_bpid = fman_pcd_frag_pool_bpid(t->pcd);\n"
        "\t\tif (frag_bpid < 0)\n"
        "\t\t\treturn frag_bpid;\n"
        "\t}\n",
        "\tif (vlan && vlan->egress_mtu) {\n"
        "\t\tif (!(l2_dst && l2_src && eth_type) || !t->pcd->fe_frag_off)\n"
        "\t\t\treturn -EOPNOTSUPP;\n"
        "\t\tfrag_bpid = fman_pcd_frag_pool_bpid(t->pcd);\n"
        "\t\tif (frag_bpid < 0)\n"
        "\t\t\treturn frag_bpid;\n"
        "\t}\n"
        "\t/* F-328: DSCP->FQID lookup requires L2/TX terminal and owned cmn_db. */\n"
        "\tif (vlan && vlan->dscp_fq_map) {\n"
        "\t\tif (!(l2_dst && l2_src && eth_type) || !t->pcd->fe_frag_off)\n"
        "\t\t\treturn -EOPNOTSUPP;\n"
        "\t}\n",
    ),
    (
        "set DSCP_FQ_MAP_ENABLE and cmn_db MURAM offset in ENQUEUE word2",
        "\t\tif (pre_off) {\n"
        "\t\t\tr[pre_off + 0] = (u8)(enqueue_off - pre_off);\n"
        "\t\t\tr[pre_off + 1] = 0x01 | (eth_type == 0x0800 ? 0x02 : 0);\n"
        "\t\t\t*(__be16 *)(r + enqueue_off + 0) =\n"
        "\t\t\t\tcpu_to_be16(vlan->egress_mtu);\n"
        "\t\t\t*(r + enqueue_off + 3) = (u8)frag_bpid;\n"
        "\t\t\t*(__be32 *)(r + enqueue_off + 12) =\n"
        "\t\t\t\tcpu_to_be32(t->pcd->fe_frag_off & 0x00ffffff);\n"
        "\t\t}\n",
        "\t\tif (pre_off) {\n"
        "\t\t\tr[pre_off + 0] = (u8)(enqueue_off - pre_off);\n"
        "\t\t\tr[pre_off + 1] = 0x01 | (eth_type == 0x0800 ? 0x02 : 0);\n"
        "\t\t\t*(__be16 *)(r + enqueue_off + 0) =\n"
        "\t\t\t\tcpu_to_be16(vlan->egress_mtu);\n"
        "\t\t\t*(r + enqueue_off + 3) = (u8)frag_bpid;\n"
        "\t\t\t*(__be32 *)(r + enqueue_off + 12) =\n"
        "\t\t\t\tcpu_to_be32(t->pcd->fe_frag_off & 0x00ffffff);\n"
        "\t\t}\n"
        "\t\t/* F-328: vendor create_enque_hm() word2 = (DSCP_FQ_MAP_ENABLE<<24) | cmn_db. */\n"
        "\t\tif (vlan && vlan->dscp_fq_map)\n"
        "\t\t\t*(__be32 *)(r + enqueue_off + 12) =\n"
        "\t\t\t\tcpu_to_be32((0x01U << 24) |\n"
        "\t\t\t\t\t    (t->pcd->fe_frag_off & 0x00ffffffU));\n",
    ),
    (
        "thread dscp_fq_map through fman_pcd_fe_flow_add",
        "\t\t\t.egress_mtu = action->egress_mtu,\t/* F-262 */\n"
        "\t\t};\n",
        "\t\t\t.egress_mtu = action->egress_mtu,\t/* F-262 */\n"
        "\t\t\t.dscp_fq_map = action->dscp_fq_map,\t/* F-328 */\n"
        "\t\t};\n",
    ),
    (
        "pass _vlan when dscp_fq_map is set",
        "\t\t\t\t\t\t (action->vlan_flags ||\n"
        "\t\t\t\t\t\t  action->egress_mtu) ? &_vlan : NULL,\n",
        "\t\t\t\t\t\t (action->vlan_flags ||\n"
        "\t\t\t\t\t\t  action->egress_mtu ||\n"
        "\t\t\t\t\t\t  action->dscp_fq_map) ? &_vlan : NULL,\n",
    ),
    (
        "implement and export fman_pcd_dscp_map_set and clear_all",
        "EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_add);\n",
        "EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_add);\n"
        "\n"
        "/* F-328 (G10 Phase 3): update one DSCP->FQID slot in shadow + MURAM. */\n"
        "int fman_pcd_dscp_map_set(struct fman *fm, u8 dscp, u32 fqid)\n"
        "{\n"
        "\tstruct fman_pcd *pcd;\n"
        "\tstruct muram_info *muram;\n"
        "\tu32 val = fqid & 0x00ffffffU;\n"
        "\n"
        "\tif (!fm || dscp >= FMAN_EHASH_DSCP_MAP_COUNT)\n"
        "\t\treturn -EINVAL;\n"
        "\tpcd = fman_get_pcd(fm);\n"
        "\tif (!pcd)\n"
        "\t\treturn -ENODEV;\n"
        "\tmutex_lock(&pcd->fe_lock);\n"
        "\tpcd->fe_dscp_map[dscp] = val;\n"
        "\tif (pcd->fe_frag_off) {\n"
        "\t\tmuram = fman_get_muram(pcd->fman);\n"
        "\t\tif (muram) {\n"
        "\t\t\tvoid __iomem *v = (void __iomem *)(void *)\n"
        "\t\t\t\tfman_muram_offset_to_vbase(muram,\n"
        "\t\t\t\t\tpcd->fe_frag_off +\n"
        "\t\t\t\t\tFMAN_EHASH_DSCP_MAP_OFF +\n"
        "\t\t\t\t\t(unsigned long)dscp * 4);\n"
        "\t\t\tiowrite32be(val, v);\n"
        "\t\t}\n"
        "\t}\n"
        "\tmutex_unlock(&pcd->fe_lock);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_dscp_map_set);\n"
        "\n"
        "/* F-328 (G10 Phase 3): zero all 64 DSCP->FQID slots in shadow + MURAM. */\n"
        "void fman_pcd_dscp_map_clear_all(struct fman *fm)\n"
        "{\n"
        "\tstruct fman_pcd *pcd;\n"
        "\tstruct muram_info *muram;\n"
        "\tunsigned int i;\n"
        "\n"
        "\tif (!fm)\n"
        "\t\treturn;\n"
        "\tpcd = fman_get_pcd(fm);\n"
        "\tif (!pcd)\n"
        "\t\treturn;\n"
        "\tmutex_lock(&pcd->fe_lock);\n"
        "\tmemset(pcd->fe_dscp_map, 0, sizeof(pcd->fe_dscp_map));\n"
        "\tif (pcd->fe_frag_off) {\n"
        "\t\tmuram = fman_get_muram(pcd->fman);\n"
        "\t\tif (muram) {\n"
        "\t\t\tvoid __iomem *v = (void __iomem *)(void *)\n"
        "\t\t\t\tfman_muram_offset_to_vbase(muram,\n"
        "\t\t\t\t\tpcd->fe_frag_off +\n"
        "\t\t\t\t\tFMAN_EHASH_DSCP_MAP_OFF);\n"
        "\t\t\tfor (i = 0; i < FMAN_EHASH_DSCP_MAP_COUNT; i++)\n"
        "\t\t\t\tiowrite32be(0, v + i * 4);\n"
        "\t\t}\n"
        "\t}\n"
        "\tmutex_unlock(&pcd->fe_lock);\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_dscp_map_clear_all);\n",
    ),
]

DPAA_ETH_EDITS = [
    (
        "dispatch clsact egress block to dpaa_ceetm_setup_dscp_block",
        "\tif (f->binder_type != FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS)\n"
        "\t\treturn -EOPNOTSUPP;\n",
        "\t/* F-328 (G10 Phase 3): clsact egress flower ip_dscp -> CEETM CQ map. */\n"
        "\tif (f->binder_type == FLOW_BLOCK_BINDER_TYPE_CLSACT_EGRESS)\n"
        "\t\treturn dpaa_ceetm_setup_dscp_block(net_dev, f);\n"
        "\tif (f->binder_type != FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS)\n"
        "\t\treturn -EOPNOTSUPP;\n",
    ),
    (
        "release or poison DSCP singleton owner before free_netdev in dpaa_remove",
        "\tunregister_netdev(net_dev);\n",
        "\tunregister_netdev(net_dev);\n"
        "\t/* F-328: never leave dpaa_ceetm_dscp_owner pointing at a freed netdev. */\n"
        "\trtnl_lock();\n"
        "\tdpaa_ceetm_dscp_netdev_unregister(net_dev);\n"
        "\trtnl_unlock();\n",
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


apply_one(FMAN_PCD_H, FMAN_PCD_H_EDITS)
apply_one(FMAN_PCD_C, FMAN_PCD_C_EDITS)
apply_one(DPAA_ETH_C, DPAA_ETH_EDITS)

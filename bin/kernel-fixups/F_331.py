"""F-331 (G10 Phase 5b): QMan CEETM WBFS group & weight API declarations.

1. drivers/soc/fsl/qbman/qman_priv.h:
     - add CEETM class-scheduler GPC bit masks (CEETM_GPC_COMBINE,
       CEETM_GPC_PRIO_B_SHIFT/MASK, CEETM_GPC_PRIO_A_MASK) and group
       eligibility bits (GROUP_A_ELIGIBILITY_SET = 1<<8,
       GROUP_B_ELIGIBILITY_SET = 1<<9) next to CQ_ELIGIBILITY_SET(n).
2. include/soc/fsl/qman.h:
     - add struct qm_ceetm_weight_code (5-bit y + 3-bit x).
     - declare WBFS primitives implemented in qman_ceetm.c:
       qman_ceetm_channel_set_group(), qman_ceetm_channel_get_group(),
       qman_ceetm_channel_set_group_cr_eligibility(),
       qman_ceetm_channel_set_group_er_eligibility(),
       qman_ceetm_cq_claim_A(), qman_ceetm_cq_claim_B(),
       qman_ceetm_set_queue_weight(), qman_ceetm_get_queue_weight(),
       qman_ceetm_ratio2wbfs(), qman_ceetm_wbfs2ratio(), and
       qman_ceetm_cq_get_frm_cnt().

Must run after 0111 and F-327. Idempotent; count-gated anchors.
"""

import sys

QMAN_PRIV_H = "drivers/soc/fsl/qbman/qman_priv.h"
QMAN_H = "include/soc/fsl/qman.h"
DPAA_ETH_H = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.h"
DPAA_ETH_SYSFS_C = "drivers/net/ethernet/freescale/dpaa/dpaa_eth_sysfs.c"
MARK = "F-331"

QMAN_PRIV_H_EDITS = [
    (
        "add WBFS group priority and eligibility macros",
        "/* class-scheduler eligibility-mask bit for CQ 'n' */\n"
        "#define CQ_ELIGIBILITY_SET(n)\t(1 << (7 - (n)))\n",
        "/* class-scheduler eligibility-mask bits (strict CQ 0..7 + WBFS groups A/B) */\n"
        "#define CQ_ELIGIBILITY_SET(n)\t(1 << (7 - (n)))\n"
        "#define GROUP_A_ELIGIBILITY_SET\t(1 << 8)\t/* F-331 */\n"
        "#define GROUP_B_ELIGIBILITY_SET\t(1 << 9)\t/* F-331 */\n"
        "\n"
        "/* class-scheduler GPC byte: combine(bit6) | prio_b(bits5..3) | prio_a(bits2..0) */\n"
        "#define CEETM_GPC_COMBINE\t0x40U\n"
        "#define CEETM_GPC_PRIO_B_SHIFT\t3U\n"
        "#define CEETM_GPC_PRIO_B_MASK\t0x38U\n"
        "#define CEETM_GPC_PRIO_A_MASK\t0x07U\n",
    ),
]

QMAN_H_EDITS = [
    (
        "define struct qm_ceetm_weight_code",
        "#define QM_CEETM_RATE_INFINITE\t((struct qm_ceetm_rate){ 0x7ff, 0x1fff })\n",
        "#define QM_CEETM_RATE_INFINITE\t((struct qm_ceetm_rate){ 0x7ff, 0x1fff })\n"
        "\n"
        "/* F-331: CEETM WBFS grouped class-queue weight code (5-bit y, 3-bit x) */\n"
        "struct qm_ceetm_weight_code {\n"
        "\tu8 y:5;\n"
        "\tu8 x:3;\n"
        "};\n",
    ),
    (
        "declare WBFS group, CQ claim_A/B, weight, and CQ frm_cnt helpers",
        "int qman_ceetm_drain_cq(struct qm_ceetm_cq *cq);\n",
        "int qman_ceetm_drain_cq(struct qm_ceetm_cq *cq);\n"
        "/* F-331 (G10 Phase 5b): CEETM WBFS group, weight, and CQ backlog API */\n"
        "int qman_ceetm_channel_set_group(struct qm_ceetm_channel *channel,\n"
        "\t\t\t\t int group_b, unsigned int prio_a,\n"
        "\t\t\t\t unsigned int prio_b);\n"
        "int qman_ceetm_channel_get_group(struct qm_ceetm_channel *channel,\n"
        "\t\t\t\t int *group_b, unsigned int *prio_a,\n"
        "\t\t\t\t unsigned int *prio_b);\n"
        "int qman_ceetm_channel_set_group_cr_eligibility(struct qm_ceetm_channel *channel,\n"
        "\t\t\t\t\t\tint group_b, int cre);\n"
        "int qman_ceetm_channel_set_group_er_eligibility(struct qm_ceetm_channel *channel,\n"
        "\t\t\t\t\t\tint group_b, int ere);\n"
        "int qman_ceetm_cq_claim_A(struct qm_ceetm_cq **cq,\n"
        "\t\t\t  struct qm_ceetm_channel *channel, unsigned int idx,\n"
        "\t\t\t  struct qm_ceetm_ccg *ccg);\n"
        "int qman_ceetm_cq_claim_B(struct qm_ceetm_cq **cq,\n"
        "\t\t\t  struct qm_ceetm_channel *channel, unsigned int idx,\n"
        "\t\t\t  struct qm_ceetm_ccg *ccg);\n"
        "int qman_ceetm_set_queue_weight(struct qm_ceetm_cq *cq,\n"
        "\t\t\t\tconst struct qm_ceetm_weight_code *weight_code);\n"
        "int qman_ceetm_get_queue_weight(struct qm_ceetm_cq *cq,\n"
        "\t\t\t\tstruct qm_ceetm_weight_code *weight_code);\n"
        "int qman_ceetm_ratio2wbfs(u32 numerator, u32 denominator,\n"
        "\t\t\t  struct qm_ceetm_weight_code *weight_code,\n"
        "\t\t\t  int rounding);\n"
        "int qman_ceetm_wbfs2ratio(const struct qm_ceetm_weight_code *weight_code,\n"
        "\t\t\t  u32 *numerator, u32 *denominator);\n"
        "int qman_ceetm_cq_get_frm_cnt(struct qm_ceetm_cq *cq, u32 *frm_cnt);\n"
        "int qman_ceetm_cq_release_drained(struct qm_ceetm_cq *cq);\n",
    ),
]


def apply_one(path: str, edits: list[tuple[str, str, str]]) -> None:
    with open(path, "r") as fh:
        text = fh.read()
    if MARK in text:
        for label, _target, replacement in edits:
            if replacement not in text:
                sys.exit(f"### {MARK}: {path}: marker present but '{label}' missing")
        print(f"### {MARK}: {path} already applied")
        return
    for label, target, replacement in edits:
        n = text.count(target)
        if n != 1:
            sys.exit(f"### {MARK}: {path}: anchor '{label}' matched {n} times (expected 1)")
        text = text.replace(target, replacement, 1)
    with open(path, "w") as fh:
        fh.write(text)
    print(f"### {MARK}: patched {path} ({len(edits)} anchor(s))")


DPAA_ETH_H_EDITS = [
    (
        "add ceetm_wbfs_hash opt-in flag in struct dpaa_priv",
        "\tbool ceetm_cgr_congested;\t/* F-330 (A458): track CGR state while paused */\n"
        "#endif\n",
        "\tbool ceetm_cgr_congested;\t/* F-330 (A458): track CGR state while paused */\n"
        "\tbool ceetm_wbfs_hash;\t\t/* F-331: opt-in equal-weight WBFS defcls hashing */\n"
        "#endif\n",
    ),
]

DPAA_ETH_SYSFS_C_EDITS = [
    (
        "include dpaa_ceetm.h in dpaa_eth_sysfs.c",
        "#include \"dpaa_eth.h\"\n"
        "#include \"mac.h\"\n",
        "#include \"dpaa_eth.h\"\n"
        "#include \"dpaa_ceetm.h\"\t/* F-331 */\n"
        "#include \"mac.h\"\n",
    ),
    (
        "add ceetm_wbfs_hash sysfs show/store and attribute",
        "static struct device_attribute dpaa_eth_attrs[] = {\n"
        "\t__ATTR(device_addr, 0444, dpaa_eth_show_addr, NULL),\n"
        "\t__ATTR(fqids, 0444, dpaa_eth_show_fqids, NULL),\n"
        "\t__ATTR(bpids, 0444, dpaa_eth_show_bpids, NULL),\n"
        "};\n",
        "#if IS_ENABLED(CONFIG_DPAA_HW_CEETM)\n"
        "static ssize_t dpaa_eth_show_ceetm_wbfs_hash(struct device *dev,\n"
        "\t\t\t\t\t     struct device_attribute *attr,\n"
        "\t\t\t\t\t     char *buf)\n"
        "{\n"
        "\treturn dpaa_ceetm_wbfs_hash_show(to_net_dev(dev), buf);\n"
        "}\n"
        "\n"
        "static ssize_t dpaa_eth_store_ceetm_wbfs_hash(struct device *dev,\n"
        "\t\t\t\t\t      struct device_attribute *attr,\n"
        "\t\t\t\t\t      const char *buf, size_t count)\n"
        "{\n"
        "\treturn dpaa_ceetm_wbfs_hash_store(to_net_dev(dev), buf, count);\n"
        "}\n"
        "#endif\n"
        "\n"
        "static struct device_attribute dpaa_eth_attrs[] = {\n"
        "\t__ATTR(device_addr, 0444, dpaa_eth_show_addr, NULL),\n"
        "\t__ATTR(fqids, 0444, dpaa_eth_show_fqids, NULL),\n"
        "\t__ATTR(bpids, 0444, dpaa_eth_show_bpids, NULL),\n"
        "#if IS_ENABLED(CONFIG_DPAA_HW_CEETM)\n"
        "\t__ATTR(ceetm_wbfs_hash, 0644, dpaa_eth_show_ceetm_wbfs_hash,\n"
        "\t       dpaa_eth_store_ceetm_wbfs_hash),\n"
        "#endif\n"
        "};\n",
    ),
]


def main() -> int:
    apply_one(QMAN_PRIV_H, QMAN_PRIV_H_EDITS)
    apply_one(QMAN_H, QMAN_H_EDITS)
    apply_one(DPAA_ETH_H, DPAA_ETH_H_EDITS)
    apply_one(DPAA_ETH_SYSFS_C, DPAA_ETH_SYSFS_C_EDITS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

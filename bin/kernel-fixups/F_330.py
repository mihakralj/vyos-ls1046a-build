"""F-330 (G10 Phase 5a + A458): Wire TC_SETUP_QDISC_RED & CEETM TX pause gate.

1. drivers/net/ethernet/freescale/dpaa/dpaa_eth.h:
     - add `bool ceetm_tx_paused` next to `priv->ceetm` so `dpaa_eth_cgscn()`
       cannot asynchronously wake TX queues while `dpaa_ceetm_htb_create()` is
       draining plain WQ TX FQs (A458 P1-1 safety fix).
2. drivers/net/ethernet/freescale/dpaa/dpaa_eth.c:
     - guard `netif_tx_wake_all_queues()` inside `dpaa_eth_cgscn()` with
       `!READ_ONCE(priv->ceetm_tx_paused)`.
     - dispatch `TC_SETUP_QDISC_RED` in `dpaa_setup_tc()` to
       `dpaa_ceetm_setup_red()` for frame-mode QMan CEETM CCGR hardware WRED
       on offloaded HTB leaf classes (strictly rejecting ECN/nodrop).

Must run after 0112 and F-327. Idempotent; count-gated anchors.
"""

import sys

DPAA_ETH_H = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.h"
DPAA_ETH_C = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
MARK = "F-330"

DPAA_ETH_H_EDITS = [
    (
        "add ceetm_tx_paused and ceetm_cgr_congested flags next to priv->ceetm",
        "\tstruct dpaa_ceetm *ceetm;\n",
        "\tstruct dpaa_ceetm *ceetm;\n"
        "\tbool ceetm_tx_paused;\t/* F-330 (A458): block cgscn TX wake during drain */\n"
        "\tbool ceetm_cgr_congested;\t/* F-330 (A458): track CGR state while paused */\n",
    ),
]

DPAA_ETH_C_EDITS = [
    (
        "record CGR congested state in dpaa_eth_cgscn",
        "\tif (priv && congested) {\n"
        "\t\tpriv->cgr_data.congestion_start_jiffies = jiffies;\n"
        "\t\tnetif_tx_stop_all_queues(priv->net_dev);\n",
        "\tif (priv && congested) {\n"
        "#if IS_ENABLED(CONFIG_DPAA_HW_CEETM)\n"
        "\t\tWRITE_ONCE(priv->ceetm_cgr_congested, true);\t/* F-330 (A458) */\n"
        "#endif\n"
        "\t\tpriv->cgr_data.congestion_start_jiffies = jiffies;\n"
        "\t\tnetif_tx_stop_all_queues(priv->net_dev);\n",
    ),
    (
        "gate dpaa_eth_cgscn netif_tx_wake_all_queues on !ceetm_tx_paused",
        "\t\tpriv->cgr_data.congested_jiffies +=\n"
        "\t\t\t(jiffies - priv->cgr_data.congestion_start_jiffies);\n"
        "\t\tnetif_tx_wake_all_queues(priv->net_dev);\n",
        "\t\tpriv->cgr_data.congested_jiffies +=\n"
        "\t\t\t(jiffies - priv->cgr_data.congestion_start_jiffies);\n"
        "#if IS_ENABLED(CONFIG_DPAA_HW_CEETM)\n"
        "\t\tWRITE_ONCE(priv->ceetm_cgr_congested, false);\t/* F-330 (A458) */\n"
        "\t\tsmp_mb();\n"
        "\t\tif (!READ_ONCE(priv->ceetm_tx_paused))\t/* F-330 (A458) */\n"
        "#endif\n"
        "\t\t\tnetif_tx_wake_all_queues(priv->net_dev);\n",
    ),
    (
        "dispatch TC_SETUP_QDISC_RED to dpaa_ceetm_setup_red in dpaa_setup_tc",
        "\tcase TC_SETUP_QDISC_HTB:\n"
        "\t\treturn dpaa_ceetm_setup_htb(net_dev, type_data);\n",
        "\tcase TC_SETUP_QDISC_HTB:\n"
        "\t\treturn dpaa_ceetm_setup_htb(net_dev, type_data);\n"
        "\tcase TC_SETUP_QDISC_RED:\t/* F-330: G10 Phase 5a frame-mode WRED */\n"
        "\t\treturn dpaa_ceetm_setup_red(net_dev, type_data);\n",
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


def main() -> int:
    apply_one(DPAA_ETH_H, DPAA_ETH_H_EDITS)
    apply_one(DPAA_ETH_C, DPAA_ETH_C_EDITS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

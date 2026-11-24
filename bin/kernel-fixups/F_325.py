"""F-325: Fix upstream fman_memac memac_set_allmulti(false) wiping active multicast buckets.

In upstream drivers/net/ethernet/freescale/fman/fman_memac.c, disabling allmulti
(e.g. when a DPAA port leaves a Linux bridge or runs `ip link set dev ethX
allmulticast off`) unconditionally clears HASH_CTRL_MCAST_EN across all 64
hardware buckets of regs->hashtable_ctrl, ignoring buckets that still have active
entries in memac->multicast_addr_hash->lsts[entry].

Because dpaa_set_rx_mode() syncs multicast addresses via delta-only
__dev_mc_sync(), existing subscriptions (notably IPv6 solicited-node multicast
33:33:ff:xx:xx:xx) are never re-added, permanently breaking IPv6 Neighbor
Discovery on any DPAA port that ever leaves a bridge.

Preserve HASH_CTRL_MCAST_EN for every bucket with a non-empty software list when
disabling allmulti; clear only empty buckets (including those whose last address
was deleted while allmulti_enabled was true).

Idempotent; count-gated anchor.
"""

import sys

MEMAC_C = "drivers/net/ethernet/freescale/fman/fman_memac.c"
MARK = "F-325"

MEMAC_EDITS = [
    (
        "memac_set_allmulti(false) preserve active multicast_addr_hash buckets",
        "\t} else {\n"
        "\t\tfor (entry = 0; entry < HASH_TABLE_SIZE; entry++)\n"
        "\t\t\tiowrite32be(entry & ~HASH_CTRL_MCAST_EN,\n"
        "\t\t\t\t    &regs->hashtable_ctrl);\n"
        "\t}\n",
        "\t} else {\n"
        "\t\tfor (entry = 0; entry < HASH_TABLE_SIZE; entry++) {\n"
        "\t\t\tu32 val = entry & ~HASH_CTRL_MCAST_EN;\n"
        "\n"
        "\t\t\t/* F-325: preserve buckets still backed by active\n"
        "\t\t\t * multicast_addr_hash entries (e.g. IPv6 solicited-node\n"
        "\t\t\t * multicast after leaving a bridge).\n"
        "\t\t\t */\n"
        "\t\t\tif (memac->multicast_addr_hash &&\n"
        "\t\t\t    memac->multicast_addr_hash->lsts &&\n"
        "\t\t\t    entry < memac->multicast_addr_hash->size &&\n"
        "\t\t\t    !list_empty(&memac->multicast_addr_hash->lsts[entry]))\n"
        "\t\t\t\tval = entry | HASH_CTRL_MCAST_EN;\n"
        "\t\t\tiowrite32be(val, &regs->hashtable_ctrl);\n"
        "\t\t}\n"
        "\t}\n",
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


apply_one(MEMAC_C, MEMAC_EDITS)

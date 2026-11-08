#!/usr/bin/env python3
"""F-294: keep DPAA netdev ifindex identity stable for cached egress FQs."""

import sys

DPAA = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
OLD = (
    "\tSET_NETDEV_DEV(net_dev, dev->parent);\n"
    "\tdev_set_drvdata(dev, net_dev);\n"
)
NEW = (
    "\tSET_NETDEV_DEV(net_dev, dev->parent);\n"
    "\t/* Egress FQ caches use ifindex identity; physical ports stay in init_net. */\n"
    "\tnet_dev->netns_immutable = true;\n"
    "\tdev_set_drvdata(dev, net_dev);\n"
)


def main():
    try:
        with open(DPAA) as stream:
            source = stream.read()
    except FileNotFoundError:
        print(f"### F-294: FATAL: {DPAA} not found")
        return 1

    assignment = "net_dev->netns_immutable = true;"
    if assignment in source:
        if source.count(assignment) == 1 and \
                source.count("Egress FQ caches use ifindex identity") == 1:
            print("### F-294: already applied")
            return 0
        print("### F-294: FATAL: partially applied tree")
        return 1

    count = source.count(OLD)
    if count != 1:
        print(f"### F-294: FATAL: anchor found {count} times (expected 1)")
        return 1

    with open(DPAA, "w") as stream:
        stream.write(source.replace(OLD, NEW))
    print("### F-294: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())

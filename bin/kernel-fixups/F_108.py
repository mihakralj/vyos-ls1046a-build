"""F_108: Ratelimit 'Err FD status = 0x...' console spam in dpaa_eth.c

Physical frame errors (CRC, runt, PHY noise) on eth0 generate FD status = 0x00000020.
Without ratelimiting, every errored frame emits a netif_err/netdev_err to console.
"""

import os

changes = 0
DPAA_ETH = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"

if os.path.exists(DPAA_ETH):
    with open(DPAA_ETH) as f:
        s = f.read()

    # Match netif_err or netdev_err calls logging "Err FD status"
    if "Err FD status = 0x%08x" in s:
        # Replace netif_err with netif_err_ratelimited or netif_dbg
        old1 = 'netif_err(priv, rx_err, net_dev, "Err FD status = 0x%08x\\n"'
        new1 = 'netif_err_ratelimited(priv, rx_err, net_dev, "Err FD status = 0x%08x\\n"'

        old2 = 'netdev_err(net_dev, "Err FD status = 0x%08x\\n"'
        new2 = 'netdev_err_ratelimited(net_dev, "Err FD status = 0x%08x\\n"'

        if old1 in s:
            s = s.replace(old1, new1)
            changes += 1
            print("### F_108: Ratelimited netif_err for Err FD status in dpaa_eth.c")
        elif old2 in s:
            s = s.replace(old2, new2)
            changes += 1
            print("### F_108: Ratelimited netdev_err for Err FD status in dpaa_eth.c")
        else:
            s = s.replace('netif_err(priv, rx_err, net_dev, "Err FD status', 'netif_err_ratelimited(priv, rx_err, net_dev, "Err FD status')
            s = s.replace('netdev_err(net_dev, "Err FD status', 'netdev_err_ratelimited(net_dev, "Err FD status')
            changes += 1
            print("### F_108: Ratelimited Err FD status in dpaa_eth.c (fallback)")
    else:
        print("### F_108: WARNING 'Err FD status' anchor not found in dpaa_eth.c")

    with open(DPAA_ETH, 'w') as f:
        f.write(s)

if changes:
    print("### F_108: %d change(s) applied" % changes)
else:
    print("### F_108: WARNING no changes applied")

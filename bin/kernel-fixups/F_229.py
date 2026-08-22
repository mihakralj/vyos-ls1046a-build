"""F-229: force symmetric pause flow control on 1G RJ45 SGMII links.

ISSUE #45 (2026-08-22, reporter AlbertSPedersen, kernel 6.18.44-vyos): the 1G
RJ45 LAN port (eth1, FMan MEMAC, SGMII, external Maxlinear GPY115C PHY) reports
`Link is Down` then `Link is Up - 1Gbps/Full - flow control off` a few seconds
later, but ONLY under sustained LAN->WAN packet-forwarding load. Router-originated
TX does not trigger it; WAN connectivity and CPU remain healthy.

SOURCE-GROUNDED DIAGNOSIS:
  * The carrier flap is PHY-reported. In MLO_AN_PHY mode, phylink copies the PHY
    state verbatim. These PHYs run in POLL mode, so gpy_read_status() reads a real
    loss from PHY_MIISTAT_LS/BMSR; no FMan/MEMAC exception, DPAA buffer failure,
    or TX-timeout path calls netif_carrier_off().
  * The link comes up with 802.3x flow control OFF. Thus sustained RX forwarding
    has no ingress backpressure when the forwarding/TX side briefly stalls. The
    direction asymmetry (heavy eth1 RX fails; router-sourced TX does not) matches.
  * EEE is not driven by this MAC: memac_link_up() contains only `TODO: EEE?`
    and phylink's mac_supports_eee is not enabled.

FIX: after phylink attaches the external PHY in dpaa_open(), but before enabling
FMan ports and starting traffic, use the public phylink pause API to select manual
symmetric pause (rx=1, tx=1, pause autoneg=0) on SGMII links. This is equivalent
to `ethtool -A <if> autoneg off rx on tx on`, but provides a safe boot default
for all three GPY115C RJ45 ports. phylink updates its advertised pause bits and
the PHY, and memac_link_up() receives tx_pause=rx_pause=true, programming both
TX pause generation and RX pause acceptance. The 10G XGMII SFP+ ports eth3/eth4
(and therefore the ASK FE offload path) are not matched and remain unchanged.

The call is made from ndo_open(), which runs under RTNL as required by
phylink_ethtool_set_pauseparam(). Failure is fail-closed: disconnect the PHY and
return the error rather than silently booting the known-unsafe pause-off state.
The link-up log becomes `flow control rx/tx`, giving direct runtime verification.

S0 QDRANT GATE: cross-checked MEMAC pause-quanta/threshold registers,
COMMAND_CONFIG PAUSE_IGN/PAUSE_FWD, MAC_SYM_PAUSE|MAC_ASYM_PAUSE capabilities,
phylink_ethtool_set_pauseparam() semantics, and ASK's eth3/eth4-only 10G path.
No conflict.

Idempotent (marker guard). Count-gated exact anchor in dpaa_open().
"""

import os
import sys

path = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
if not os.path.exists(path):
    print("### F-229: dpaa_eth.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

MARKER = "F-229(force-sgmii-pause)"
if MARKER in src:
    print("### F-229: already applied")
    sys.exit(0)

old = (
    "\terr = phylink_of_phy_connect(mac_dev->phylink,\n"
    "\t\t\t\t     mac_dev->dev->of_node, 0);\n"
    "\tif (err)\n"
    "\t\tgoto phy_init_failed;\n"
    "\n"
    "\tfor (i = 0; i < ARRAY_SIZE(mac_dev->port); i++) {\n"
)

new = (
    "\terr = phylink_of_phy_connect(mac_dev->phylink,\n"
    "\t\t\t\t     mac_dev->dev->of_node, 0);\n"
    "\tif (err)\n"
    "\t\tgoto phy_init_failed;\n"
    "\n"
    "\t/* F-229(force-sgmii-pause): issue #45 -- the GPY115C 1G RJ45\n"
    "\t * links resolved with flow control off and lost carrier under sustained\n"
    "\t * ingress forwarding load. Select manual symmetric 802.3x pause after\n"
    "\t * PHY attach, before enabling traffic. The 10G XGMII ports are untouched.\n"
    "\t */\n"
    "\tif (mac_dev->phy_if == PHY_INTERFACE_MODE_SGMII) {\n"
    "\t\tstruct ethtool_pauseparam pause = {\n"
    "\t\t\t.autoneg = 0,\n"
    "\t\t\t.rx_pause = 1,\n"
    "\t\t\t.tx_pause = 1,\n"
    "\t\t};\n"
    "\n"
    "\t\terr = phylink_ethtool_set_pauseparam(mac_dev->phylink, &pause);\n"
    "\t\tif (err) {\n"
    "\t\t\tnetif_err(priv, ifup, net_dev,\n"
    "\t\t\t\t  \"failed to enable symmetric pause: %d\\n\", err);\n"
    "\t\t\tphylink_disconnect_phy(mac_dev->phylink);\n"
    "\t\t\tgoto phy_init_failed;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\tfor (i = 0; i < ARRAY_SIZE(mac_dev->port); i++) {\n"
)

if old not in src:
    print("### F-229: FATAL: dpaa_open() PHY-connect anchor not found (source drifted)")
    sys.exit(1)
if src.count(old) != 1:
    print(f"### F-229: FATAL: dpaa_open() anchor not unique ({src.count(old)} matches)")
    sys.exit(1)

src = src.replace(old, new, 1)

with open(path, "w") as f:
    f.write(src)

print("### dpaa_eth.c: F-229 force symmetric pause on 1G SGMII links (issue #45)")

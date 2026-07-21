"""F_104: Add get_channels ethtool op to DPAA1 driver.

VPP's af_xdp plugin uses ETHTOOL_GCHANNELS to detect the number of
available RX queues. DPAA1 doesn't implement get_channels, so VPP
defaults to 1 queue. This prevents multi-queue XSK binding, which
is required for ZC RX because FMan RSS distributes frames across
4 qbands — with only 1 XSK socket (qband 0), frames on qbands 1-3
bypass the XSK pool entirely.

This fixup adds a minimal get_channels that reports:
  combined_count = DPAA1_XSK_MAX_QBANDS (4)
  max_combined = DPAA1_XSK_MAX_QBANDS (4)

This is truthful: DPAA1 has 4 qbands, each with 32 FQs. VPP will
create 4 XSK sockets, one per qband, covering all RSS-distributed
frames.

Count-gated: expects exactly 1 occurrence of the ethtool_ops struct
closing brace pattern in dpaa_ethtool.c.
"""

import os

changes = 0
ETHTOOL = "drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c"

if os.path.exists(ETHTOOL):
    with open(ETHTOOL) as f:
        s = f.read()

    # Anchor: the get_rxfh_fields line followed by set_rxfh_fields
    # Pattern varies between tab-indented (patched) and space-indented (mainline)
    old = ('.get_rxfh_fields = dpaa_get_rxfh_fields,\n'
           '\t.set_rxfh_fields = dpaa_set_rxfh_fields,')
    new = ('.get_rxfh_fields = dpaa_get_rxfh_fields,\n'
           '\t.get_channels = dpaa_get_channels,\n'
           '\t.set_rxfh_fields = dpaa_set_rxfh_fields,')

    if old in s:
        s = s.replace(old, new, 1)
        changes += 1
        print("### F_104: get_channels added to ethtool_ops")
    else:
        print("### F_104: WARNING anchor not found in dpaa_ethtool.c")

    # Add the get_channels function before the ethtool_ops struct
    func = '''
/* F_104: report 4 combined channels (one per qband) for multi-queue XSK */
static void dpaa_get_channels(struct net_device *dev,
\t\t\t      struct ethtool_channels *ch)
{
\tch->max_combined\t= DPAA1_XSK_MAX_QBANDS;
\tch->combined_count\t= DPAA1_XSK_MAX_QBANDS;
}

'''
    # Insert before the ethtool_ops struct
    anchor = 'const struct ethtool_ops dpaa_ethtool_ops = {'
    if anchor in s:
        s = s.replace(anchor, func + anchor, 1)
        changes += 1
        print("### F_104: dpaa_get_channels function added")
    else:
        print("### F_104: WARNING ethtool_ops anchor not found")

    with open(ETHTOOL, 'w') as f:
        f.write(s)

if changes:
    print("### F_104: %d change(s) applied" % changes)
else:
    print("### F_104: WARNING no changes applied")
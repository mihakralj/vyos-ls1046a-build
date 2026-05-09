#!/usr/bin/env python3
"""
Patch phylink.c: trust SFP link state over PCS in INBAND mode.

Problem: On NXP LS1046A FMan 10G MACs with managed="in-band-status" and SFP
modules, the Lynx XFI PCS pcs_get_state() may return link=false even when
the SFP module has deasserted LOS and the SFP state machine has reported
link_up. This regression in kernel 6.6.130+ causes permanent NO-CARRIER on
SFP+ ports (eth3/eth4).

Root cause: phylink_resolve() in MLO_AN_INBAND case requires PCS confirmation
before calling mac_link_up(). FMan MEMAC XFI PCS (Lynx PCS reading clause-45
MDIO) fails to detect 10GBASE-R block sync on LS1046A.

Fix: In phylink_resolve()'s INBAND case, after PCS state read and before the
PHY union check, insert: if PCS says link=false but an SFP bus is attached,
override link to true. This is safe because:
- SFP link_down sets PHYLINK_DISABLE_LINK before we reach INBAND case
  (phylink_disable_state is checked at resolver entry → forces link=false)
- By the time we reach MLO_AN_INBAND, PHYLINK_DISABLE_LINK is clear,
  meaning SFP has called link_up (module present, LOS deasserted)
- Only affects INBAND mode with SFP — fixed-link and PHY modes unaffected
"""
import sys

if len(sys.argv) < 2:
    print("Usage: patch-phylink.py <path/to/phylink.c>")
    sys.exit(1)

filepath = sys.argv[1]

with open(filepath, 'r') as f:
    content = f.read()

# Insertion point: right before the PHY union check in phylink_resolve().
# This comment is unique in phylink.c and appears after PCS state reads.
marker = '/* If we have a phy, the "up" state is the union of'

idx = content.find(marker)
if idx < 0:
    print("WARNING: phylink.c patch marker not found — SFP trust fix NOT applied")
    sys.exit(0)

# Check if already patched
if 'VyOS: trust SFP link over PCS' in content:
    print("I: phylink.c already patched — skipping")
    sys.exit(0)

patch = (
    '\t\t\t/* VyOS: trust SFP link over PCS in INBAND mode (LS1046A XFI fix) */\n'
    '\t\t\tif (!link_state.link && pl->sfp_bus)\n'
    '\t\t\t\tlink_state.link = true;\n'
    '\n\t\t\t'
)

content = content[:idx] + patch + content[idx:]

with open(filepath, 'w') as f:
    f.write(content)

print("I: Patched phylink.c — SFP trust override inserted before phy union check")
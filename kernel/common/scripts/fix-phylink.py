#!/usr/bin/env python3
"""Insert phylink SFP in-band fallback into phylink_resolve().

After phylink_mac_pcs_get_state() in the MLO_AN_INBAND polling path,
if PCS reports no link but an SFP bus is present, force link=true.
Fixes NO-CARRIER on kernel 6.18 for rollball-copper SFPs.
"""

import os, sys

PYLINK = "drivers/net/phy/phylink.c"
if not os.path.exists(PYLINK):
    print(f"ERROR: {PYLINK} not found — wrong working directory?", file=sys.stderr)
    sys.exit(1)

src = open(PYLINK).read()

# Idempotency check
if "trust SFP link" in src:
    print("### phylink.c: SFP fallback already present")
    sys.exit(0)

# Unique anchor: the comment after phylink_mac_pcs_get_state()
# in the MLO_AN_INBAND polling path.
marker = '\t\t\t/* If we have a phy, the "up" state'
if marker not in src:
    print("ERROR: phylink anchor not found — upstream phylink_resolve() changed",
          file=sys.stderr)
    sys.exit(1)

# Insert brand-agnostic fallback before the anchor comment.
override = (
    '\t\t\t/* VyOS: trust SFP link over PCS in INBAND mode (LS1046A XFI fix) */\n'
    '\t\t\tif (!link_state.link && pl->sfp_bus)\n'
    '\t\t\t\tlink_state.link = true;\n\n'
)

new_src = src.replace(marker, override + marker, 1)
open(PYLINK, "w").write(new_src)

# Verify
check = open(PYLINK).read()
if "trust SFP link" in check:
    print("### phylink.c: SFP in-band fallback injected")
else:
    print("FATAL: phylink injection verification failed", file=sys.stderr)
    sys.exit(1)

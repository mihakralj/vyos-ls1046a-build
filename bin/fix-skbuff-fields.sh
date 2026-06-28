#!/bin/bash
# Inject sk_buff fields (layerscape_underlying_iif, underlying_vlan_tci,
# ipsec_offload) referenced by NXP SDK-patched consumers that lack
# CONFIG_CPE_FAST_PATH guards. Called by auto-build.yml nxp-sdk path.
set -e

SKB="${1:?usage: $0 <path/to/linux/skbuff.h>}"

python3 -c '
import sys
f = sys.argv[1]
t = open(f).read()
i = t.find("struct sk_buff {")
j = i + 16
fields = (
    "\n\tint\t\tlayerscape_underlying_iif;"
    "\n\t__u16\t\tunderlying_vlan_tci;"
    "\n\tunsigned long\tipsec_offload;"
)
open(f, "w").write(t[:j] + fields + t[j:])
' "$SKB"

echo "### Patched $SKB: injected layerscape_underlying_iif, underlying_vlan_tci, ipsec_offload"

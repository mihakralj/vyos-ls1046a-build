#!/bin/bash
# Inject sk_buff fields (layerscape_underlying_iif, underlying_vlan_tci,
# ipsec_offload) referenced by NXP SDK-patched consumers that lack
# CONFIG_CPE_FAST_PATH guards. Called by auto-build.yml nxp-sdk path.
#
# Fields are injected AFTER the _nfct member (line ~926) to avoid
# breaking the BUILD_BUG_ON assertion that requires next/prev at
# offset 0 of struct sk_buff.
set -e

SKB="${1:?usage: $0 <path/to/linux/skbuff.h>}"

python3 -c '
import sys
f = sys.argv[1]
t = open(f).read()
# Match the _nfct line and inject after it
i = t.find("\tunsigned long\t\t _nfct;\n")
if i == -1:
    print("ERROR: _nfct member not found in sk_buff", file=sys.stderr)
    sys.exit(1)
j = i + len("\tunsigned long\t\t _nfct;\n")
fields = (
    "\tint\t\tlayerscape_underlying_iif;\n"
    "\t__u16\t\tunderlying_vlan_tci;\n"
    "\tunsigned long\tipsec_offload;\n"
)
open(f, "w").write(t[:j] + fields + t[j:])
' "$SKB"

echo "### Patched $SKB: injected layerscape_underlying_iif, underlying_vlan_tci, ipsec_offload after _nfct"

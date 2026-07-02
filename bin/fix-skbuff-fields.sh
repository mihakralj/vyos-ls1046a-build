#!/bin/bash
# Inject sk_buff fields referenced by NXP SDK-patched consumers
# (kernel/flavors/ask/sdk-sources overlay) that the surviving upstream
# kernel patch series (kernel/flavors/ask/patches/010-098, see README.md)
# does not itself provide:
#
#  - layerscape_underlying_iif (int): referenced by the overlay's
#    drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_sg.c to preserve the
#    physical ingress ifindex across bridge handoff. Patch 010 adds a
#    DIFFERENT field for similar purpose (`underlying_iif`/`iif_index`),
#    but not this exact name — this overlay file's hunk from patch 010 is
#    excluded (ASK_PATCH_PATH_EXCLUDES in stage-kernel.sh), so the overlay's
#    own field name must be supplied here instead.
#  - ipsec_sa_handle (__u16): referenced unconditionally (no #ifdef guard)
#    by dpaa_eth_sg.c's dpaa_submit_outb_pkt_to_SEC() to carry the SA handle
#    for hardware IPsec-offload lookups. No surviving patch declares this
#    field (patch 040 adds `handle`/`parent_sa_handle` to struct xfrm_state,
#    not to sk_buff). Type confirmed against the old (now-deleted, see git
#    show f1af6ab -- kernel/flavors/ask/patches/724-*.patch)
#    locally-authored patch 724, which declared it identically.
#
# NOTE: underlying_vlan_tci and ipsec_offload used to be injected here too,
# but 2026-07-02's patch-series replacement (010-ask-fman-dpaa-ehash.patch)
# now declares both natively (guarded by CONFIG_CPE_FAST_PATH /
# CONFIG_INET_IPSEC_OFFLOAD||CONFIG_INET6_IPSEC_OFFLOAD respectively, both
# forced on by ci-build-packages.sh) — injecting them here too caused
# "duplicate member" compile errors (run 28565682543 → fixed here).
#
# Called by auto-build.yml nxp-sdk path. Fields are injected AFTER the
# _nfct member (line ~926) to avoid breaking the BUILD_BUG_ON assertion
# that requires next/prev at offset 0 of struct sk_buff.
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
    "\t__u16\t\tipsec_sa_handle;\n"
)
open(f, "w").write(t[:j] + fields + t[j:])
' "$SKB"

echo "### Patched $SKB: injected layerscape_underlying_iif, ipsec_sa_handle after _nfct"

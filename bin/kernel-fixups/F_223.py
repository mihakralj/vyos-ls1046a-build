"""F-223: safe eth4-only diagnostic KG-hash capture (restores hash_probe producer).

WHY: The IPv6 dual-lane key design (specs/ask2-ipv6-dual-lane-key-design.md)
must be gated on a single-port silicon proof that reads the hardware KG hash for
a controlled v4/v6 frame and compares it to crc64_raw() of candidate keys. The
debugfs `hash_probe` reader (F-071) and its globals fman_pcd_kg_hash /
fman_pcd_hash_off still exist, but F-216 REMOVED the producer (the eth4-only
be64 capture in rx_default_dqrr) because the old F-072/F-170 form was unguarded
and amplified a zero-address FD into a kernel panic (phys_to_virt(0)+0x108).
So on the shipping image hash_probe is permanently "idle" and the proof cannot
run.

WHAT: re-add the eth4-only KG-hash capture, but SAFELY, so it cannot repeat the
F-216 panic:
  - Inserted AFTER F-216's zero-address FD guard (addr==0 already rejected and
    consumed) and AFTER the mainline be32 RX-hash block, so `vaddr`,
    `hash_offset`, and `priv->keygen_in_use` are all validated/in-scope.
  - Only fires for eth4 (strcmp(net_dev->name,"eth4")), the F-072 v7 form, so it
    never touches the mgmt or production-v4 (eth3) hot path.
  - Bounds-checked: only reads when hash_offset + 8 <= DPAA_BP_RAW_SIZE, so the
    8-byte read stays inside the RX buffer (defends the page-boundary panics
    that plagued F-069).
  - Reuses the existing globals (fman_pcd_kg_hash u64, fman_pcd_hash_off uint)
    that F-216 left in place; sets fman_pcd_hash_off nonzero so hash_probe_show
    prints the captured value.
  - DIAGNOSTIC ONLY. No datapath/register/scheme/MURAM writes. No behavior
    change for valid frames beyond a single guarded 8-byte read on eth4.

This is a temporary diagnostic to run the dual-lane GEC proof; it is NOT a
production feature and should be retired once the proof is captured.

Must run AFTER F-216 (which normalized the RXHASH block and added the zero-FD
guard). Anchors on F-216's emitted be32 block. Idempotent via F-223 marker.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
if not os.path.exists(path):
    print("### F-223: dpaa_eth.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

marker = "F-223(eth4-hash-capture)"
if marker in src:
    print("### F-223: already applied")
    sys.exit(0)

# Anchor: the exact F-216-normalized mainline be32 RX-hash block. Insert the
# eth4-only diagnostic capture immediately after it (hash_offset already
# resolved by fman_port_get_hash_result_offset in the same if).
anchor = (
    "\tif (net_dev->features & NETIF_F_RXHASH && priv->keygen_in_use &&\n"
    "\t    !fman_port_get_hash_result_offset(priv->mac_dev->port[RX],\n"
    "\t\t\t\t\t      &hash_offset)) {\n"
    "\t\thash = be32_to_cpu(*(__be32 *)(vaddr + hash_offset));\n"
    "\t\thash_valid = true;\n"
    "\t}\n"
)

if anchor not in src:
    print("### F-223: FATAL: F-216-normalized RXHASH block not found — run after F-216")
    sys.exit(1)
if src.count(anchor) != 1:
    print(f"### F-223: FATAL: RXHASH anchor not unique ({src.count(anchor)})")
    sys.exit(1)

capture = (
    "\tif (net_dev->features & NETIF_F_RXHASH && priv->keygen_in_use &&\n"
    "\t    !fman_port_get_hash_result_offset(priv->mac_dev->port[RX],\n"
    "\t\t\t\t\t      &hash_offset)) {\n"
    "\t\thash = be32_to_cpu(*(__be32 *)(vaddr + hash_offset));\n"
    "\t\thash_valid = true;\n"
    "\n"
    "\t\t/* F-223(eth4-hash-capture): DIAGNOSTIC-ONLY safe restore of the\n"
    "\t\t * eth4 KG-hash producer for hash_probe (F-216 removed the unsafe\n"
    "\t\t * F-072/F-170 form). addr!=0 is guaranteed by the F-216 zero-FD\n"
    "\t\t * guard above; bound the 8-byte read to the RX buffer so a\n"
    "\t\t * page-boundary/short buffer cannot fault. eth4 only, so the mgmt\n"
    "\t\t * and eth3 production paths are untouched. Used to prove the IPv6\n"
    "\t\t * dual-lane GEC key extraction (absent-header default expansion).\n"
    "\t\t */\n"
    "\t\tif (!strcmp(net_dev->name, \"eth4\") &&\n"
    "\t\t    (size_t)hash_offset + sizeof(u64) <= DPAA_BP_RAW_SIZE) {\n"
    "\t\t\tfman_pcd_kg_hash =\n"
    "\t\t\t\tbe64_to_cpu(*(__be64 *)(vaddr + hash_offset));\n"
    "\t\t\tfman_pcd_hash_off = hash_offset ? hash_offset : 0xffffffffu;\n"
    "\t\t}\n"
    "\t}\n"
)

src = src.replace(anchor, capture, 1)

with open(path, "w") as f:
    f.write(src)

print("### dpaa_eth.c: F-223 safe eth4-only diagnostic KG-hash capture added")

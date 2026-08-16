"""F-200 (T-M7-2 S3): UPDATE_TTL opcode for correct routed IPv4 forwarding.

S1 (F-198) forwards a HIT with INSERT_L2_HDR(0x41) -> ENQUEUE_PKT(0x01) but does
NOT decrement the IPv4 TTL, so the DUT is not a correct RFC-791 router (no TTL
decrement, no traceroute/loop protection). Vendor cdx_ehash.c create_ttl_hm()
(fm_ehash.h) emits UPDATE_TTL(0x21) which decrements TTL and fixes the IPv4
header checksum in hardware.

Vendor contract (verified /mnt/build/ASK/cdx/cdx_ehash.c + fm_ehash.h):
  * create_ttl_hm() = insert_opcodeonly_hm(UPDATE_TTL) THEN
    create_update_dscp_hm(UPDATE_TTL).
  * insert_opcodeonly_hm writes ONE opcode byte 0x21 into the opcode list.
  * create_update_dscp_hm ALWAYS consumes a 4-byte en_ehash_update_dscp param
    for UPDATE_TTL (writes dscp=0 when no DSCP marking). So UPDATE_TTL costs
    1 opcode byte + 4 param bytes.
  * Plain routed IPv4 order (fill_actions): UPDATE_TTL(0x21) -> INSERT_L2_HDR
    (0x41) -> ENQUEUE_PKT(0x01); params in emission order: DSCP(4) -> L2(20) ->
    enqueue(16).

This fixup extends ONLY the F-198 TX branch (l2_dst && l2_src && eth_type) and
ONLY for IPv4 (eth_type == 0x0800); IPv6 (deferred to a later release) keeps the
S1 two-opcode chain, and UPDATE_HOPLIMIT(0x29) would be its analogue. The
tx_fqid==0 fallback and the shared enqueue-param writer are untouched (they key
off enqueue_off, which this fixup recomputes).

TX IPv4 record layout (key14, 320B): opcode list @24 = [0x21,0x41,0x01];
DSCP param @40 (4B, zero); L2 param @44 (20B); enqueue @64 (16B);
param_end/ctx ptr @80. All within 320 (IPv6 key37 TX unchanged from S1).

Count-gated, idempotent marker F-200; hard-fail on drift.
"""

import sys

SRC = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

with open(SRC) as f:
    src = f.read()

if "F-200" in src:
    print("### F-200 already applied")
    sys.exit(0)

old = (
    "\t\tif (l2_dst && l2_src && eth_type) {\n"
    "\t\t\t/* Vendor create_ethernet_hm(): INSERT_L2_HDR parameter is\n"
    "\t\t\t * first because opcode 0x41 is first. hdrlen=14; the 4-byte\n"
    "\t\t\t * struct header + 14-byte L2 payload needs two pad bytes, so\n"
    "\t\t\t * word = 14 | (2 << 29) = 0x4000000e. Param consumes 20B. */\n"
    "\t\t\tr[opc_off + 0] = FMAN_EHASH_OPC_INSERT_L2_HDR;\n"
    "\t\t\tr[opc_off + 1] = FMAN_EHASH_OPC_ENQUEUE_PKT;\n"
    "\t\t\t*(__be32 *)(r + param_off + 0) = cpu_to_be32(0x4000000e);\n"
    "\t\t\tmemcpy(r + param_off + 4, l2_dst, 6);\n"
    "\t\t\tmemcpy(r + param_off + 10, l2_src, 6);\n"
    "\t\t\t*(__be16 *)(r + param_off + 16) = cpu_to_be16(eth_type);\n"
    "\t\t\tenqueue_off = param_off + 20;\t/* ALIGN(4 + 14, 4) */\n"
    "\t\t} else {\n"
)

new = (
    "\t\tif (l2_dst && l2_src && eth_type) {\n"
    "\t\t\t/* F-200 (T-M7-2 S3): for routed IPv4, prepend UPDATE_TTL\n"
    "\t\t\t * (0x21) so the FE decrements TTL and fixes the IPv4 header\n"
    "\t\t\t * checksum in hardware.  Vendor create_ttl_hm() always emits\n"
    "\t\t\t * a 4-byte en_ehash_update_dscp param (zero = no DSCP mark)\n"
    "\t\t\t * for UPDATE_TTL, which must precede the INSERT_L2_HDR param\n"
    "\t\t\t * in emission order.  IPv6 (eth_type 0x86dd) is deferred: it\n"
    "\t\t\t * needs UPDATE_HOPLIMIT(0x29), so keep the S1 two-opcode\n"
    "\t\t\t * chain there for now.\n"
    "\t\t\t *\n"
    "\t\t\t * Vendor create_ethernet_hm(): INSERT_L2_HDR parameter\n"
    "\t\t\t * follows; hdrlen=14; the 4-byte struct header + 14-byte L2\n"
    "\t\t\t * payload needs two pad bytes, so word = 14 | (2 << 29) =\n"
    "\t\t\t * 0x4000000e. L2 param consumes 20B. */\n"
    "\t\t\t#define FMAN_EHASH_OPC_UPDATE_TTL\t0x21\n"
    "\t\t\tsize_t l2poff = param_off;\n"
    "\t\t\tsize_t oi = 0;\n"
    "\n"
    "\t\t\tif (eth_type == 0x0800) {\n"
    "\t\t\t\t/* UPDATE_TTL opcode + 4B zero DSCP param first. */\n"
    "\t\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_UPDATE_TTL;\n"
    "\t\t\t\t*(__be32 *)(r + param_off + 0) = cpu_to_be32(0);\n"
    "\t\t\t\tl2poff = param_off + 4;\n"
    "\t\t\t}\n"
    "\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_INSERT_L2_HDR;\n"
    "\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_ENQUEUE_PKT;\n"
    "\t\t\t*(__be32 *)(r + l2poff + 0) = cpu_to_be32(0x4000000e);\n"
    "\t\t\tmemcpy(r + l2poff + 4, l2_dst, 6);\n"
    "\t\t\tmemcpy(r + l2poff + 10, l2_src, 6);\n"
    "\t\t\t*(__be16 *)(r + l2poff + 16) = cpu_to_be16(eth_type);\n"
    "\t\t\tenqueue_off = l2poff + 20;\t/* after L2 (ALIGN(4+14,4)) */\n"
    "\t\t} else {\n"
)

if old not in src:
    print("### F-200: FATAL: F-198 TX branch anchor not found -- F-198 not "
          "applied or drifted. Refusing to guess.")
    sys.exit(1)

if src.count(old) != 1:
    print(f"### F-200: FATAL: expected 1 anchor match, got {src.count(old)}")
    sys.exit(1)

src = src.replace(old, new, 1)
with open(SRC, "w") as f:
    f.write(src)
print("### fman_pcd.c: F-200 UPDATE_TTL for routed IPv4 applied (1 block)")

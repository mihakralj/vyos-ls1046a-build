"""F-216: harden rx_default_dqrr against malformed zero-address FDs and remove
the obsolete F-072/F-170 frame-data hash diagnostic from the shipping RX path.

BOARD PANIC (image 2228, 2026-08-19)
------------------------------------
During dual-port v6 arm, eth3 logged an error-FQ FD status 0x00020020
(CLS_DISCARD|PRS_HDR_ERR), followed by a separate default-FQ panic:
  fault VA ffffffff80000108
  x26 = ffffffff80000000 = phys_to_virt(0)
  x2  = 0x108 = hash_offset
  pc  = rx_default_dqrr+0x7fc
This arithmetic proves a good-status descriptor with qm_fd_addr(fd)==0 reached
RXHASH extraction and dereferenced vaddr+hash_offset. The logged 0x00020020 FD
is a different error-FQ descriptor: the default path rejects RX_ERRORS before
vaddr, so it cannot be the panicking FD.

The exact likely fault site is the obsolete F-072/F-170 diagnostic 64-bit load:
  fman_pcd_kg_hash = be64_to_cpu(*(__be64 *)(vaddr + hash_offset));
It has no addr/bounds guard and executes on every eth3/eth4 good-status FD.
The adjacent mainline be32 RX-hash load is also unsafe when addr==0.

WHAT THIS DOES
--------------
1. Immediately after the existing FD-status error guard, before dma_unmap_page,
   phys_to_virt, prefetch, timestamp, or hash reads, reject addr==0. Emit a
   netdev_warn_ratelimited with interface, status, format, BPID, FD offset,
   length and FQID; increment rx_errors; consume WITHOUT dpaa_fd_release (there
   is no valid buffer address to release). This converts the panic into an
   observable malformed-FD event and validates the hardware root cause.
2. Remove the F-072 eth4-only diagnostic capture block (F-170 is deleted from
   the fixup stack). Normalize the RXHASH block to the original safe mainline
   form: be32 hash read + hash_valid=true. No shipping be64 diagnostic read,
   no fragile brace/mis-nesting.

This is DEFENSE + LOGGING, not the root hardware fix. The source of the zero FD
is still the live dual-port v6 arm and must be diagnosed after this prevents the
kernel panic. No behavior change for valid FDs. Count-gated/idempotent markers.
Must run AFTER F-072 (removes its emitted block); F-170 must NOT run. Wire at
F-170's former position in ci-setup-kernel.sh.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
if not os.path.exists(path):
    print("### F-216: dpaa_eth.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

changes = 0

# 1. Zero-address FD guard after existing RX-error handling, before DMA unmap.
guard_marker = "F-216(zero-fd-guard)"
if guard_marker in src:
    print("### F-216: zero-FD guard already present")
else:
    guard_anchor = (
        "\t\tpercpu_stats->rx_errors++;\n"
        "\t\tdpaa_fd_release(net_dev, fd);\n"
        "\t\treturn qman_cb_dqrr_consume;\n"
        "\t}\n"
        "\n"
        "\tdma_unmap_page(dpaa_bp->priv->rx_dma_dev, addr, DPAA_BP_RAW_SIZE,\n"
    )
    if guard_anchor not in src:
        print("### F-216: FATAL: RX-error/dma_unmap anchor not found")
        sys.exit(1)
    guard_new = (
        "\t\tpercpu_stats->rx_errors++;\n"
        "\t\tdpaa_fd_release(net_dev, fd);\n"
        "\t\treturn qman_cb_dqrr_consume;\n"
        "\t}\n"
        "\n"
        "\t/* F-216(zero-fd-guard): dual-port v6 arm can transiently deliver a\n"
        "\t * good-status default-FQ descriptor with addr==0. phys_to_virt(0)\n"
        "\t * plus hash_offset (0x108) panicked at ffffffff80000108. Reject and\n"
        "\t * log before ANY DMA-unmap/headroom access; no valid buffer exists\n"
        "\t * to release. */\n"
        "\tif (unlikely(!addr)) {\n"
        "\t\tif (net_ratelimit())\n"
        "\t\t\tnetdev_warn(net_dev,\n"
        "\t\t\t\t\"zero-address RX FD: status=0x%08x format=%u bpid=%u off=%u len=%u fqid=%u\\n\",\n"
        "\t\t\t\tfd_status, (unsigned int)fd_format, dq->fd.bpid,\n"
        "\t\t\t\tqm_fd_get_offset(fd), qm_fd_get_length(fd), fq->fqid);\n"
        "\t\tpercpu_stats->rx_errors++;\n"
        "\t\treturn qman_cb_dqrr_consume;\n"
        "\t}\n"
        "\n"
        "\tdma_unmap_page(dpaa_bp->priv->rx_dma_dev, addr, DPAA_BP_RAW_SIZE,\n"
    )
    src = src.replace(guard_anchor, guard_new, 1)
    changes += 1
    print("### F-216: zero-address FD guard + diagnostics added")

# 2. Remove F-072 (or F-170 if replayed on an old generated tree) capture and
# normalize the whole RXHASH block to mainline be32+hash_valid form.
normal_marker = "F-216(rxhash-normalized)"
if normal_marker in src:
    print("### F-216: RXHASH block already normalized")
else:
    # Locate from the stable mainline comment through the next contig-format if.
    start_marker = "\t/* Extract the hash stored in the headroom before running XDP */\n"
    end_marker = "\n\tif (likely(fd_format == qm_fd_contig)) {\n"
    start = src.find(start_marker)
    end = src.find(end_marker, start)
    if start < 0 or end < 0:
        print("### F-216: FATAL: RXHASH block boundaries not found")
        sys.exit(1)
    old_block = src[start:end]
    if "fman_pcd_kg_hash" not in old_block:
        print("### F-216: FATAL: expected F-072/F-170 capture not present in RXHASH block")
        sys.exit(1)
    clean_block = (
        "\t/* Extract the hash stored in the headroom before running XDP.\n"
        "\t * F-216(rxhash-normalized): the obsolete F-072/F-170 be64 frame-data\n"
        "\t * diagnostic was removed after it amplified a zero-address FD into a\n"
        "\t * kernel panic. Valid descriptors retain the original mainline be32\n"
        "\t * RX-hash extraction only. */\n"
        "\tif (net_dev->features & NETIF_F_RXHASH && priv->keygen_in_use &&\n"
        "\t    !fman_port_get_hash_result_offset(priv->mac_dev->port[RX],\n"
        "\t\t\t\t\t      &hash_offset)) {\n"
        "\t\thash = be32_to_cpu(*(__be32 *)(vaddr + hash_offset));\n"
        "\t\thash_valid = true;\n"
        "\t}\n"
    )
    src = src[:start] + clean_block + src[end:]
    changes += 1
    print("### F-216: F-072/F-170 capture removed; RXHASH block normalized")

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### F-216 complete ({changes} change(s))")
else:
    print("### F-216 no changes (already present)")
    sys.exit(0)

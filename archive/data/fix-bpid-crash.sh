#!/bin/bash
# Apply bm_free_bpid_range() fix to bman.c and bman_priv.h
set -e

BMAN_C="/opt/vyos-dev/linux/drivers/soc/fsl/qbman/bman.c"
BMAN_H="/opt/vyos-dev/linux/drivers/soc/fsl/qbman/bman_priv.h"
USDPAA_C="/opt/vyos-dev/linux/drivers/soc/fsl/fsl_usdpaa_mainline.c"

# 1. Add bm_free_bpid_range() after EXPORT_SYMBOL(bm_release_bpid) in bman.c
if ! grep -q "bm_free_bpid_range" "$BMAN_C"; then
    sed -i '/^EXPORT_SYMBOL(bm_release_bpid);$/a\
\
/*\
 * bm_free_bpid_range - Free BPID IDs without hardware pool drain\
 *\
 * Unlike bm_release_bpid() which calls bm_shutdown_pool() to drain\
 * the hardware buffer pool, this function ONLY returns the IDs to\
 * the genalloc allocator. Used by USDPAA driver where userspace\
 * DPDK manages pool draining before releasing BPIDs.\
 */\
void bm_free_bpid_range(u32 bpid, u32 count)\
{\
	gen_pool_free(bm_bpalloc, bpid | DPAA_GENALLOC_OFF, count);\
}\
EXPORT_SYMBOL(bm_free_bpid_range);' "$BMAN_C"
    echo "bman.c: added bm_free_bpid_range()"
else
    echo "bman.c: bm_free_bpid_range() already present"
fi

# 2. Add declaration to bman_priv.h
if ! grep -q "bm_free_bpid_range" "$BMAN_H"; then
    sed -i '/^int bm_release_bpid(u32 bpid);$/a\
void bm_free_bpid_range(u32 bpid, u32 count);' "$BMAN_H"
    echo "bman_priv.h: added bm_free_bpid_range() declaration"
else
    echo "bman_priv.h: declaration already present"
fi

# 3. Verify
echo "=== Verification ==="
grep -n "bm_free_bpid_range" "$BMAN_C" "$BMAN_H" "$USDPAA_C" 2>/dev/null || true
echo "DONE"

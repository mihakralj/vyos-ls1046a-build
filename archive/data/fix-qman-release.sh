#!/bin/bash
# fix-qman-release.sh — Add allocator-only free functions to qman.c
# These avoid hardware portal access during USDPAA cleanup

QMAN="/opt/vyos-dev/linux/drivers/soc/fsl/qbman/qman.c"
QMAN_PRIV="/opt/vyos-dev/linux/drivers/soc/fsl/qbman/qman_priv.h"
USDPAA="/opt/vyos-dev/linux/drivers/soc/fsl/qbman/fsl_usdpaa_mainline.c"

# 1. Add qman_free_*_range functions to qman.c (before final #endif or at end)
cat >> "$QMAN" << 'EOF'

/*
 * Allocator-only free functions for USDPAA driver.
 * These ONLY return IDs to the genalloc pool WITHOUT hardware cleanup.
 * Used when DPDK userspace manages hardware state - calling the hardware
 * cleanup variants (qman_shutdown_fq, qpool_cleanup, cgr_cleanup) crashes
 * because portals may be reserved for userspace.
 */
void qman_free_fqid_range(u32 fqid, u32 count)
{
	gen_pool_free(qm_fqalloc, fqid | DPAA_GENALLOC_OFF, count);
}
EXPORT_SYMBOL(qman_free_fqid_range);

void qman_free_pool_range(u32 qp, u32 count)
{
	gen_pool_free(qm_qpalloc, qp | DPAA_GENALLOC_OFF, count);
}
EXPORT_SYMBOL(qman_free_pool_range);

void qman_free_cgrid_range(u32 cgrid, u32 count)
{
	gen_pool_free(qm_cgralloc, cgrid | DPAA_GENALLOC_OFF, count);
}
EXPORT_SYMBOL(qman_free_cgrid_range);
EOF
echo "Added qman_free_*_range to qman.c"

# 2. Add declarations to qman_priv.h (before the closing #endif or at appropriate location)
# First check if already declared
if ! grep -q 'qman_free_fqid_range' "$QMAN_PRIV"; then
    # Find a good insertion point - after qman_alloc declarations
    sed -i '/^int qman_alloc_cgrid_range/a \
\
/* Allocator-only free - no hardware cleanup (for USDPAA) */\
void qman_free_fqid_range(u32 fqid, u32 count);\
void qman_free_pool_range(u32 qp, u32 count);\
void qman_free_cgrid_range(u32 cgrid, u32 count);' "$QMAN_PRIV"
    echo "Added declarations to qman_priv.h"
else
    echo "Declarations already in qman_priv.h"
fi

# 3. Fix release_id_range in fsl_usdpaa_mainline.c to use new functions
# Replace qman_release_fqid loop with qman_free_fqid_range
sed -i '/case usdpaa_id_fqid:/{
n
s/for (i = 0; i < count; i++)/qman_free_fqid_range(base, count);/
n
s/qman_release_fqid(base + i);//
}' "$USDPAA"

# Replace qman_release_pool loop with qman_free_pool_range  
sed -i '/case usdpaa_id_qpool:/{
n
s/for (i = 0; i < count; i++)/qman_free_pool_range(base, count);/
n
s/qman_release_pool(base + i);//
}' "$USDPAA"

# Replace qman_release_cgrid loop with qman_free_cgrid_range
sed -i '/case usdpaa_id_cgrid:/{
n
s/for (i = 0; i < count; i++)/qman_free_cgrid_range(base, count);/
n
s/qman_release_cgrid(base + i);//
}' "$USDPAA"

echo "Fixed release_id_range in fsl_usdpaa_mainline.c"

# 4. Verify
echo ""
echo "=== Verification ==="
echo "qman.c new functions:"
grep -n 'qman_free_.*_range' "$QMAN" | tail -10
echo ""
echo "qman_priv.h declarations:"
grep -n 'qman_free_.*_range' "$QMAN_PRIV"
echo ""
echo "fsl_usdpaa_mainline.c release_id_range:"
grep -n -A2 'case usdpaa_id_fqid:\|case usdpaa_id_bpid:\|case usdpaa_id_qpool:\|case usdpaa_id_cgrid:' "$USDPAA" | grep -v '^--$' | head -20

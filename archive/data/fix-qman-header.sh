#!/bin/bash
# fix-qman-header.sh - Add qman_free_*_range declarations to qman.h
HEADER="/opt/vyos-dev/linux/include/soc/fsl/qman.h"

if grep -q 'qman_free_fqid_range' "$HEADER"; then
    echo "Already declared in qman.h"
    exit 0
fi

# Add after the last qman_alloc_cgrid define
sed -i '/#define qman_alloc_cgrid(result)/a \\n/* Allocator-only free - no hardware cleanup (for USDPAA driver) */\nvoid qman_free_fqid_range(u32 fqid, u32 count);\nvoid qman_free_pool_range(u32 qp, u32 count);\nvoid qman_free_cgrid_range(u32 cgrid, u32 count);' "$HEADER"

echo "Declarations added:"
grep -n 'qman_free_' "$HEADER"

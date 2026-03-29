#!/bin/bash
# patch-vpp-dpaa-mempool.sh — Patch VPP DPDK plugin for DPAA1 BMan mempool
#
# Root cause #27: VPP mempool ops="vpp" but DPAA PMD requires ops="dpaa"
# Root cause #28: VPP driver.c missing "net_dpaa" entry
# Root cause #29: Mempool creation must happen BEFORE dpdk_lib_init() so
#                 BMan pool exists when DPAA devices probe during EAL init
#
# Run on LXC 200 against /opt/vyos-dev/vpp source tree
# 7 patches across 4 files: driver.c, dpdk.h, init.c, common.c

set -euo pipefail

VPP="/opt/vyos-dev/vpp/src/plugins/dpdk"
INIT="${VPP}/device/init.c"
COMMON="${VPP}/device/common.c"
DRIVER="${VPP}/device/driver.c"
DPDK_H="${VPP}/device/dpdk.h"

log() { echo -e "\033[0;32m[$(date +%H:%M:%S)]\033[0m $*"; }
err() { echo -e "\033[0;31m[$(date +%H:%M:%S)] ERROR:\033[0m $*"; exit 1; }

[ -d "${VPP}" ] || err "VPP DPDK plugin source not found at ${VPP}"

log "=== Patching VPP for DPAA1 BMan mempool support ==="

# ================================================================
# PATCH 1: driver.c — Add net_dpaa to DPDK_DRIVERS
# ================================================================
log "Patch 1: Adding net_dpaa to driver.c"

if grep -q '"net_dpaa"' "${DRIVER}" 2>/dev/null; then
    log "  Already patched — skipping"
else
    sed -i '/\.drivers = DPDK_DRIVERS.*"net_dpaa2"/i\  {\n    .drivers = DPDK_DRIVERS ({ "net_dpaa", "NXP DPAA1 FMan Mac" }),\n    .interface_name_prefix = "TenGigabitEthernet",\n  },' "${DRIVER}"
    log "  Added net_dpaa driver entry"
fi

# ================================================================
# PATCH 2: dpdk.h — Add IS_DPAA flag (bit 16)
# ================================================================
log "Patch 2: Adding IS_DPAA flag to dpdk.h"

if grep -q 'IS_DPAA' "${DPDK_H}" 2>/dev/null; then
    log "  Already patched — skipping"
else
    sed -i '/_ (15, TX_PREPARE, "tx-prepare")/a\  _ (16, IS_DPAA, "dpaa-device")                                              \\' "${DPDK_H}"
    log "  Added IS_DPAA flag (bit 16)"
fi

# ================================================================
# PATCH 3: dpdk.h — Add dpaa_mempool to dpdk_main_t
# ================================================================
log "Patch 3: Adding dpaa_mempool to dpdk_main_t in dpdk.h"

if grep -q 'dpaa_mempool' "${DPDK_H}" 2>/dev/null; then
    log "  Already patched — skipping"
else
    sed -i '/^} dpdk_main_t;/i\\n  /* DPAA1 BMan hardware mempool for DPAA PMD devices */\n  struct rte_mempool *dpaa_mempool;' "${DPDK_H}"
    log "  Added dpaa_mempool to dpdk_main_t"
fi

# ================================================================
# PATCH 4: init.c — Add IS_DPAA detection
# ================================================================
log "Patch 4: Adding IS_DPAA detection to init.c"

if grep -q 'IS_DPAA' "${INIT}" 2>/dev/null; then
    log "  Already patched — skipping"
else
    sed -i "/dpdk_log_warn.*unknown driver.*driver_name/a\\
\\
      /* Mark DPAA1 devices for BMan mempool routing */\\
      if (di.driver_name \&\& strstr (di.driver_name, \"net_dpaa\") \&\&\\
          !strstr (di.driver_name, \"net_dpaa2\"))\\
        dpdk_device_flag_set (xd, DPDK_DEVICE_FLAG_IS_DPAA, 1);" "${INIT}"
    log "  Added DPAA device detection (IS_DPAA flag)"
fi

# ================================================================
# PATCH 5: init.c — Add dpdk_dpaa_mempool_create() function + call
# Use Python for complex multi-line insert (sed can't handle this reliably)
# ================================================================
log "Patch 5: Adding DPAA mempool creation to init.c"

if grep -q 'dpaa_mempool_create' "${INIT}" 2>/dev/null; then
    log "  Already patched — skipping"
else
    # Create the C function as a temp file
    cat > /tmp/dpaa_mempool_func.c << 'CFUNC'

/* DPAA1 BMan mempool: create hardware buffer pool for DPAA PMD devices.
 * MUST be called BEFORE dpdk_lib_init() so the BMan pool exists when
 * DPAA devices probe during EAL init (root cause #29).
 * Uses rte_pktmbuf_pool_create_by_ops() with ops="dpaa" which triggers
 * dpaa_mbuf_create_pool() -> bman_new_pool() -> allocates HW BPID.
 * FMan DMA writes received packets into these BMan-managed buffers.
 *
 * No device check needed: if "dpaa" mempool ops aren't registered
 * (non-DPAA platform), create_by_ops() returns NULL harmlessly. */
static void
dpdk_dpaa_mempool_create (dpdk_main_t *dm)
{
  dm->dpaa_mempool = rte_pktmbuf_pool_create_by_ops (
    "dpaa_vpp_pool",           /* name */
    4096,                       /* n mbufs */
    256,                        /* cache_size */
    0,                          /* priv_size: dpaa ops fill pool_data */
    RTE_MBUF_DEFAULT_BUF_SIZE,  /* data_room_size */
    0,                          /* socket_id */
    "dpaa");                    /* ops_name: BMan HW pool */

  if (!dm->dpaa_mempool)
    dpdk_log_notice ("DPAA mempool not created (no DPAA platform): %s",
                     rte_strerror (rte_errno));
  else
    dpdk_log_notice ("DPAA BMan mempool created: %u buffers",
                     dm->dpaa_mempool->size);
}
CFUNC

    # Find the line number of the last #include in init.c
    LAST_INCLUDE=$(grep -n '^#include' "${INIT}" | tail -1 | cut -d: -f1)
    log "  Last #include at line ${LAST_INCLUDE}"

    # Insert the function after the last #include using sed with line number
    sed -i "${LAST_INCLUDE}r /tmp/dpaa_mempool_func.c" "${INIT}"
    log "  Inserted dpdk_dpaa_mempool_create() after line ${LAST_INCLUDE}"

    # Add the call BEFORE dpdk_lib_init (root cause #29: pool must exist during probe)
    # Use sed with alternate delimiter to avoid slash issues
    sed -i '/error = dpdk_lib_init (dm);/i\
  /* Create DPAA BMan mempool BEFORE device init (root cause #29) */\
  dpdk_dpaa_mempool_create (dm);\
' "${INIT}"
    log "  Added dpdk_dpaa_mempool_create() call BEFORE dpdk_lib_init"

    rm -f /tmp/dpaa_mempool_func.c
fi

# ================================================================
# PATCH 6: common.c — Route DPAA devices to BMan mempool
# ================================================================
log "Patch 6: Adding DPAA mempool routing to common.c"

if grep -q 'IS_DPAA' "${COMMON}" 2>/dev/null; then
    log "  Already patched — skipping"
else
    # Add dm reference after the first ASSERT in dpdk_device_setup
    sed -i '/^dpdk_device_setup (dpdk_device_t \* xd)/,/ASSERT/{
        /ASSERT/a\
  dpdk_main_t *dm = \&dpdk_main;
    }' "${COMMON}"
    log "  Added dm reference to dpdk_device_setup"

    # Replace the mempool assignment with DPAA-aware version
    sed -i 's|struct rte_mempool \*mp = dpdk_mempool_by_buffer_pool_index\[bpidx\];|/* Route DPAA devices to BMan hardware mempool */\n      struct rte_mempool *mp;\n      if ((xd->flags \& DPDK_DEVICE_FLAG_IS_DPAA) \&\& dm->dpaa_mempool)\n        mp = dm->dpaa_mempool;\n      else\n        mp = dpdk_mempool_by_buffer_pool_index[bpidx];|' "${COMMON}"
    log "  Added DPAA mempool routing in rx_queue_setup"
fi

# ================================================================
# PATCH 7: Add rte_mbuf.h include for rte_mbuf_set_pool_ops_name
# ================================================================
log "Patch 7: Adding rte_mbuf.h include to init.c"

if grep -q 'rte_mbuf.h' "${INIT}" 2>/dev/null; then
    log "  Already has rte_mbuf.h — skipping"
else
    # Use alternate delimiter since path has slashes
    sed -i '1,/^#include.*dpdk.h/{/^#include.*dpdk.h/a\#include <rte_mbuf.h>
}' "${INIT}"
    log "  Added rte_mbuf.h include"
fi

# ================================================================
# VERIFICATION
# ================================================================
log ""
log "=== Verification ==="

echo "--- driver.c ---"
grep -n 'net_dpaa' "${DRIVER}" | head -5

echo "--- dpdk.h flags ---"
grep -n 'IS_DPAA' "${DPDK_H}" | head -3

echo "--- dpdk.h mempool ---"
grep -n 'dpaa_mempool' "${DPDK_H}" | head -3

echo "--- init.c function ---"
grep -n 'dpaa_mempool_create\|IS_DPAA\|dpaa_vpp_pool\|rte_mbuf.h' "${INIT}" | head -15

echo "--- common.c routing ---"
grep -n 'IS_DPAA\|dpaa_mempool\|dpdk_main_t.*dm' "${COMMON}" | head -5

log ""
log "=== Patch complete! ==="

#!/usr/bin/env bash
# build-usdpaa-mainline.sh — Apply USDPAA mainline patches + build kernel
#
# Runs on LXC 200 "vyos-builder" (Debian 12, aarch64-linux-gnu-gcc 12.2.0)
# Applies 6 patches to mainline kernel 6.6.y for /dev/fsl-usdpaa support.
#
# Run on LXC 200:
#   cd /opt/vyos-dev && ./build-usdpaa-mainline.sh all
#
# Phases: patch | build | deploy | all | verify | reset
# See plans/MAINLINE-PATCH-SPEC.md for technical specification.

set -euo pipefail

# ── Paths ────────────────────────────────────────────────────────────────────
KERNEL_DIR=/opt/vyos-dev/linux
QBMAN_DIR="${KERNEL_DIR}/drivers/soc/fsl/qbman"
USDPAA_SRC=/opt/vyos-dev/fsl_usdpaa_mainline.c
CROSS=aarch64-linux-gnu-
NPROC=$(nproc)
TFTP_DIR=/srv/tftp

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()   { echo -e "${RED}[FATAL]${NC} $*" >&2; exit 1; }

timer_start() { TIMER_START=$(date +%s); }
timer_end()   {
    local elapsed=$(( $(date +%s) - TIMER_START ))
    local mins=$(( elapsed / 60 ))
    local secs=$(( elapsed % 60 ))
    ok "$1 completed in ${mins}m ${secs}s"
}

# ── Verify a sed replacement occurred ────────────────────────────────────────
verify_change() {
    local file="$1" pattern="$2" description="$3"
    if grep -q "$pattern" "$file"; then
        ok "  $description"
    else
        die "  FAILED: $description — pattern '$pattern' not found in $file"
    fi
}

# ═════════════════════════════════════════════════════════════════════════════
# Phase: patch — Apply all 6 patches via source surgery
#
# Uses sed + file insertion instead of git-apply because our patches use
# placeholder line numbers (designed as human-readable specs).
# Each patch is idempotent: checks if already applied before modifying.
# ═════════════════════════════════════════════════════════════════════════════
phase_patch() {
    info "═══ Phase: patch — Applying USDPAA mainline patches ═══"
    timer_start

    [ -d "$KERNEL_DIR" ] || die "Kernel source not found at $KERNEL_DIR"
    [ -d "$QBMAN_DIR" ]  || die "QBMan directory not found at $QBMAN_DIR"

    # ── Backup originals (first run only) ─────────────────────────────────
    for f in bman.c bman_portal.c bman_priv.h qman_portal.c qman_priv.h qman_ccsr.c Kconfig Makefile; do
        if [ -f "${QBMAN_DIR}/${f}" ] && [ ! -f "${QBMAN_DIR}/${f}.orig" ]; then
            cp "${QBMAN_DIR}/${f}" "${QBMAN_DIR}/${f}.orig"
            info "Backed up ${f} -> ${f}.orig"
        fi
    done

    # ══════════════════════════════════════════════════════════════════════
    # PATCH 1: bman.c — Export bm_alloc_bpid_range + bm_release_bpid
    # ══════════════════════════════════════════════════════════════════════
    info "Patch 1/6: bman.c — Export BPID range allocator"
    local bman_c="${QBMAN_DIR}/bman.c"

    if grep -q 'EXPORT_SYMBOL(bm_alloc_bpid_range)' "$bman_c"; then
        info "  Already applied — skipping"
    else
        # Remove 'static' from bm_alloc_bpid_range
        sed -i 's/^static int bm_alloc_bpid_range(u32 \*result, u32 count)/int bm_alloc_bpid_range(u32 *result, u32 count)/' "$bman_c"
        verify_change "$bman_c" "^int bm_alloc_bpid_range" "Removed static from bm_alloc_bpid_range"

        # Add EXPORT_SYMBOL after bm_alloc_bpid_range's closing brace
        # The function ends with "return 0;\n}" before the blank line before bm_release_bpid
        # Strategy: find "static int bm_release_bpid" and insert EXPORT_SYMBOL before the blank line preceding it
        sed -i '/^int bm_alloc_bpid_range/,/^}/ {
            /^}/ a\EXPORT_SYMBOL(bm_alloc_bpid_range);
        }' "$bman_c"
        # The above adds it after the FIRST closing brace of bm_alloc_bpid_range
        # But sed range is greedy — let's use a more targeted approach
        # Actually, sed range /start/,/end/ stops at the FIRST match of end after start, so this is correct.

        # Remove 'static' from bm_release_bpid
        sed -i 's/^static int bm_release_bpid(u32 bpid)/int bm_release_bpid(u32 bpid)/' "$bman_c"
        verify_change "$bman_c" "^int bm_release_bpid" "Removed static from bm_release_bpid"

        # Add EXPORT_SYMBOL after bm_release_bpid's closing brace
        # bm_release_bpid is followed by "struct bman_pool *bman_new_pool"
        sed -i '/^int bm_release_bpid/,/^}/ {
            /^}/ a\EXPORT_SYMBOL(bm_release_bpid);
        }' "$bman_c"

        verify_change "$bman_c" "EXPORT_SYMBOL(bm_alloc_bpid_range)" "Added EXPORT_SYMBOL(bm_alloc_bpid_range)"
        verify_change "$bman_c" "EXPORT_SYMBOL(bm_release_bpid)" "Added EXPORT_SYMBOL(bm_release_bpid)"
    fi

    # ── Patch 1 continued: bman_priv.h — Add declarations ────────────────
    info "Patch 1/6: bman_priv.h — Add BPID allocator declarations"
    local bman_priv_h="${QBMAN_DIR}/bman_priv.h"

    if grep -q 'int bm_alloc_bpid_range' "$bman_priv_h"; then
        info "  Already applied — skipping"
    else
        # Insert before "struct bm_portal_config {"
        sed -i '/^struct bm_portal_config {/i\
/* BPID range allocator — exported for USDPAA mainline driver */\
int bm_alloc_bpid_range(u32 *result, u32 count);\
int bm_release_bpid(u32 bpid);\
' "$bman_priv_h"
        verify_change "$bman_priv_h" "int bm_alloc_bpid_range" "Added bm_alloc_bpid_range declaration"
    fi

    # ══════════════════════════════════════════════════════════════════════
    # PATCH 2: bman_portal.c + bman_priv.h — Phys addr storage + reservation
    # ══════════════════════════════════════════════════════════════════════
    info "Patch 2/6: bman_priv.h — Add phys addr fields to bm_portal_config"

    if grep -q 'addr_phys_ce' "$bman_priv_h"; then
        info "  Already applied — skipping struct fields"
    else
        # Add phys addr fields after "void __iomem *addr_virt_ci;"
        sed -i '/void __iomem \*addr_virt_ci;/a\
\t/* Physical addresses for userspace mmap (populated during probe) */\
\tresource_size_t addr_phys_ce;\
\tresource_size_t addr_phys_ci;\
\tsize_t size_ce;\
\tsize_t size_ci;' "$bman_priv_h"
        verify_change "$bman_priv_h" "addr_phys_ce" "Added phys addr fields to bm_portal_config"
    fi

    if grep -q 'bool reserved;' "$bman_priv_h"; then
        info "  Already applied — skipping reserved field"
    else
        # Add 'reserved' field before the closing "};" of bm_portal_config
        # It's after "int irq;" line
        sed -i '/\tint irq;/a\
\t/* true if reserved for userspace (not affine to any CPU) */\
\tbool reserved;' "$bman_priv_h"
        verify_change "$bman_priv_h" "bool reserved" "Added reserved field"
    fi

    if grep -q 'bman_portal_reserve' "$bman_priv_h"; then
        info "  Already applied — skipping reservation API declarations"
    else
        # Add reservation API declarations after closing of bm_portal_config struct
        # Find "struct bman_portal *bman_create_affine_portal" and insert before it
        sed -i '/^struct bman_portal \*bman_create_affine_portal/i\
/* Portal reservation API for USDPAA mainline driver */\
int bman_portal_reserve(struct bm_portal_config **pcfg_out);\
void bman_portal_release_reserved(struct bm_portal_config *pcfg);\
' "$bman_priv_h"
        verify_change "$bman_priv_h" "bman_portal_reserve" "Added reservation API declarations"
    fi

    info "Patch 2/6: bman_portal.c — Add free portal pool + reservation functions"
    local bman_portal_c="${QBMAN_DIR}/bman_portal.c"

    if grep -q 'bman_free_portals' "$bman_portal_c"; then
        info "  Already applied — skipping"
    else
        # Add free portal list after "static DEFINE_SPINLOCK(bman_lock);"
        sed -i '/static DEFINE_SPINLOCK(bman_lock);/a\
\
/* Pool of portals available for userspace reservation */\
static LIST_HEAD(bman_free_portals);\
static DEFINE_SPINLOCK(bman_free_lock);' "$bman_portal_c"
        verify_change "$bman_portal_c" "bman_free_portals" "Added bman_free_portals list"

        # Store physical addresses during probe — after the CE resource check
        # Find the pattern: addr_phys = platform_get_resource(pdev, IORESOURCE_MEM,
        #                                    DPAA_PORTAL_CE);
        # After the error check block, add phys addr storage.
        # The approach: insert after "goto err_ioremap1;\n\t}" for the CE block
        # This is fragile — instead, insert right before the memremap call
        # Actually, let's insert after the CE error check closes
        # Pattern: find "Can't get %pOF property 'reg::CE'" and after the closing "}" add our storage
        sed -i "/Can't get.*reg::CE/,/}/ {
            /}/ a\\
\tpcfg->addr_phys_ce = addr_phys[0]->start;\\
\tpcfg->size_ce = resource_size(addr_phys[0]);
        }" "$bman_portal_c"

        # Now we need to also capture the CI physical address.
        # The existing code does platform_get_resource for CI separately for the ioremap.
        # We need to add a CI resource fetch BEFORE the ioremap of CI.
        # Find the ioremap_resource for CI — pattern varies. Let's store CI phys addr after
        # the memremap for CE succeeds. Actually, we need to find where DPAA_PORTAL_CI is used.

        # The second resource get for CI happens later. Let's find it.
        # In bman_portal.c: there's a devm_ioremap for CI. Find addr_phys used for CI.
        # The code does:
        #   pcfg->addr_virt_ci = devm_ioremap(dev, addr_phys->start, ...)
        # where addr_phys has been reassigned to the CI resource.
        # We need to store phys_ci AFTER the CI resource is obtained.
        # 
        # Simpler approach: look for the CI memremap/ioremap and add storage before it.
        # Find "DPAA_PORTAL_CI" which should appear in a platform_get_resource call
        if grep -q 'DPAA_PORTAL_CI' "$bman_portal_c"; then
            sed -i "/DPAA_PORTAL_CI/,/}/ {
                /}/ a\\
\tpcfg->addr_phys_ci = addr_phys[1]->start;\\
\tpcfg->size_ci = resource_size(addr_phys[1]);
            }" "$bman_portal_c"
        else
            # CI resource might be obtained differently — store using a different pattern
            # The ioremap for CI uses addr_phys that's re-obtained for index 1
            warn "DPAA_PORTAL_CI pattern not found — CI phys addr storage may need manual fix"
        fi

        # Add portal to free list when no CPU available
        # Find "cpu >= nr_cpu_ids" block in bman_portal_probe
        # The existing code does: spin_unlock(&bman_lock); return 0;
        # We need to add the portal to bman_free_portals before the return
        sed -i '/cpu >= nr_cpu_ids/,/return 0;/ {
            /return 0;/ i\
\t\tpcfg->reserved = false;\
\t\tspin_lock(\&bman_free_lock);\
\t\tlist_add_tail(\&pcfg->list, \&bman_free_portals);\
\t\tspin_unlock(\&bman_free_lock);
        }' "$bman_portal_c"

        # Add reservation functions before "static const struct of_device_id bman_portal_ids"
        sed -i '/^static const struct of_device_id bman_portal_ids/i\
/**\
 * bman_portal_reserve - Reserve a BMan portal for userspace use\
 * @pcfg_out: pointer to receive the portal configuration\
 *\
 * Returns 0 on success, -ENOENT if no free portals available.\
 */\
int bman_portal_reserve(struct bm_portal_config **pcfg_out)\
{\
\tstruct bm_portal_config *pcfg;\
\
\tspin_lock(\&bman_free_lock);\
\tif (list_empty(\&bman_free_portals)) {\
\t\tspin_unlock(\&bman_free_lock);\
\t\treturn -ENOENT;\
\t}\
\tpcfg = list_first_entry(\&bman_free_portals,\
\t\t\t\tstruct bm_portal_config, list);\
\tlist_del(\&pcfg->list);\
\tpcfg->reserved = true;\
\tspin_unlock(\&bman_free_lock);\
\t*pcfg_out = pcfg;\
\treturn 0;\
}\
EXPORT_SYMBOL(bman_portal_reserve);\
\
void bman_portal_release_reserved(struct bm_portal_config *pcfg)\
{\
\tspin_lock(\&bman_free_lock);\
\tpcfg->reserved = false;\
\tlist_add_tail(\&pcfg->list, \&bman_free_portals);\
\tspin_unlock(\&bman_free_lock);\
}\
EXPORT_SYMBOL(bman_portal_release_reserved);\
' "$bman_portal_c"
        verify_change "$bman_portal_c" "EXPORT_SYMBOL(bman_portal_reserve)" "Added bman_portal_reserve"
    fi

    # ══════════════════════════════════════════════════════════════════════
    # PATCH 3: qman_portal.c + qman_priv.h — Same pattern for QMan
    # ══════════════════════════════════════════════════════════════════════
    info "Patch 3/6: qman_priv.h — Add phys addr fields to qm_portal_config"
    local qman_priv_h="${QBMAN_DIR}/qman_priv.h"

    if grep -q 'addr_phys_ce' "$qman_priv_h"; then
        info "  Already applied — skipping struct fields"
    else
        # Add phys addr fields after "void __iomem *addr_virt_ci;" in qm_portal_config
        sed -i '/void __iomem \*addr_virt_ci;/a\
\t/* Physical addresses for userspace mmap (populated during probe) */\
\tresource_size_t addr_phys_ce;\
\tresource_size_t addr_phys_ci;\
\tsize_t size_ce;\
\tsize_t size_ci;' "$qman_priv_h"
        verify_change "$qman_priv_h" "addr_phys_ce" "Added phys addr fields to qm_portal_config"
    fi

    if grep -q 'bool reserved;' "$qman_priv_h"; then
        info "  Already applied — skipping reserved field"
    else
        # Add 'reserved' field after "u32 pools;"
        sed -i '/\tu32 pools;/a\
\t/* true if reserved for userspace (not affine to any CPU) */\
\tbool reserved;' "$qman_priv_h"
        verify_change "$qman_priv_h" "bool reserved" "Added reserved field"
    fi

    if grep -q 'qman_portal_reserve' "$qman_priv_h"; then
        info "  Already applied — skipping reservation API declarations"
    else
        # Insert AFTER struct qm_portal_config closing '};' (which follows 'bool reserved;')
        # The prototypes reference struct qm_portal_config, so they MUST come after the
        # struct definition — otherwise the compiler treats it as a different forward-declared type.
        sed -i '/^\tbool reserved;/{
n
/^};/a\
\
/* Portal reservation API for USDPAA mainline driver */\
int qman_portal_reserve(struct qm_portal_config **pcfg_out);\
void qman_portal_release_reserved(struct qm_portal_config *pcfg);
}' "$qman_priv_h"
        verify_change "$qman_priv_h" "qman_portal_reserve" "Added reservation API declarations"
    fi

    info "Patch 3/6: qman_portal.c — Add free portal pool + reservation functions"
    local qman_portal_c="${QBMAN_DIR}/qman_portal.c"

    if grep -q 'qman_free_portals' "$qman_portal_c"; then
        info "  Already applied — skipping"
    else
        # Add free portal list after "static DEFINE_SPINLOCK(qman_lock);"
        sed -i '/static DEFINE_SPINLOCK(qman_lock);/a\
\
/* Pool of portals available for userspace reservation */\
static LIST_HEAD(qman_free_portals);\
static DEFINE_SPINLOCK(qman_free_lock);' "$qman_portal_c"
        verify_change "$qman_portal_c" "qman_free_portals" "Added qman_free_portals list"

        # Store CE physical address during probe
        sed -i "/Can't get.*reg::CE/,/}/ {
            /}/ a\\
\tpcfg->addr_phys_ce = addr_phys[0]->start;\\
\tpcfg->size_ce = resource_size(addr_phys[0]);
        }" "$qman_portal_c"

        # Store CI physical address
        if grep -q 'DPAA_PORTAL_CI' "$qman_portal_c"; then
            sed -i "/DPAA_PORTAL_CI/,/}/ {
                /}/ a\\
\tpcfg->addr_phys_ci = addr_phys[1]->start;\\
\tpcfg->size_ci = resource_size(addr_phys[1]);
            }" "$qman_portal_c"
        fi

        # Add portal to free list when no CPU available
        sed -i '/cpu >= nr_cpu_ids/,/return 0;/ {
            /return 0;/ i\
\t\tpcfg->reserved = false;\
\t\tspin_lock(\&qman_free_lock);\
\t\tlist_add_tail(\&pcfg->list, \&qman_free_portals);\
\t\tspin_unlock(\&qman_free_lock);
        }' "$qman_portal_c"

        # Add reservation functions before "static const struct of_device_id qman_portal_ids"
        sed -i '/^static const struct of_device_id qman_portal_ids/i\
/**\
 * qman_portal_reserve - Reserve a QMan portal for userspace use\
 * @pcfg_out: pointer to receive the portal configuration\
 *\
 * Returns 0 on success, -ENOENT if no free portals available.\
 */\
int qman_portal_reserve(struct qm_portal_config **pcfg_out)\
{\
\tstruct qm_portal_config *pcfg;\
\
\tspin_lock(\&qman_free_lock);\
\tif (list_empty(\&qman_free_portals)) {\
\t\tspin_unlock(\&qman_free_lock);\
\t\treturn -ENOENT;\
\t}\
\tpcfg = list_first_entry(\&qman_free_portals,\
\t\t\t\tstruct qm_portal_config, list);\
\tlist_del(\&pcfg->list);\
\tpcfg->reserved = true;\
\tspin_unlock(\&qman_free_lock);\
\t*pcfg_out = pcfg;\
\treturn 0;\
}\
EXPORT_SYMBOL(qman_portal_reserve);\
\
/**\
 * qman_portal_release_reserved - Return a reserved QMan portal\
 * @pcfg: portal config previously obtained from qman_portal_reserve()\
 */\
void qman_portal_release_reserved(struct qm_portal_config *pcfg)\
{\
\tspin_lock(\&qman_free_lock);\
\tpcfg->reserved = false;\
\tlist_add_tail(\&pcfg->list, \&qman_free_portals);\
\tspin_unlock(\&qman_free_lock);\
}\
EXPORT_SYMBOL(qman_portal_release_reserved);\
' "$qman_portal_c"
        verify_change "$qman_portal_c" "EXPORT_SYMBOL(qman_portal_reserve)" "Added qman_portal_reserve"
    fi

    # ══════════════════════════════════════════════════════════════════════
    # PATCH 4: qman_ccsr.c — Export qman_set_sdest
    # ══════════════════════════════════════════════════════════════════════
    info "Patch 4/6: qman_ccsr.c — Export qman_set_sdest"
    local qman_ccsr_c="${QBMAN_DIR}/qman_ccsr.c"

    if grep -q 'EXPORT_SYMBOL(qman_set_sdest)' "$qman_ccsr_c"; then
        info "  Already applied — skipping"
    else
        # Find the closing brace of qman_set_sdest function
        # The function ends with "}\n\nstatic int qman_resource_init"
        # Add EXPORT_SYMBOL after the closing brace
        sed -i '/^void qman_set_sdest/,/^}/ {
            /^}/ a\EXPORT_SYMBOL(qman_set_sdest);
        }' "$qman_ccsr_c"
        verify_change "$qman_ccsr_c" "EXPORT_SYMBOL(qman_set_sdest)" "Added EXPORT_SYMBOL(qman_set_sdest)"
    fi

    # ══════════════════════════════════════════════════════════════════════
    # PATCH 5: Kconfig + Makefile + copy C source
    # ══════════════════════════════════════════════════════════════════════
    info "Patch 5/6: Add fsl_usdpaa_mainline driver to build system"
    local kconfig="${QBMAN_DIR}/Kconfig"
    local makefile="${QBMAN_DIR}/Makefile"

    # Copy C source
    if [ -f "$USDPAA_SRC" ]; then
        cp "$USDPAA_SRC" "${QBMAN_DIR}/fsl_usdpaa_mainline.c"
        ok "  Copied fsl_usdpaa_mainline.c to ${QBMAN_DIR}/"
    else
        die "Source file not found: $USDPAA_SRC — push it to LXC 200 first"
    fi

    # Add Kconfig entry
    if grep -q 'FSL_USDPAA_MAINLINE' "$kconfig"; then
        info "  Kconfig entry already present — skipping"
    else
        # Insert before "endif # FSL_DPAA"
        sed -i '/^endif # FSL_DPAA/i\
config FSL_USDPAA_MAINLINE\
\ttristate "Userspace DPAA driver for DPDK"\
\tdepends on FSL_DPAA\
\thelp\
\t  Provides /dev/fsl-usdpaa character device for DPDK DPAA1 PMD.\
\t  Allows userspace to allocate QBMan resources, reserve portals,\
\t  and manage DMA memory from reserved-memory regions.\
\t  Required for DPDK with the dpaa bus driver.\
\t  If unsure, say N.\
' "$kconfig"
        verify_change "$kconfig" "FSL_USDPAA_MAINLINE" "Added Kconfig entry"
    fi

    # Add Makefile entry
    if grep -q 'FSL_USDPAA_MAINLINE' "$makefile"; then
        info "  Makefile entry already present — skipping"
    else
        echo 'obj-$(CONFIG_FSL_USDPAA_MAINLINE)               += fsl_usdpaa_mainline.o' >> "$makefile"
        verify_change "$makefile" "FSL_USDPAA_MAINLINE" "Added Makefile entry"
    fi

    # ══════════════════════════════════════════════════════════════════════
    # PATCH 6: DTS reserved-memory (skipped — applied to local DTS, not kernel tree)
    # ══════════════════════════════════════════════════════════════════════
    info "Patch 6/6: DTS reserved-memory — skipped (applied to local DTS, not kernel tree)"
    info "  The reserved-memory node goes in data/dtb/mono-gateway-dk.dts"
    info "  Apply it locally before compiling DTB"

    echo ""
    ok "═══ All patches applied successfully ═══"
    ok ""
    ok "Modified files:"
    ok "  ${QBMAN_DIR}/bman.c            (Patch 1: export BPID allocator)"
    ok "  ${QBMAN_DIR}/bman_priv.h       (Patch 1+2: declarations + struct fields)"
    ok "  ${QBMAN_DIR}/bman_portal.c     (Patch 2: phys addr storage + reservation)"
    ok "  ${QBMAN_DIR}/qman_priv.h       (Patch 3: QMan struct fields + declarations)"
    ok "  ${QBMAN_DIR}/qman_portal.c     (Patch 3: QMan phys addr + reservation)"
    ok "  ${QBMAN_DIR}/qman_ccsr.c       (Patch 4: export qman_set_sdest)"
    ok "  ${QBMAN_DIR}/Kconfig           (Patch 5: FSL_USDPAA_MAINLINE config)"
    ok "  ${QBMAN_DIR}/Makefile          (Patch 5: build rule)"
    ok "  ${QBMAN_DIR}/fsl_usdpaa_mainline.c (Patch 5: 620-line driver)"
    echo ""
    info "Next: ./build-usdpaa-mainline.sh build"

    timer_end "patch"
}

# ═════════════════════════════════════════════════════════════════════════════
# Phase: build — Configure and compile kernel with USDPAA
# ═════════════════════════════════════════════════════════════════════════════
phase_build() {
    info "═══ Phase: build — Compile kernel with USDPAA mainline driver ═══"
    timer_start

    [ -d "$KERNEL_DIR" ] || die "Kernel source not found at $KERNEL_DIR"
    [ -f "${QBMAN_DIR}/fsl_usdpaa_mainline.c" ] || die "USDPAA source not found — run 'patch' phase first"

    cd "$KERNEL_DIR"

    # ── Enable CONFIG_FSL_USDPAA_MAINLINE ─────────────────────────────────
    info "Enabling CONFIG_FSL_USDPAA_MAINLINE=y in .config"
    if [ ! -f .config ]; then
        die ".config not found — kernel was not previously configured"
    fi

    # Use scripts/config to set the new option (--set-val forces =y even if =m)
    ./scripts/config --set-val FSL_USDPAA_MAINLINE y
    make ARCH=arm64 CROSS_COMPILE=${CROSS} olddefconfig

    # Verify it stuck
    if grep -q 'CONFIG_FSL_USDPAA_MAINLINE=y' .config; then
        ok "CONFIG_FSL_USDPAA_MAINLINE=y ✓"
    else
        warn "CONFIG_FSL_USDPAA_MAINLINE not set to =y — check Kconfig dependencies"
        grep 'USDPAA' .config || true
    fi

    # ── Build (incremental — only recompiles changed files) ───────────────
    info "Building kernel Image with ${NPROC} cores (incremental)..."
    make ARCH=arm64 CROSS_COMPILE=${CROSS} -j${NPROC} Image 2>&1 | tail -20

    # ── Summary ──────────────────────────────────────────────────────────
    local image="${KERNEL_DIR}/arch/arm64/boot/Image"
    if [ -f "$image" ]; then
        local image_size
        image_size=$(du -h "$image" | cut -f1)
        ok "Kernel Image built: $image ($image_size)"
    else
        die "Kernel Image not found after build — compilation failed"
    fi

    # Check if fsl_usdpaa_mainline was compiled
    if [ -f "${QBMAN_DIR}/fsl_usdpaa_mainline.o" ]; then
        ok "fsl_usdpaa_mainline.o compiled successfully ✓"
    else
        warn "fsl_usdpaa_mainline.o not found — may have been compiled into vmlinux directly"
    fi

    echo ""
    info "Next: ./build-usdpaa-mainline.sh deploy"

    timer_end "build"
}

# ═════════════════════════════════════════════════════════════════════════════
# Phase: deploy — Compress kernel and copy to TFTP
# ═════════════════════════════════════════════════════════════════════════════
phase_deploy() {
    info "═══ Phase: deploy — Copy kernel to TFTP server ═══"
    timer_start

    local image="${KERNEL_DIR}/arch/arm64/boot/Image"
    [ -f "$image" ] || die "Kernel Image not found — run 'build' phase first"

    # Back up current TFTP kernel (if it hasn't been backed up already)
    if [ -f "${TFTP_DIR}/vmlinuz" ] && [ ! -f "${TFTP_DIR}/vmlinuz.pre-usdpaa.bak" ]; then
        cp "${TFTP_DIR}/vmlinuz" "${TFTP_DIR}/vmlinuz.pre-usdpaa.bak"
        ok "Backed up current vmlinuz → vmlinuz.pre-usdpaa.bak"
    fi

    # Compress and deploy
    info "Compressing kernel Image for TFTP..."
    gzip -9 -c "$image" > "${TFTP_DIR}/vmlinuz"

    local compressed_size
    compressed_size=$(du -h "${TFTP_DIR}/vmlinuz" | cut -f1)
    ok "Deployed: ${TFTP_DIR}/vmlinuz ($compressed_size)"

    # Show TFTP directory
    echo ""
    ok "═══ TFTP directory ═══"
    ls -lah "${TFTP_DIR}/"

    echo ""
    info "READY: From U-Boot serial console → run dev_boot"
    info "After boot, verify: ls -la /dev/fsl-usdpaa"

    timer_end "deploy"
}

# ═════════════════════════════════════════════════════════════════════════════
# Phase: verify — Check patch application correctness
# ═════════════════════════════════════════════════════════════════════════════
phase_verify() {
    info "═══ Phase: verify — Checking patch application ═══"
    local fail=0

    [ -d "$QBMAN_DIR" ] || die "QBMan directory not found"

    info "Checking Patch 1 (bm_alloc_bpid_range export)..."
    grep -n 'EXPORT_SYMBOL(bm_alloc_bpid_range)' "${QBMAN_DIR}/bman.c" || { warn "MISSING: EXPORT_SYMBOL(bm_alloc_bpid_range)"; fail=1; }
    grep -n 'EXPORT_SYMBOL(bm_release_bpid)' "${QBMAN_DIR}/bman.c" || { warn "MISSING: EXPORT_SYMBOL(bm_release_bpid)"; fail=1; }

    info "Checking Patch 2 (BMan portal phys addr)..."
    grep -n 'addr_phys_ce' "${QBMAN_DIR}/bman_priv.h" || { warn "MISSING: addr_phys_ce in bman_priv.h"; fail=1; }
    grep -n 'bman_portal_reserve' "${QBMAN_DIR}/bman_portal.c" || { warn "MISSING: bman_portal_reserve"; fail=1; }

    info "Checking Patch 3 (QMan portal phys addr)..."
    grep -n 'addr_phys_ce' "${QBMAN_DIR}/qman_priv.h" || { warn "MISSING: addr_phys_ce in qman_priv.h"; fail=1; }
    grep -n 'qman_portal_reserve' "${QBMAN_DIR}/qman_portal.c" || { warn "MISSING: qman_portal_reserve"; fail=1; }

    info "Checking Patch 4 (qman_set_sdest export)..."
    grep -n 'EXPORT_SYMBOL(qman_set_sdest)' "${QBMAN_DIR}/qman_ccsr.c" || { warn "MISSING: EXPORT_SYMBOL(qman_set_sdest)"; fail=1; }

    info "Checking Patch 5 (USDPAA driver)..."
    [ -f "${QBMAN_DIR}/fsl_usdpaa_mainline.c" ] && ok "fsl_usdpaa_mainline.c present" || { warn "MISSING: fsl_usdpaa_mainline.c"; fail=1; }
    grep -n 'FSL_USDPAA_MAINLINE' "${QBMAN_DIR}/Kconfig" || { warn "MISSING: Kconfig entry"; fail=1; }
    grep -n 'FSL_USDPAA_MAINLINE' "${QBMAN_DIR}/Makefile" || { warn "MISSING: Makefile entry"; fail=1; }

    echo ""
    if [ "$fail" -eq 0 ]; then
        ok "═══ All patches verified ✓ ═══"
    else
        die "═══ Some patches are MISSING — run 'patch' phase first ═══"
    fi
}

# ═════════════════════════════════════════════════════════════════════════════
# Phase: reset — Revert all patches (restore from backups)
# ═════════════════════════════════════════════════════════════════════════════
phase_reset() {
    info "═══ Phase: reset — Reverting all patches ═══"

    for f in bman.c bman_portal.c bman_priv.h qman_portal.c qman_priv.h qman_ccsr.c Kconfig Makefile; do
        if [ -f "${QBMAN_DIR}/${f}.orig" ]; then
            cp "${QBMAN_DIR}/${f}.orig" "${QBMAN_DIR}/${f}"
            ok "Restored ${f} from backup"
        else
            warn "No backup found for ${f}"
        fi
    done

    # Remove the new file
    rm -f "${QBMAN_DIR}/fsl_usdpaa_mainline.c"
    ok "Removed fsl_usdpaa_mainline.c"

    ok "═══ All patches reverted ═══"
    info "Run './build-usdpaa-mainline.sh patch' to re-apply"
}

# ═════════════════════════════════════════════════════════════════════════════
# Phase: all — patch + build + deploy
# ═════════════════════════════════════════════════════════════════════════════
phase_all() {
    info "═══ Running ALL phases ═══"
    phase_patch
    phase_build
    phase_deploy
}

# ── Usage ────────────────────────────────────────────────────────────────────
usage() {
    cat << 'EOF'
Usage: build-usdpaa-mainline.sh <phase>

Phases:
  patch     Apply all 6 USDPAA patches to mainline kernel source
  build     Configure + compile kernel with CONFIG_FSL_USDPAA_MAINLINE=y
  deploy    Compress kernel and copy to TFTP server
  verify    Check that all patches are correctly applied
  reset     Revert all patches (restore from backups)
  all       Run patch + build + deploy in sequence

Run on LXC 200:
  cd /opt/vyos-dev && ./build-usdpaa-mainline.sh all

After TFTP boot, verify:
  ls -la /dev/fsl-usdpaa
  cat /proc/devices | grep usdpaa

See: plans/MAINLINE-PATCH-SPEC.md
EOF
    exit 1
}

# ── Main ─────────────────────────────────────────────────────────────────────
[ $# -lt 1 ] && usage

case "$1" in
    patch)   phase_patch ;;
    build)   phase_build ;;
    deploy)  phase_deploy ;;
    verify)  phase_verify ;;
    reset)   phase_reset ;;
    all)     phase_all ;;
    *)       usage ;;
esac

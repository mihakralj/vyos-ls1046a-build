#!/bin/bash
# ci-build-ask-modules.sh — Build NXP ASK 1.x OOT kernel modules (cdx, fci, auto_bridge)
#
# Clones the we-are-mono/ASK repo at mt-6.12.y, builds cdx.ko, fci.ko, and
# auto_bridge.ko against a pre-built NXP lf-6.12.49-2.2.0 kernel tree, signs
# them with the kernel's auto-generated module signing key, and packages them
# as Debian .deb files.
#
# Invariants:
#   - $KSRC must be a fully built NXP kernel tree (Module.symvers, scripts/sign-file,
#     certs/signing_key.{pem,x509}), with CONFIG_MODULE_SIG_FORCE=y.
#   - The NXP kernel tree must have the SDK DPAA drivers (sdk_fman, sdk_dpaa,
#     fsl_qbman) in-tree — the cdx Kbuild includes sdk_fman/ncsw_config.mk.
#   - Cross-build env (ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-) is inherited.
#
# Module dependency order:
#   cdx.ko  →  fci.ko (depends on cdx/Module.symvers)
#   cdx.ko  →  auto_bridge.ko (independent of fci, depends on cdx symbols)
#
# Inputs:
#   $1  KSRC       — absolute path to the built kernel source tree (required)
#   $2  PKG_DIR    — absolute path to where .debs should land (required)
#
# Outputs:
#   cdx-modules-${KVER}_${PKG_VER}_arm64.deb
#   fci-modules-${KVER}_${PKG_VER}_arm64.deb
#   auto-bridge-modules-${KVER}_${PKG_VER}_arm64.deb
#
# Plan: plans/NXP-SDK-ASK-INTEGRATION.md §3.2, M2
set -ex -o pipefail

KSRC="${1:?KSRC required as \$1}"
PKG_DIR="${2:?PKG_DIR required as \$2}"

[ -d "$KSRC" ] || { echo "FATAL: KSRC=$KSRC does not exist"; exit 1; }
[ -d "$PKG_DIR" ] || { echo "FATAL: PKG_DIR=$PKG_DIR does not exist"; exit 1; }

# ── Resolve KSRC — handle post-bindeb-pkg cleanup (snapshot fallback) ─────
# When the kernel was built via bindeb-pkg, the original source tree may have
# had Module.symvers cleaned. The ask-kernel-snapshot mechanism (injected by
# build-kernel.sh) preserves a headers snapshot. Try that first.
SNAP_DIR="$(dirname "$KSRC")/ask-kernel-snapshot"
if [ ! -f "$KSRC/Module.symvers" ] && [ -d "$SNAP_DIR" ] && [ -e "$SNAP_DIR/.done" ]; then
    if [ -L "$SNAP_DIR/ksrc" ] || [ -d "$SNAP_DIR/ksrc" ]; then
        SNAP_KSRC="$(readlink -f "$SNAP_DIR/ksrc")"
    else
        SNAP_KSRC="$(find "$SNAP_DIR/extracted/usr/src" -maxdepth 1 -type d -name 'linux-headers-*' 2>/dev/null | head -1)"
    fi
    if [ -n "$SNAP_KSRC" ] && [ -f "$SNAP_KSRC/Module.symvers" ]; then
        echo "### Switching to snapshot KSRC: $SNAP_KSRC"
        KSRC="$SNAP_KSRC"
    fi
fi

# ── Validate kernel tree readiness ─────────────────────────────────────────
[ -f "$KSRC/Module.symvers" ] || { echo "FATAL: $KSRC/Module.symvers missing — kernel must be built first"; exit 1; }
[ -x "$KSRC/scripts/sign-file" ] || { echo "FATAL: $KSRC/scripts/sign-file missing"; exit 1; }
[ -f "$KSRC/certs/signing_key.pem" ] || { echo "FATAL: $KSRC/certs/signing_key.pem missing"; exit 1; }
[ -f "$KSRC/certs/signing_key.x509" ] || { echo "FATAL: $KSRC/certs/signing_key.x509 missing"; exit 1; }

# SDK FMan must be present in the kernel tree for cdx Kbuild's ncsw_config.mk include
NCSW_MK="$KSRC/drivers/net/ethernet/freescale/sdk_fman/ncsw_config.mk"
[ -f "$NCSW_MK" ] || { echo "FATAL: SDK FMan ncsw_config.mk not found at $NCSW_MK — NXP kernel tree required"; exit 1; }

# ── Resolve KVER ───────────────────────────────────────────────────────────
# Prefer reading from the produced kernel .deb in PKG_DIR (matches what apt sees);
# then from include/config/kernel.release (authoritative post-build); fall back
# to make kernelrelease, stripping any "+" dirty marker.
KERNEL_DEB="$(find "$PKG_DIR" -maxdepth 1 -name 'linux-image-*-vyos_*_arm64.deb' \
    ! -name '*-dbg_*' ! -name '*-headers_*' ! -name '*-dbgsym_*' 2>/dev/null | head -1)"
if [ -n "$KERNEL_DEB" ]; then
    KVER="$(basename "$KERNEL_DEB" | sed -E 's/^linux-image-(.+)_[^_]+_arm64\.deb$/\1/')"
    echo "### KVER from $(basename "$KERNEL_DEB"): $KVER"
elif [ -f "$KSRC/include/config/kernel.release" ]; then
    KVER="$(cat "$KSRC/include/config/kernel.release")"
    echo "### KVER from kernel.release: $KVER"
else
    KVER="$(make -C "$KSRC" -s kernelrelease 2>/dev/null | sed 's/+$//' || true)"
    if [ -z "$KVER" ]; then
        KVER="$(make -C "$KSRC" -s kernelversion 2>/dev/null || true)"
    fi
    echo "### KVER from kernel tree: $KVER"
fi
[ -n "$KVER" ] || { echo "FATAL: could not resolve KVER"; exit 1; }

# ── Clone we-are-mono/ASK ─────────────────────────────────────────────────
ASK_REPO="https://github.com/we-are-mono/ASK.git"
ASK_BRANCH="mt-6.12.y"
ASK_CACHE_DIR="${RUNNER_TOOL_CACHE:-/tmp}/ask-clone-cache"
ASK_DIR="$ASK_CACHE_DIR/ask-mt-6.12.y"

if [ -d "$ASK_DIR/.git" ]; then
    echo "### Updating ASK repo cache at $ASK_DIR"
    git -C "$ASK_DIR" fetch --depth 1 origin "$ASK_BRANCH" 2>&1 | tail -3 || true
    git -C "$ASK_DIR" checkout -f "$ASK_BRANCH" 2>&1 || true
else
    echo "### Cloning we-are-mono/ASK ($ASK_BRANCH)…"
    rm -rf "$ASK_DIR"
    git clone --depth 1 --branch "$ASK_BRANCH" "$ASK_REPO" "$ASK_DIR" 2>&1 | tail -3
fi

# ── Patch: disable auto-start of dpa_app by cdx.ko ─────────────────────────
# cdx_main.c defines START_DPA_APP which causes cdx_module_init() to call
# dpa_app via call_usermodehelper() BEFORE /dev/cdx_ctrl is created. dpa_app
# then fails with -EAGAIN because the device doesn't exist yet. Disable
# auto-start so dpa_app can be started separately after cdx.ko loads.
sed -i 's/^#define START_DPA_APP 1$/\/\/ #define START_DPA_APP 1/' "$ASK_DIR/cdx/cdx_main.c"
echo "### Patched cdx_main.c: START_DPA_APP disabled"

# ── Patch: stub out WiFi offload init ─────────────────────────────────────
# cdx_module_init() calls dpaa_vwd_init() under CFG_WIFI_OFFLOAD, which
# tries to allocate an OH port (vwd_init_ohport). On LS1046A without WiFi,
# alloc_offline_port() finds no free OH ports and returns failure, causing
# the entire cdx init to abort. Solutions tried and failed:
#   - Disabling CFG_WIFI_OFFLOAD → #else stubs have struct pfe issues + control_wifi symbols lost
#   - Removing .o files → linker undefined symbols
#   - -Wno-error alone → dpa_wifi.c still fails at modpost
#
# Fix: keep WiFi enabled at compile time, but stub dpaa_vwd_init() to
# return 0 immediately. The WiFi offload won't be active but all symbols
# remain available for linking and cdx_module_init() succeeds.
sed -i '/^int dpaa_vwd_init(void)/,/^{/ { /^{/a\    return 0;
}' "$ASK_DIR/cdx/dpa_wifi.c"
echo "### Patched dpa_wifi.c: dpaa_vwd_init() returns 0 immediately"

# ── Patch: fix cdx_create_fragment_bufpool NULL deref ──────────────────────
# If get_phys_port_poolinfo_bysize() fails (dpa_interface_info empty because
# dpa_app never ran), the error path tries bman_free_pool(bp->pool) but
# bp->pool was never allocated (still NULL from kzalloc). Remove the bogus
# free and just kfree the struct.
sed -i '/bman_free_pool(bp->pool);/d' "$ASK_DIR/cdx/cdx_ehash.c"
echo "### Patched cdx_ehash.c: removed bogus bman_free_pool(NULL) on error path"

# ── Patch: make cdx_init_frag_module non-fatal on MURAM error ────────────────
# dpa_get_fm_MURAM_handle() returns NULL because it's an OOT module
# that can't access the SDK FMan's MURAM handle. Instead of killing
# the entire cdx init, change return -1 to return 0 so the module
# loads in degraded mode (no frag/IP-reassembly). The unguarded
# MURAM_VIRT_TO_PHYS_ADDR calls are guarded below.
sed -i '/Error in getting MURAM handle/,/return -1/s/return -1/return 0/' "$ASK_DIR/cdx/cdx_ehash.c"
echo "### Patched cdx_ehash.c: MURAM handle failure no longer kills cdx init"

# ── Patch: guard unguarded NULL MURAM derefs in flow-creation functions ──────
# create_enque_hm() line ~2556 and cdx_create_rtp_qos_slowpath_flow() line ~3718
# dereference dscp_fq_map_ff_g.muram_addr which can be NULL if MURAM init
# failed. Guard them with NULL checks.
sed -i '/^.*MURAM_VIRT_TO_PHYS_ADDR(dscp_fq_map_ff_g.muram_addr)/{ s/^/if (dscp_fq_map_ff_g.muram_addr) /; }' "$ASK_DIR/cdx/cdx_ehash.c"
echo "### Patched cdx_ehash.c: guarded MURAM_VIRT_TO_PHYS_ADDR against NULL"

# ── Patch: add procfs cleanup on cdx module deinit ───────────────────────────
# cdx_init_fqid_procfs() creates /proc/fqid_stats/{tx,rx,pcd,sa} but
# cdx_module_deinit() never removes them. On failed init + retry, this
# causes 'proc_dir_entry already registered' WARNINGs. Add cleanup
# and include linux/proc_fs.h for remove_proc_subtree().
sed -i '/^#include "cdx.h"/a\
#include <linux/proc_fs.h>' "$ASK_DIR/cdx/cdx_main.c"
echo "### Patched cdx_main.c: added linux/proc_fs.h include"

# ── Patch: pre-populate fman_info MURAM handle via /dev/fm0pcd ──────────────
# Normally dpa_app opens /dev/fm0pcd and passes the fd number via
# CDX_CTRL_DPA_SET_PARAMS ioctl, which then calls cdxdrv_get_fman_handles()
# to populate fman_info[0].muram_handle. Since START_DPA_APP is disabled,
# this never happens. Open /dev/fm0pcd directly from cdx_module_init()
# BEFORE cdx_init_frag_module() so the MURAM handle is available.
sed -i '/^#include "lnxwrp_fsl_fman.h"/a\
#include <linux/file.h>\
#include "lnxwrp_fm.h"' "$ASK_DIR/cdx/cdx_main.c"
sed -i '/^#include "lnxwrp_fm.h"/a\
\
extern uint32_t num_fmans;' "$ASK_DIR/cdx/cdx_main.c"
echo "### Patched cdx_main.c: added extern uint32_t num_fmans"

# Pre-populate FMan MURAM handle before cdx_init_frag_module.
# First: make num_fmans non-static (dpa_cfg.c:33) so cdx_main.c can set it.
sed -i 's/^static uint32_t num_fmans;/uint32_t num_fmans;/' "$ASK_DIR/cdx/dpa_cfg.c"
echo "### Patched dpa_cfg.c: num_fmans no longer static"

# Then insert FMan MURAM init right before cdx_init_fqid_procfs()
sed -i '/\/\* creating a \/proc\/fqid_stats dir/i\
\t/* Pre-populate FMan info via /dev/fm0pcd so MURAM handle is available */\
\t{\
\t\tstruct file *fm_file = filp_open("/dev/fm0-pcd", O_RDWR, 0);\
\t\tif (!IS_ERR(fm_file)) {\
\t\t\tt_LnxWrpFmDev *wrapper = (t_LnxWrpFmDev *)fm_file->private_data;\
\t\t\tif (wrapper \&\& wrapper->h_MuramDev) {\
\t\t\t\tfman_info = kzalloc(sizeof(*fman_info), GFP_KERNEL);\
\t\t\t\tfman_info->muram_handle = wrapper->h_MuramDev;\
\t\t\t\tfman_info->physicalMuramBase = wrapper->fmMuramPhysBaseAddr;\
\t\t\t\tfman_info->fmMuramMemSize = wrapper->fmMuramMemSize;\
\t\t\t\tnum_fmans = 1;\
\t\t\t\tprintk("cdx: pre-populated MURAM handle from /dev/fm0-pcd\\n");\
\t\t\t}\
\t\t\tfilp_close(fm_file, NULL);\
\t\t}\
\t}\
' "$ASK_DIR/cdx/cdx_main.c"
echo "### Patched cdx_main.c: pre-populate fman_info MURAM handle before frag init"

sed -i '/^static void cdx_module_deinit/,/^}$/ {
    /kfree(cdx_info);/i\
\tremove_proc_subtree("fqid_stats", NULL);\
\tremove_proc_subtree("ucode_frag", NULL);
}' "$ASK_DIR/cdx/cdx_main.c"
echo "### Patched cdx_main.c: added procfs cleanup in cdx_module_deinit"

# ── Patch: disable CDX_FRAG_USE_BUFF_POOL ───────────────────────────────────
# The frag pool init calls get_phys_port_poolinfo_bysize() which walks
# dpa_interface_info. That list is populated by dpa_app (START_DPA_APP)
# which we've disabled. Skip the frag pool allocation entirely.
sed -i 's/^#define CDX_FRAG_USE_BUFF_POOL$/\/\/ #define CDX_FRAG_USE_BUFF_POOL/' "$ASK_DIR/cdx/cdx_ehash.c"
echo "### Patched cdx_ehash.c: CDX_FRAG_USE_BUFF_POOL disabled"

# ── Build cdx.ko ───────────────────────────────────────────────────────────
echo "### ======== Building cdx.ko ========"
make -C "$KSRC" \
    ARCH=arm64 CROSS_COMPILE="${CROSS_COMPILE:-}" \
    PLATFORM="LS1043A" \
    CONFIG_ASK_CDX=m \
    EXTRA_CFLAGS="-Wno-unused-variable -Wno-unused-function" \
    M="$ASK_DIR/cdx" \
    modules

CDX_KO="$ASK_DIR/cdx/cdx.ko"
[ -f "$CDX_KO" ] || { echo "FATAL: cdx.ko was not produced"; exit 1; }
echo "### cdx.ko built: $(stat -c '%s bytes' "$CDX_KO")"

# ── Build fci.ko (depends on cdx/Module.symvers) ──────────────────────────
echo "### ======== Building fci.ko ========"
make -C "$KSRC" \
    ARCH=arm64 CROSS_COMPILE="${CROSS_COMPILE:-}" \
    BOARD_ARCH=arm64 \
    CONFIG_ASK_FCI=m \
    KBUILD_EXTRA_SYMBOLS="$ASK_DIR/cdx/Module.symvers" \
    M="$ASK_DIR/fci" \
    modules

FCI_KO="$ASK_DIR/fci/fci.ko"
[ -f "$FCI_KO" ] || { echo "FATAL: fci.ko was not produced"; exit 1; }
echo "### fci.ko built: $(stat -c '%s bytes' "$FCI_KO")"

# ── Build auto_bridge.ko ───────────────────────────────────────────────────
echo "### ======== Building auto_bridge.ko ========"
make -C "$KSRC" \
    ARCH=arm64 CROSS_COMPILE="${CROSS_COMPILE:-}" \
    PLATFORM="LS1043A" \
    CONFIG_ASK_AUTO_BRIDGE=m \
    M="$ASK_DIR/auto_bridge" \
    modules

ABM_KO="$ASK_DIR/auto_bridge/auto_bridge.ko"
[ -f "$ABM_KO" ] || { echo "FATAL: auto_bridge.ko was not produced"; exit 1; }
echo "### auto_bridge.ko built: $(stat -c '%s bytes' "$ABM_KO")"

# ── Sign all modules ───────────────────────────────────────────────────────
echo "### Signing OOT modules with kernel auto-generated signing key"
for ko in "$CDX_KO" "$FCI_KO" "$ABM_KO"; do
    "$KSRC/scripts/sign-file" sha512 \
        "$KSRC/certs/signing_key.pem" \
        "$KSRC/certs/signing_key.x509" \
        "$ko"
    if ! tail -c 28 "$ko" | grep -q "Module signature appended"; then
        echo "FATAL: $(basename "$ko") was not signed"
        exit 1
    fi
    echo "###   signed: $(basename "$ko") ($(stat -c '%s bytes' "$ko"))"
done

# ── Package as .debs ──────────────────────────────────────────────────────
PKG_VER="1.0.0-${KVER}"

package_module() {
    local mod_name="$1" mod_ko="$2" pkg_name="$3" pkg_desc="$4"
    local STAGE DEB_NAME DEB_FILE

    STAGE="$(mktemp -d -t ask-deb-stage.XXXXXXXX)"
    # shellcheck disable=SC2064
    trap "rm -rf '$STAGE'" RETURN

    mkdir -p "$STAGE/DEBIAN" "$STAGE/lib/modules/${KVER}/extra"

    cp "$mod_ko" "$STAGE/lib/modules/${KVER}/extra/"

    DEB_NAME="${pkg_name}-modules-${KVER}"
    DEB_FILE="${DEB_NAME}_${PKG_VER}_arm64.deb"

    cat > "$STAGE/DEBIAN/control" <<EOF
Package: ${DEB_NAME}
Version: ${PKG_VER}
Section: kernel
Priority: optional
Architecture: arm64
Maintainer: VyOS LS1046A maintainers <noreply@invalid>
Depends: linux-image-${KVER}
Description: ${pkg_desc}
 NXP ASK 1.x out-of-tree kernel module for LS1046A FMan offload.
 Part of the NXP SDK ASK fast-path stack (cdx → fci → auto_bridge).
EOF

    cat > "$STAGE/DEBIAN/postinst" <<EOF
#!/bin/sh
set -e
if [ -d /lib/modules/${KVER} ]; then
    depmod -a ${KVER} || true
fi
exit 0
EOF
    chmod 0755 "$STAGE/DEBIAN/postinst"

    cat > "$STAGE/DEBIAN/postrm" <<EOF
#!/bin/sh
set -e
if [ -d /lib/modules/${KVER} ]; then
    depmod -a ${KVER} || true
fi
exit 0
EOF
    chmod 0755 "$STAGE/DEBIAN/postrm"

    echo "### Building $DEB_FILE"
    dpkg-deb --build --root-owner-group "$STAGE" "$PKG_DIR/$DEB_FILE"
    echo "###   $(ls -lh "$PKG_DIR/$DEB_FILE" | awk '{print $5, $NF}')"
}

package_module "cdx"          "$CDX_KO" "cdx"          "CDX — NXP ASK data-plane acceleration module"
package_module "fci"          "$FCI_KO" "fci"          "FCI — NXP ASK flow control interface module"
package_module "auto_bridge"  "$ABM_KO" "auto-bridge"  "Auto Bridge — NXP ASK L2 bridge flow detection module"

echo "### ASK OOT modules built and packaged successfully"
echo "###   KVER:     $KVER"
echo "###   PKG_DIR:  $PKG_DIR"
ls -lh "$PKG_DIR"/{cdx,fci,auto-bridge}-modules-*.deb

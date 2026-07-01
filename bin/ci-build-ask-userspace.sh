#!/bin/bash
# ci-build-ask-userspace.sh — Build NXP ASK 1.x userspace binaries (cmm, dpa_app, fmc)
#
# Clones/updates the NXP library repos (fmlib, fmc) at lf-6.12.49-2.2.0,
# downloads + patches libnfnetlink + libnetfilter_conntrack, builds libfci,
# then compiles cmm and dpa_app. Everything is statically linked against the
# patched libraries; only system shared libs (libcli, libpcap, libmnl, libxml2)
# are linked dynamically.
#
# Prerequisites (host packages):
#   libcli-dev libpcap-dev libmnl-dev libxml2-dev pkg-config g++ wget
#
# Invariants:
#   - The we-are-mono/ASK repo must already be cloned (by M2's ci-build-ask-modules.sh)
#     into $ASK_DIR (default: ${RUNNER_TOOL_CACHE:-/tmp}/ask-clone-cache/ask-mt-6.12.y).
#   - Cross-build env (ARCH, CROSS_COMPILE) is inherited. On native arm64,
#     CROSS_COMPILE is empty and --host is omitted from autotools configure.
#   - KSRC (kernel source tree) is required for fmlib header references.
#
# Inputs:
#   $1  KSRC       — absolute path to built kernel source tree (required)
#   $2  PKG_DIR    — absolute path to where .debs should land (required)
#
# Outputs:
#   ask-userspace-${PKG_VER}_arm64.deb
#
# Plan: plans/NXP-SDK-ASK-INTEGRATION.md §3.3, M3
set -ex -o pipefail

# REPO_ROOT is exported by common.sh when called from ci-build-packages.sh.
# Fallback for standalone execution.
export REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

KSRC="${1:?KSRC required as \$1}"
PKG_DIR="${2:?PKG_DIR required as \$2}"

[ -d "$KSRC" ] || { echo "FATAL: KSRC=$KSRC does not exist"; exit 1; }
[ -d "$PKG_DIR" ] || { echo "FATAL: PKG_DIR=$PKG_DIR does not exist"; exit 1; }

# ── Auto-detect native vs cross-compile ───────────────────────────────────
HOST_ARCH=$(uname -m)
if [[ "$HOST_ARCH" == "aarch64" || "$HOST_ARCH" == "arm64" ]]; then
    CROSS_COMPILE="${CROSS_COMPILE:-}"
    CBUILD=""
    CHOST=""
    echo "### Native arm64 build — CROSS_COMPILE='$CROSS_COMPILE'"
else
    CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
    CBUILD=""
    CHOST="aarch64-linux-gnu"
    echo "### Cross-compile build — CROSS_COMPILE='$CROSS_COMPILE' CHOST='$CHOST'"
fi
export CROSS_COMPILE
CC="${CROSS_COMPILE}gcc"
CXX="${CROSS_COMPILE}g++"
AR="${CROSS_COMPILE}ar"
STRIP="${CROSS_COMPILE}strip"
export CC CXX AR

# ── Paths ─────────────────────────────────────────────────────────────────
NXP_TAG="lf-6.12.49-2.2.0"
NXP_FMLIB_REPO="https://github.com/nxp-qoriq/fmlib.git"
NXP_FMC_REPO="https://github.com/nxp-qoriq/fmc.git"
LIBNFNETLINK_VER="1.0.2"
LIBNFNETLINK_URL="https://www.netfilter.org/projects/libnfnetlink/files/libnfnetlink-${LIBNFNETLINK_VER}.tar.bz2"
LIBNFCT_VER="1.1.0"
LIBNFCT_URL="https://www.netfilter.org/projects/libnetfilter_conntrack/files/libnetfilter_conntrack-${LIBNFCT_VER}.tar.xz"

SRC_CACHE="${RUNNER_TOOL_CACHE:-/tmp}/ask-sources-cache"
mkdir -p "$SRC_CACHE"
SYSROOT="$SRC_CACHE/sysroot"
mkdir -p "$SYSROOT" "$SYSROOT/lib" "$SYSROOT/include" "$SYSROOT/lib/pkgconfig"

ASK_CACHE="${RUNNER_TOOL_CACHE:-/tmp}/ask-clone-cache"
ASK_DIR="$ASK_CACHE/ask-mt-6.12.y"
[ -d "$ASK_DIR" ] || { echo "FATAL: ASK repo not found at $ASK_DIR — M2 must run first"; exit 1; }

FMLIB_DIR="$SRC_CACHE/fmlib"
FMC_DIR="$SRC_CACHE/fmc"
LIBFCI_DIR="$ASK_DIR/fci/lib"
ABM_DIR="$ASK_DIR/auto_bridge"

# ── Resolve KVER ───────────────────────────────────────────────────────────
# Prefer $KSRC/include/config/kernel.release (set by LOCALVERSION during build).
# Fall back to extracting KVER from the kernel image .deb in PKG_DIR (the most
# reliable source after bindeb-pkg may have cleaned the tree).
KVER=""
if [ -f "$KSRC/include/config/kernel.release" ]; then
    KVER="$(cat "$KSRC/include/config/kernel.release")"
    # Strip trailing '+' which indicates a dirty tree without LOCALVERSION
    KVER="${KVER%+}"
fi
if [ -z "$KVER" ] || [ "$KVER" = "6.12.49" ]; then
    # bindeb-pkg may have cleaned kernel.release; extract from .deb filename
    KVER="$(ls "$PKG_DIR"/linux-image-*_arm64.deb 2>/dev/null | head -1 | sed 's/.*linux-image-\(.*\)_arm64.deb/\1/; s/_.*//' || true)"
fi
if [ -z "$KVER" ]; then
    KVER="$(make -C "$KSRC" -s kernelrelease 2>/dev/null | sed 's/+$//' || true)"
    [ -z "$KVER" ] && KVER="$(make -C "$KSRC" -s kernelversion 2>/dev/null || true)"
fi
echo "### KVER: $KVER"

# ===========================================================================
#  fmlib — NXP Frame Manager userspace library
# ===========================================================================
echo "### ======== fmlib ========"
if [ ! -f "$FMLIB_DIR/.built" ]; then
    rm -rf "$FMLIB_DIR"
    git clone -q --depth 1 --branch "$NXP_TAG" "$NXP_FMLIB_REPO" "$FMLIB_DIR" 2>&1 | tail -3
    # Patch
    FMLIB_PATCH="$ASK_DIR/patches/fmlib/01-mono-ask-extensions.patch"
    if [ -f "$FMLIB_PATCH" ]; then
        (cd "$FMLIB_DIR" && git apply "$FMLIB_PATCH")
    fi
    # Build
    make -C "$FMLIB_DIR" \
        CROSS_COMPILE="$CROSS_COMPILE" \
        KERNEL_SRC="$KSRC" \
        libfm-arm.a
    ln -sf libfm-arm.a "$FMLIB_DIR/libfm.a"
    touch "$FMLIB_DIR/.built"
fi
echo "### fmlib ready"

# ===========================================================================
#  fmc — NXP FMan Configuration tool
# ===========================================================================
echo "### ======== fmc ========"
if [ ! -f "$FMC_DIR/.built" ] || ! grep -q 'return err.*name, err' "$FMC_DIR/source/fmc_exec.c" 2>/dev/null; then
    rm -rf "$FMC_DIR"
    git clone -q --depth 1 --branch "$NXP_TAG" "$NXP_FMC_REPO" "$FMC_DIR" 2>&1 | tail -3
    FMC_PATCH="$ASK_DIR/patches/fmc/01-mono-ask-extensions.patch"
    if [ -f "$FMC_PATCH" ]; then
        (cd "$FMC_DIR" && git apply "$FMC_PATCH")
    fi
    # Patch: skip E_ALREADY_EXISTS (EEXIST) in fmc_execute loop
    # Kernel RSS creates PCD schemes at IDs 0-N during fsl_dpa probe.
    # When dpa_app's fmc_execute tries to create CDX schemes at the same
    # IDs, FM_PCD_KgSchemeSet returns EEXIST. Instead of aborting the
    # entire fmc_execute, skip the conflicting scheme and continue.
    python3 << PYEOF
import sys
p = '$FMC_DIR/source/fmc_exec.c'
with open(p) as f: s = f.read()

# Fix 1: CHECK_ERR macro returns hardcoded 1 — change to return err
# so that callers can inspect the actual NXP error code.
# Match pattern: line with 'return 1;' immediately preceded by
# 'name, err );' (unique to CHECK_ERR, not CHECK_HANDLE).
old_checkerr = 'name, err );                                          \\\n        return 1;'
new_checkerr = 'name, err );                                          \\\n        return err;'
if old_checkerr in s:
    s = s.replace(old_checkerr, new_checkerr)
    print('Patched fmc_exec.c: CHECK_ERR returns err instead of 1')
else:
    print('WARNING: CHECK_ERR pattern not found')

# Fix 2: skip EEXIST in fmc_execute loop
old = '        /* Exit the loop in case of failure */\n        if ( ret != 0 ) {\n            break;\n        }'
new = "        /* Exit the loop in case of failure (skip kernel RSS collision) */\n        if ( ret != 0 ) {\n            if (GET_ERROR_TYPE(ret) == EEXIST) {\n                fmc_log_write(LOG_WARN, \"scheme exists (kernel RSS) - skipping\");\n                ret = 0;\n                continue;\n            }\n            break;\n        }"
if old in s:
    s = s.replace(old, new)
    print('Patched fmc_exec.c: skip EEXIST in fmc_execute')
else:
    print('WARNING: EEXIST-skip pattern not found in fmc_exec.c')
with open(p,'w') as f: f.write(s)
PYEOF
    # Build
    make -C "$FMC_DIR/source" \
        CC="$CC" CXX="$CXX" AR="$AR" \
        MACHINE=ls1046 \
        FMD_USPACE_HEADER_PATH="$FMLIB_DIR/include/fmd" \
        FMD_USPACE_LIB_PATH="$FMLIB_DIR" \
        LIBXML2_HEADER_PATH=/usr/include/libxml2 \
        TCLAP_HEADER_PATH=/usr/include
    touch "$FMC_DIR/.built"
fi
# Debian 12 libxml2 v2.9.14+ changed xmlStructuredErrorFunc signature
# from void(*)(void*, const xmlError*) to void(*)(void*, xmlError*).
# Fix the mismatch (idempotent — safe to run on cached clones too).
sed -i 's/const xmlError \*/xmlError */g' "$FMC_DIR/source/FMCGenericError.cpp" "$FMC_DIR/source/FMCGenericError.h"
echo "### fmc ready: $(ls -lh "$FMC_DIR/source/fmc" | awk '{print $5}')"

# ===========================================================================
#  libnfnetlink (patched) — static .a installed to sysroot
# ===========================================================================
echo "### ======== libnfnetlink $LIBNFNETLINK_VER (patched) ========"
LIBNFNETLINK_SRC="$SRC_CACHE/libnfnetlink-${LIBNFNETLINK_VER}"
if [ ! -f "$LIBNFNETLINK_SRC/.built" ]; then
    TARBALL="$SRC_CACHE/libnfnetlink-${LIBNFNETLINK_VER}.tar.bz2"
    if [ ! -f "$TARBALL" ]; then
        wget -q -P "$SRC_CACHE" "$LIBNFNETLINK_URL"
    fi
    rm -rf "$LIBNFNETLINK_SRC"
    tar xf "$TARBALL" -C "$SRC_CACHE"
    PATCH="$ASK_DIR/patches/libnfnetlink/${LIBNFNETLINK_VER}/01-nxp-ask-nonblocking-heap-buffer.patch"
    (
        cd "$LIBNFNETLINK_SRC" && \
        git init -q && git add -A && git commit -q --allow-empty -m "upstream" && \
        git apply "$PATCH" && \
        ./configure --host="${CHOST:-$CBUILD}" --prefix="$SYSROOT" --enable-static --disable-shared -q && \
        make -j"$(nproc)" -s && make install -s
    )
    touch "$LIBNFNETLINK_SRC/.built"
fi
echo "### libnfnetlink ready"

# ===========================================================================
#  libnetfilter_conntrack (patched) — static .a installed to sysroot
# ===========================================================================
echo "### ======== libnetfilter_conntrack $LIBNFCT_VER (patched) ========"
LIBNFCT_SRC="$SRC_CACHE/libnetfilter_conntrack-${LIBNFCT_VER}"
if [ ! -f "$LIBNFCT_SRC/.built" ]; then
    TARBALL="$SRC_CACHE/libnetfilter_conntrack-${LIBNFCT_VER}.tar.xz"
    if [ ! -f "$TARBALL" ]; then
        wget -q -P "$SRC_CACHE" "$LIBNFCT_URL"
    fi
    rm -rf "$LIBNFCT_SRC"
    tar xf "$TARBALL" -C "$SRC_CACHE"
    PATCH="$ASK_DIR/patches/libnetfilter-conntrack/${LIBNFCT_VER}/01-nxp-ask-comcerto-fp-extensions.patch"
    (
        cd "$LIBNFCT_SRC" && \
        git init -q && git add -A && git commit -q --allow-empty -m "upstream" && \
        git apply "$PATCH" && \
        PKG_CONFIG_PATH="$SYSROOT/lib/pkgconfig" \
        ./configure --host="${CHOST:-$CBUILD}" --prefix="$SYSROOT" --enable-static --disable-shared -q \
            CFLAGS="-I$SYSROOT/include" LDFLAGS="-L$SYSROOT/lib" && \
        make -j"$(nproc)" -s && make install -s
    )
    touch "$LIBNFCT_SRC/.built"
fi
echo "### libnetfilter_conntrack ready"

# ===========================================================================
#  libfci — ASK flow control interface library (static .a)
# ===========================================================================
echo "### ======== libfci ========"
if [ ! -f "$LIBFCI_DIR/.built" ]; then
    make -C "$LIBFCI_DIR" CC="$CC" AR="$AR"
    touch "$LIBFCI_DIR/.built"
fi
echo "### libfci ready"

# ===========================================================================
#  cmm — Connection Manager (userspace daemon)
# ===========================================================================
echo "### ======== cmm ========"
CMM_BUILT="$ASK_DIR/cmm/src/cmm"
CMM_SRC="$ASK_DIR/cmm/src/conntrack.c"

# Apply CT-TRACE diagnostics to the ASK clone's conntrack.c. The CI builds
# CMM from the cloned ASK repo, NOT from our local reference copy, so we
# inject these before compilation. Remove once conntrack dispatch is verified.
echo "### Injecting CT-TRACE diagnostics into conntrack.c"
python3 << PYPATCH
import re
p = '${CMM_SRC}'
with open(p) as f:
    s = f.read()

# Diagnostic 1: CT-TRACE at top of __cmmCtCatch() — proves callback fires
old1 = '''{
	u_int32_t id, ctFlags = 0;
	struct nf_conntrack *ctTemp = NULL;'''
new1 = '''{
	u_int32_t id, ctFlags = 0;
	struct nf_conntrack *ctTemp = NULL;

	/* Diagnostic (2026-07-01): unconditional DEBUG_CRIT to prove
	 * nfct_catch()/libnetfilter_conntrack dispatches kernel ctnetlink
	 * events into this callback. grep CT-TRACE in journal. Remove once
	 * vendored-libnfct dispatch is confirmed/refuted. */
	cmm_print(DEBUG_CRIT, "CT-TRACE: __cmmCtCatch type=%d(%s) enable=%d\\n",
		  type, conntrack_event_type(type), globalConf.enable);'''
if old1 in s:
    s = s.replace(old1, new1)
    print('Injected CT-TRACE __cmmCtCatch')
else:
    print('WARNING: __cmmCtCatch pattern not found')

# Diagnostic 2: CT-TRACE after nfct_catch() — proves event loop polls
old2 = '''			rc = nfct_catch(ctx->catch_handle);
			if (rc < 0)'''
new2 = '''			rc = nfct_catch(ctx->catch_handle);
			if (rc > 0)
				cmm_print(DEBUG_CRIT, "CT-TRACE: nfct_catch processed %d events\\n", rc);
			if (rc < 0)'''
if old2 in s:
    s = s.replace(old2, new2)
    print('Injected CT-TRACE nfct_catch')
else:
    print('WARNING: nfct_catch pattern not found')

# Diagnostic 3: CT-TRACE at fd_ct init — proves which fd the handle wraps
old3 = '	fd_ct = nfct_fd(ctx->catch_handle);'
new3 = '''	fd_ct = nfct_fd(ctx->catch_handle);
	cmm_print(DEBUG_CRIT, "CT-TRACE: fd_fci=%d fd_ct=%d\\n", fd_fci, fd_ct);'''
if old3 in s:
    s = s.replace(old3, new3)
    print('Injected CT-TRACE fd_ct')
else:
    print('WARNING: fd_ct pattern not found')

with open(p, 'w') as f:
    f.write(s)
PYPATCH
echo "### CT-TRACE diagnostics injected"

# libfci .a is in LIBFCI_DIR; patched libs .a in SYSROOT/lib
# PKG_CONFIG_PATH must be explicit — Makefile ?= default may not resolve SYSROOT
make -C "$ASK_DIR/cmm" CC="$CC" \
    LIBFCI_DIR="$LIBFCI_DIR" \
    ABM_DIR="$ABM_DIR" \
    SYSROOT="$SYSROOT" \
    PKG_CONFIG_PATH="$SYSROOT/lib/pkgconfig"
[ -f "$CMM_BUILT" ] || { echo "FATAL: cmm was not produced"; exit 1; }
echo "### cmm ready: $(ls -lh "$CMM_BUILT" | awk '{print $5}')"
file "$CMM_BUILT"

# Verify libnetfilter_conntrack is compiled in — required for conntrack-based
# hardware offload. Without it, CMM opens Eth 12 (NETLINK_NETFILTER) but
# never receives conntrack events → never pushes flows to CDX/FMan.
if ! strings "$CMM_BUILT" 2>/dev/null | grep -q "nfct_open\|nfct_callback_register"; then
    echo "FATAL: cmm built without libnetfilter_conntrack — PKG_CONFIG_PATH=$PKG_CONFIG_PATH"
    echo "  Check that libnetfilter_conntrack.pc exists in SYSROOT/lib/pkgconfig/"
    echo "  and that pkg-config can resolve libnfnetlink dependency."
    exit 1
fi
echo "### cmm: libnetfilter_conntrack symbols verified"

# ===========================================================================
#  dpa_app — real (with fmc, CDX PCD push)
# ===========================================================================
# FIXED 2026-06-26: The struct mismatch was caused by SEC_PROFILE_SUPPORT
# being defined in the kernel Kbuild (cdx/ccflags-y) but NOT in the
# dpa_app compilation. This caused:
#   - Kernel: INGRESS_ALL_POLICER_QUEUES=9, sizeof=424, index@400
#   - Userspace: INGRESS_ALL_POLICER_QUEUES=8, sizeof=360, index@336 (wrong)
# Adding -DSEC_PROFILE_SUPPORT to dpa_app CFLAGS aligns both sides.
# Verified live on board 192.168.1.190: dpa_app returns 0, no ioctl error,
# only +3 QMan errors (non-fatal), ping 0% loss.
echo "### ======== dpa_app (real, with fmc + SEC_PROFILE_SUPPORT fix) ========"
DPA_BUILT="$ASK_DIR/dpa_app/dpa_app"
DPA_SRC="$ASK_DIR/dpa_app"
FMC_SRC="$FMC_DIR/source"
FMLIB_INC="$FMLIB_DIR/include/fmd"

# SEC_PROFILE_SUPPORT is MANDATORY — matches kernel Kbuild: cdx/ccflags-y
# (without it, cdx_fman_info layout mismatches → get_port_info ioctl fails)
DPA_CFLAGS="-DNCSW_LINUX -DLS1043 -D__STDC_LIMIT_MACROS -DDPAA_DEBUG_ENABLE -DSEC_PROFILE_SUPPORT -DENDIAN_LITTLE -DVLAN_FILTER -O2"
DPA_INCLUDES="-I$FMC_SRC -I$FMLIB_INC -I$FMLIB_INC/integrations -I$FMLIB_INC/Peripherals -I/usr/include/libxml2 -I$ASK_DIR/cdx"

$CC -c $DPA_CFLAGS $DPA_INCLUDES "$DPA_SRC/main.c" -o "$DPA_SRC/main.o" 2>&1
$CC -c $DPA_CFLAGS $DPA_INCLUDES "$DPA_SRC/dpa.c" -o "$DPA_SRC/dpa.o" 2>&1
$CC -c $DPA_CFLAGS $DPA_INCLUDES "$DPA_SRC/testapp.c" -o "$DPA_SRC/testapp.o" 2>&1

$CXX -o "$DPA_BUILT" \
    "$DPA_SRC/main.o" "$DPA_SRC/dpa.o" "$DPA_SRC/testapp.o" \
    "$FMC_SRC/libfmc.a" \
    -L"$FMLIB_DIR" -lfm \
    -lxml2 -lpthread -lcli \
    -static-libstdc++ -static-libgcc \
    2>&1

[ -f "$DPA_BUILT" ] || { echo "FATAL: dpa_app was not produced"; exit 1; }
echo "### dpa_app ready: $(ls -lh "$DPA_BUILT" | awk '{print $5}')"
file "$DPA_BUILT"

# ===========================================================================
#  Package as .deb
# ===========================================================================
PKG_VER="1.0.0-${KVER}"
DEB_NAME="ask-userspace-${KVER}"
DEB_FILE="${DEB_NAME}_${PKG_VER}_arm64.deb"

STAGE="$(mktemp -d -t ask-usp-deb-stage.XXXXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/DEBIAN"
mkdir -p "$STAGE/usr/sbin"
mkdir -p "$STAGE/etc/systemd/system"
mkdir -p "$STAGE/etc/modules-load.d"
mkdir -p "$STAGE/etc"

# Binaries — cmm goes to /usr/bin per cmm.service ExecStart
mkdir -p "$STAGE/usr/bin"
cp "$CMM_BUILT"       "$STAGE/usr/bin/cmm"
cp "$DPA_BUILT"       "$STAGE/usr/bin/dpa_app"
cp "$FMC_DIR/source/fmc" "$STAGE/usr/sbin/fmc"

# libcli.so — shipped from our release snapshot (libcli1.10 1.10.8 is not
# in VyOS arm64 apt repos; we cross-compile and ship our own).
mkdir -p "$STAGE/usr/local/lib"
if [ -f "$REPO_ROOT/release/ask-6.12.49/libcli.so.1.10.8" ]; then
    cp "$REPO_ROOT/release/ask-6.12.49/libcli.so.1.10.8" "$STAGE/usr/local/lib/"
    ln -sf libcli.so.1.10.8 "$STAGE/usr/local/lib/libcli.so.1.10"
    ln -sf libcli.so.1.10.8 "$STAGE/usr/local/lib/libcli.so"
    echo "### Shipped libcli.so.1.10.8"
else
    echo "WARNING: libcli.so.1.10.8 not found — CMM will fail at runtime"
fi
"${STRIP:-strip}" "$STAGE/usr/bin/cmm" "$STAGE/usr/bin/dpa_app" "$STAGE/usr/sbin/fmc" 2>/dev/null || true

# Runtime config files from ASK repo
# NOTE: modules-load.d is handled by 97-ask-modules.chroot hook, not here.
cp "$ASK_DIR/config/cmm.service"      "$STAGE/etc/systemd/system/cmm.service"
# Fix cmm.service: remove WiFi/vwd pre/post hooks (we don't have WiFi offload),
# and set Restart=no to prevent infinite cycling when cdx is in degraded mode.
sed -i '/ExecStartPre=/d; /ExecStopPost=/d; s/Restart=on-failure/Restart=no/' "$STAGE/etc/systemd/system/cmm.service"
echo "### Patched cmm.service: removed WiFi hooks, set Restart=no"

# Add dependency on ask-cdx.service so /dev/cdx_ctrl exists before CMM starts
sed -i '/^After=network.target/ s/$/ ask-cdx.service/' "$STAGE/etc/systemd/system/cmm.service"
sed -i '/^Wants=systemd-modules-load/ a\Wants=ask-cdx.service' "$STAGE/etc/systemd/system/cmm.service"
echo "### Patched cmm.service: added After/Wants ask-cdx.service"

# Conntrack fix: deploy ask-ct-setup.service (runs vyos-ask-ct-fix to remove
# VyOS notrack rule and ensure nf_conntrack_netlink is loaded before CMM)
cp "$REPO_ROOT/board/systemd/ask-ct-setup.service" "$STAGE/etc/systemd/system/ask-ct-setup.service"
echo "### Deployed ask-ct-setup.service"

# Conntrack fix persistence (specs/conntrack-root-cause-analysis.md §3/§6):
# ask-ct-setup.service only runs once at boot, but a later commit touching
# firewall/nat/system-conntrack config makes VyOS regenerate vyos_conntrack
# with notrack restored. This timer re-applies the same idempotent fix every
# 30s so a regression self-heals instead of persisting until reboot.
cp "$REPO_ROOT/board/systemd/ask-ct-resync.timer"   "$STAGE/etc/systemd/system/ask-ct-resync.timer"
cp "$REPO_ROOT/board/systemd/ask-ct-resync.service" "$STAGE/etc/systemd/system/ask-ct-resync.service"
echo "### Deployed ask-ct-resync.timer/.service"

mkdir -p "$STAGE/usr/local/sbin"
cp "$REPO_ROOT/board/scripts/vyos-ask-ct-fix" "$STAGE/usr/local/sbin/vyos-ask-ct-fix"
chmod +x "$STAGE/usr/local/sbin/vyos-ask-ct-fix"
echo "### Deployed vyos-ask-ct-fix"

# Replace upstream cmm.service with our ls1046a-ask.service wrapper
cp "$REPO_ROOT/board/systemd/ls1046a-ask.service" "$STAGE/etc/systemd/system/ls1046a-ask.service"
echo "### Deployed ls1046a-ask.service (After/Requires ask-ct-setup)"

# dpa_app systemd unit (programs FMan PCD via /dev/cdx_ctrl before CMM starts)
cat > "$STAGE/etc/systemd/system/dpa_app.service" <<'UNIT'
[Unit]
Description=DPA APP — FMan PCD configuration for ASK Fast Path
After=systemd-modules-load.service network-pre.target
Before=cmm.service
Wants=systemd-modules-load.service
ConditionPathExists=/dev/cdx_ctrl
ConditionPathExists=/etc/cdx_cfg.xml
ConditionPathExists=/etc/cdx_pcd.xml

[Service]
Type=oneshot
ExecStart=/usr/bin/dpa_app
ExecStartPost=/bin/sleep 1
RemainAfterExit=yes
Restart=no
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
echo "### Created dpa_app.service"

# Enable dpa_app so it runs before cmm on every boot
# NOTE: cdx.ko spawns dpa_app via call_usermodehelper (START_DPA_APP=1)
# at module load time. The systemd unit is a fallback for manual runs.
# We DON'T auto-enable — cdx handles the first run.
echo "### dpa_app.service created (cdx auto-spawn, systemd as fallback)"
mkdir -p "$STAGE/etc/config"
[ -f "$ASK_DIR/config/fastforward" ] && cp "$ASK_DIR/config/fastforward" "$STAGE/etc/config/fastforward"
[ -f "$ASK_DIR/config/gateway-dk/cdx_cfg.xml" ] && cp "$ASK_DIR/config/gateway-dk/cdx_cfg.xml" "$STAGE/etc/cdx_cfg.xml"
[ -f "$ASK_DIR/dpa_app/files/etc/cdx_pcd.xml" ] && cp "$ASK_DIR/dpa_app/files/etc/cdx_pcd.xml" "$STAGE/etc/cdx_pcd.xml"
[ -f "$ASK_DIR/dpa_app/files/etc/cdx_cfg_dgw.xml" ] && cp "$ASK_DIR/dpa_app/files/etc/cdx_cfg_dgw.xml" "$STAGE/etc/cdx_cfg_dgw.xml"
[ -f "$ASK_DIR/dpa_app/files/etc/cdx_sp.xml" ] && cp "$ASK_DIR/dpa_app/files/etc/cdx_sp.xml" "$STAGE/etc/cdx_sp.xml"

# fmc PDL (Protocol Definition Language) — needed by dpa_app for fmc_compile()
mkdir -p "$STAGE/etc/fmc/config"
[ -f "$FMC_DIR/etc/fmc/config/hxs_pdl_v3.xml" ] && cp "$FMC_DIR/etc/fmc/config/hxs_pdl_v3.xml" "$STAGE/etc/fmc/config/hxs_pdl_v3.xml"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: ${DEB_NAME}
Version: ${PKG_VER}
Section: net
Priority: optional
Architecture: arm64
Maintainer: VyOS LS1046A maintainers <noreply@invalid>
Depends: linux-image-${KVER}, cdx-modules-${KVER}, fci-modules-${KVER}, libpcap0.8, libmnl0, libxml2, libstdc++6
Description: NXP ASK 1.x userspace — cmm, dpa_app, fmc for LS1046A FMan offload
 Connection Manager (cmm), DPAA application (dpa_app), and FMan Configuration
 tool (fmc) — the userspace components of the NXP ASK 1.x fast-path offload
 stack for the LS1046A FMan microcode.
 .
 Requires the CDX/FCI/Auto-Bridge kernel modules and the 210-series FMan
 microcode in SPI flash (mtd3).
EOF

cat > "$STAGE/DEBIAN/postinst" <<'PEOF'
#!/bin/sh
set -e
# Register libcli.so in ldconfig
if [ -d /usr/local/lib ] && [ -f /usr/local/lib/libcli.so.1.10.8 ]; then
    echo "/usr/local/lib" > /etc/ld.so.conf.d/zz-ask-libcli.conf
    ldconfig
fi
if [ -f /etc/systemd/system/ls1046a-ask.service ]; then
    systemctl daemon-reload || true
    systemctl enable ask-ct-setup.service || true
    systemctl enable ask-ct-resync.timer || true
    systemctl enable ls1046a-ask.service || true
    # Mask upstream cmm.service — ls1046a-ask wraps it with conntrack setup
    systemctl mask cmm.service 2>/dev/null || true
fi
exit 0
PEOF
chmod 0755 "$STAGE/DEBIAN/postinst"

echo "### Building $DEB_FILE"
dpkg-deb --build --root-owner-group "$STAGE" "$PKG_DIR/$DEB_FILE"
echo "### ASK userspace .deb produced:"
ls -lh "$PKG_DIR/$DEB_FILE"
dpkg-deb --info "$PKG_DIR/$DEB_FILE"
dpkg-deb --contents "$PKG_DIR/$DEB_FILE"

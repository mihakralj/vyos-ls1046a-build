#!/bin/bash
# ci-build.sh — CI driver for the ASK2 OOT kernel module
#
# Builds ask.ko against the kernel source tree that bin/ci-build-packages.sh
# just compiled, signs the module with the kernel's auto-generated signing
# key, and packages the signed .ko as a Debian package that
# bin/ci-pick-packages.sh will pull into the live-build chroot.
#
# Invariants relied upon (see AGENTS.md and plans/ASK2-IMPLEMENTATION.md):
#   - $KSRC must contain a complete built kernel tree with Module.symvers,
#     vmlinux, scripts/sign-file, and certs/signing_key.{pem,x509}.
#     bin/ci-build-packages.sh calls us BEFORE the post-build cleanup that
#     deletes the kernel source — never reorder.
#   - The kernel must have CONFIG_MODULE_SIG_FORCE=y. Unsigned modules
#     would be rejected at insmod time. The MODULE_SIG_FORCE invariant is
#     enforced by kernel/common/vyos-base/02-module-signing.config.
#   - Cross-build env (ARCH=arm64 CROSS_COMPILE=…) is inherited from the
#     caller. The kernel was just compiled in the same env so this is
#     safe to assume.
#
# Inputs:
#   $1  KSRC       — absolute path to the kernel source tree (required)
#   $2  PKG_DIR    — absolute path to where the .deb should land (required)
#                    (typically the package-build/linux-kernel/ dir, where
#                    bin/ci-pick-packages.sh's `find scripts/package-build`
#                    will sweep it up)
#
# Outputs:
#   ask-modules-${KVER}_${PKG_VER}_arm64.deb in $PKG_DIR
#
# Spec: plans/ASK2-IMPLEMENTATION.md PR3 (M0.3 — wire build pipeline)
set -ex -o pipefail

KSRC="${1:?KSRC required as \$1}"
PKG_DIR="${2:?PKG_DIR required as \$2}"

[ -d "$KSRC" ] || { echo "FATAL: KSRC=$KSRC does not exist"; exit 1; }
[ -f "$KSRC/Module.symvers" ] || { echo "FATAL: $KSRC/Module.symvers missing — kernel not built?"; exit 1; }
[ -x "$KSRC/scripts/sign-file" ] || { echo "FATAL: $KSRC/scripts/sign-file missing — kernel not built?"; exit 1; }
[ -f "$KSRC/certs/signing_key.pem" ] || { echo "FATAL: $KSRC/certs/signing_key.pem missing — MODULE_SIG_KEYS broken?"; exit 1; }
[ -f "$KSRC/certs/signing_key.x509" ] || { echo "FATAL: $KSRC/certs/signing_key.x509 missing"; exit 1; }
[ -d "$PKG_DIR" ] || { echo "FATAL: PKG_DIR=$PKG_DIR does not exist"; exit 1; }

OOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$OOT_DIR"

# Resolve KVER from the kernel tree itself (not from defaults.toml — they
# can drift if the tree was patched). `make kernelrelease` is the
# canonical answer; falls back to `kernelversion` if release reports the
# raw version (no -vyos suffix yet).
KVER="$(make -C "$KSRC" -s kernelrelease 2>/dev/null || true)"
if [ -z "$KVER" ]; then
    KVER="$(make -C "$KSRC" -s kernelversion 2>/dev/null || true)"
fi
[ -n "$KVER" ] || { echo "FATAL: could not resolve KVER from $KSRC"; exit 1; }

# ASK module version. Track MODULE_VERSION() in ask_main.c so dpkg-deb
# version matches what insmod will report.
ASK_VER="$(grep -E '^MODULE_VERSION' ask_main.c | sed -nE 's/.*"([^"]+)".*/\1/p' | head -1)"
[ -n "$ASK_VER" ] || ASK_VER="2.0.0"

echo "### ASK2 OOT module build"
echo "###   KSRC      = $KSRC"
echo "###   KVER      = $KVER"
echo "###   ASK_VER   = $ASK_VER"
echo "###   PKG_DIR   = $PKG_DIR"
echo "###   OOT_DIR   = $OOT_DIR"
echo "###   ARCH=${ARCH:-} CROSS_COMPILE=${CROSS_COMPILE:-}"

# Clean any stale build artefacts left over from a previous CI run on the
# same self-hosted runner workspace.
make -C "$KSRC" M="$OOT_DIR" clean 2>/dev/null || true
rm -f ./*.ko ./*.o ./*.mod ./*.mod.c ./Module.symvers ./modules.order

# Build. Force CONFIG_NET_ASK=m on the command line — there is no in-tree
# Kconfig hook for the OOT module (the in-tree Kconfig only exists inside
# the OOT tree itself; the kernel's main config does not know about it).
make -C "$KSRC" M="$OOT_DIR" \
    CONFIG_NET_ASK=m \
    modules

# Verify the produced .ko exists and is sane.
[ -f "$OOT_DIR/ask.ko" ] || { echo "FATAL: ask.ko was not produced"; exit 1; }
file "$OOT_DIR/ask.ko"
modinfo "$OOT_DIR/ask.ko" || true

# Sign every produced .ko (just `ask.ko` for now; future PRs add
# ask_bridge.ko under the same Kbuild).
echo "### Signing OOT modules with kernel's auto-generated signing key"
for ko in "$OOT_DIR"/*.ko; do
    [ -f "$ko" ] || continue
    "$KSRC/scripts/sign-file" sha512 \
        "$KSRC/certs/signing_key.pem" \
        "$KSRC/certs/signing_key.x509" \
        "$ko"
    # Confirm the signature trailer was appended.
    if ! tail -c 28 "$ko" | grep -q "Module signature appended"; then
        echo "FATAL: $(basename "$ko") was not signed (no signature trailer found)"
        exit 1
    fi
    echo "###   signed: $(basename "$ko") ($(stat -c '%s bytes' "$ko"))"
done

# ─── Package as a .deb ─────────────────────────────────────────────────
#
# Layout matches what live-build's `lb chroot_install-packages` expects:
#   /lib/modules/$KVER/extra/ask.ko
#
# `depmod -A` is run in the chroot at first-boot by systemd-modules-load,
# so we don't need to ship modules.dep — it'll be regenerated.
#
# Package version: ${ASK_VER}-${KVER} so a kernel ABI bump invalidates the
# old .deb (apt won't replay an ask.ko built against a different kernel).
PKG_VER="${ASK_VER}-${KVER}"
DEB_NAME="ask-modules-${KVER}"
DEB_FILE="${DEB_NAME}_${PKG_VER}_arm64.deb"

STAGE="$(mktemp -d -t ask-deb-stage.XXXXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/DEBIAN"
mkdir -p "$STAGE/lib/modules/${KVER}/extra"

cp "$OOT_DIR"/*.ko "$STAGE/lib/modules/${KVER}/extra/"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: ${DEB_NAME}
Version: ${PKG_VER}
Section: kernel
Priority: optional
Architecture: arm64
Maintainer: VyOS LS1046A maintainers <noreply@invalid>
Depends: linux-image-${KVER}
Description: ASK2 OOT kernel modules for LS1046A FMan/210 hardware offload
 Out-of-tree kernel modules implementing the ASK2 fast-path offload
 for the NXP LS1046A FMan microcode (210-series). Replaces the legacy
 proprietary cdx.ko / auto_bridge.ko stack.
 .
 See specs/ask2-rewrite-spec.md for the architecture and
 plans/ASK2-IMPLEMENTATION.md for the implementation status.
EOF

# postinst: regenerate the modules.dep so insmod-by-name works on next boot.
cat > "$STAGE/DEBIAN/postinst" <<EOF
#!/bin/sh
set -e
if [ -d /lib/modules/${KVER} ]; then
    depmod -a ${KVER} || true
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst"

# postrm: same — refresh modules.dep after removal.
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

echo "### ASK2 OOT module .deb produced:"
ls -lh "$PKG_DIR/$DEB_FILE"
dpkg-deb --info "$PKG_DIR/$DEB_FILE"
dpkg-deb --contents "$PKG_DIR/$DEB_FILE"
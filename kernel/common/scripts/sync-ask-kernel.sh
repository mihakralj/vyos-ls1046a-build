#!/bin/sh
# sync-ask-kernel.sh — vendor the kernel-side ASK inputs as a matched set:
#   1. pristine NXP DPAA/FMan/QBMan SDK driver source -> kernel/flavors/ask/sdk-sources/
#   2. ASK's own kernel patch series (010-NNN)         -> kernel/flavors/ask/patches/
# stage-kernel.sh overlays (1) onto the fetched NXP kernel tree, then applies
# (2) plus our vyos-local patches on top -- see stage-kernel.sh's ASK section.
#
# ASK is the SINGLE pin (kernel/flavors/ask/ask-version.env). ASK's patches
# are developed against a specific NXP SDK overlay, and ASK records that
# overlay in a format-neutral pin file (pins/nxp-sdk-srcrev.inc, one line:
# NXP_SDK_SRCREV = "<sha>"). This script fetches ASK@ASK_VERSION and READS
# that ref, so the SDK ref can never drift from what ASK was built against
# -- ASK_VERSION is the only pin to bump. Mirrors we-are-mono/openwrt's
# scripts/mono-sync-ask-kernel.sh (verified against their tree 2026-09-11).
#
# Unlike openwrt (which builds on top of a mainline kernel.org tree and so
# must also vendor the NXP-flavoured "base" dtsi files -- fsl-ls1046a.dtsi,
# fsl-ls1046-post.dtsi, the non-SDK qoriq-{q,b}man-portals.dtsi -- to shadow
# mainline's incompatible bindings), FLAVOR=ask here starts from the FULL
# NXP LSDK kernel tree (fetch-kernel-nxp.sh), which already ships correct
# native versions of those files. Only the SDK-specific fragments actually
# used by the DPAA1 driver are vendored below.
#
# Board DTS (board/dtb/mono-gateway-dk*.dts) is intentionally OUT OF SCOPE:
# unlike openwrt, which takes ASK's canonical board DTS verbatim, ours is a
# heavily VyOS-customized fork (fan control, LEDs, etc.) with real content
# divergence from ASK's own -- a permanent local fork, not something to
# sync. (The stale copies previously duplicated under sdk-sources/ were
# dead weight: stage-kernel.sh's separate board-DTS-injection step, which
# copies from board/dtb/, always overwrote them after this overlay step
# ran, and a "regenerate sdk-sources/ from source" resync as done here does
# not recreate them.)
#
# --check: don't write anything; fetch the pinned upstreams and DIFF them
# against the committed tree, exiting nonzero on any drift. Same code path
# as sync, so "check" can't disagree with what "sync" would produce.
#
# Usage: kernel/common/scripts/sync-ask-kernel.sh [--check]
set -eu

cd "$(dirname "$0")/../../.."   # repo root

MODE=sync
[ "${1:-}" = "--check" ] && MODE=check

# --- pins ---
# ASK is the single authoritative pin. The NXP SDK ref is READ from ASK's
# recipe (NXP_SDK_SRCREV) below, never hand-maintained here.
. kernel/flavors/ask/ask-version.env   # ASK_REPO, ASK_VERSION
NXP_URL="https://github.com/nxp-qoriq/linux.git"
ASK_SDK_PIN="pins/nxp-sdk-srcrev.inc"   # ASK's format-neutral NXP SDK SRCREV pin

# vyos-local, non-ASK kernel patches that must NEITHER be re-copied NOR
# cleaned by this sync -- they carry no ASK-verbatim drift guard (git
# tracks them directly). Board-specific device-node population, not part
# of upstream ASK (see kernel/flavors/ask/patches/README.md provenance
# section). Mirrors openwrt's MONO_FWD mechanism for its 070 forward-port.
#
# LSDK_FWD: patches we DO take from ASK but had to forward-port onto the
# real NXP LSDK vendor tree (fetch-kernel-nxp.sh's lf-6.12.49-2.2.0)
# instead of the pristine-mainline base ASK itself develops against --
# same category as openwrt's own 070 forward-port, just for a different
# base-tree mismatch. `--check` would otherwise permanently flag these as
# drifted, since they are intentionally not byte-identical to ASK's raw
# patch. See kernel/flavors/ask/patches/README.md for what changed in each
# and why; re-verify (not blindly re-copy) when ASK_VERSION bumps.
LOCAL_PATCHES=" 005-fman-populate-mac-children.patch 006-proxy-populate-dpaa-eth-children.patch 007-sdk-dpaa-select-phylink.patch 140-sdk-dpaa-select-queue-3arg.patch "
LSDK_FWD=" 010-ask-fman-dpaa-ehash.patch 020-ask-bridge-hooks.patch 030-ask-ipv4-ipv6-forwarding.patch 040-ask-xfrm-ipsec-offload.patch 097-xfrm-trans-queue-force-dst-refcount.patch "

SDK_DIR="kernel/flavors/ask/sdk-sources"
PATCHES_DIR="kernel/flavors/ask/patches"
ASK_MANIFEST="$PATCHES_DIR/.ask-kernel-patches"   # provenance + list of synced ASK patches

DRIFT=0

# Fetch <paths...> at <ref> from <url> into <tmp>. Remote: blobless+sparse+shallow.
sparse_fetch() {  # url ref tmp path...
	_url=$1; _ref=$2; _tmp=$3; shift 3
	case "$_url" in
	file://*)
		_repo=${_url#file://}
		git -C "$_repo" archive "$_ref" "$@" | tar -x -C "$_tmp"
		;;
	*)
		git -C "$_tmp" init -q
		git -C "$_tmp" remote add origin "$_url"
		git -C "$_tmp" sparse-checkout init --cone
		git -C "$_tmp" sparse-checkout set "$@"
		git -C "$_tmp" fetch -q --depth 1 --filter=blob:none origin "$_ref"
		git -C "$_tmp" checkout -q FETCH_HEAD
		;;
	esac
}

# sync: install <src> at <dest>. check: diff instead, flag DRIFT. Handles dir or file.
install_item() {  # src dest label
	_src=$1; _dest=$2; _label=$3
	if [ "$MODE" = check ]; then
		if [ -d "$_src" ]; then
			{ [ -d "$_dest" ] && diff -rq "$_src" "$_dest" >/dev/null 2>&1; } || { echo "  DRIFT: $_label"; DRIFT=1; }
		else
			{ [ -f "$_dest" ] && diff -q "$_src" "$_dest" >/dev/null 2>&1; } || { echo "  DRIFT: $_label"; DRIFT=1; }
		fi
	else
		if [ -d "$_src" ]; then mkdir -p "$_dest"; rsync -a --delete "$_src/" "$_dest/"
		else mkdir -p "$(dirname "$_dest")"; cp -a "$_src" "$_dest"; fi
	fi
}

KTMP="$(mktemp -d)"; ATMP="$(mktemp -d)"
trap 'rm -rf "$KTMP" "$ATMP"' EXIT

# ===== 1. Fetch ASK (single pin): patch series + the kernel recipe =====
echo "sync-ask-kernel [$MODE]: fetching ASK @ $ASK_VERSION"
sparse_fetch "$ASK_REPO" "$ASK_VERSION" "$ATMP" patches/kernel pins

# The single-pin source of truth: read the NXP SDK overlay ref from ASK's pin file.
NXP_SDK_REF=$(sed -n 's/^NXP_SDK_SRCREV *= *"\([0-9a-fA-F]\{40\}\)".*/\1/p' "$ATMP/$ASK_SDK_PIN" | head -1)
[ -n "$NXP_SDK_REF" ] || { echo "  ERROR: could not read NXP_SDK_SRCREV from ASK $ASK_SDK_PIN" >&2; exit 1; }
echo "sync-ask-kernel [$MODE]: ASK pins NXP SDK @ $NXP_SDK_REF"

# ===== 2. Pristine NXP SDK driver source -> sdk-sources/ =====
# PRISTINE NXP only. ASK-added files (e.g. include/linux/fsl_oh_port.h,
# introduced as a new file by patch 010-ask-fman-dpaa-ehash) arrive with
# the patch series below, never from here -- vendoring them here would
# make the patch's own "new file" hunk fail to apply (file already exists).
SDK_PATHS="drivers/net/ethernet/freescale/sdk_dpaa
drivers/net/ethernet/freescale/sdk_fman
drivers/staging/fsl_qbman
include/uapi/linux/fmd
include/linux/fsl_bman.h
include/linux/fsl_qman.h
include/linux/fsl_usdpaa.h
arch/arm64/boot/dts/freescale/qoriq-qman-portals-sdk.dtsi
arch/arm64/boot/dts/freescale/qoriq-bman-portals-sdk.dtsi
arch/arm64/boot/dts/freescale/qoriq-dpaa-eth.dtsi
arch/arm64/boot/dts/freescale/qoriq-fman3-0.dtsi
arch/arm64/boot/dts/freescale/qoriq-fman3-0-1g-0.dtsi
arch/arm64/boot/dts/freescale/qoriq-fman3-0-1g-1.dtsi
arch/arm64/boot/dts/freescale/qoriq-fman3-0-1g-2.dtsi
arch/arm64/boot/dts/freescale/qoriq-fman3-0-1g-3.dtsi
arch/arm64/boot/dts/freescale/qoriq-fman3-0-1g-4.dtsi
arch/arm64/boot/dts/freescale/qoriq-fman3-0-1g-5.dtsi
arch/arm64/boot/dts/freescale/qoriq-fman3-0-10g-0.dtsi
arch/arm64/boot/dts/freescale/qoriq-fman3-0-10g-1.dtsi"
SDK_SPARSE="drivers/net/ethernet/freescale/sdk_dpaa
drivers/net/ethernet/freescale/sdk_fman
drivers/staging/fsl_qbman
include/uapi/linux/fmd
include/linux
arch/arm64/boot/dts/freescale"

echo "sync-ask-kernel [$MODE]: fetching NXP SDK @ $NXP_SDK_REF"
# shellcheck disable=SC2086
sparse_fetch "$NXP_URL" "$NXP_SDK_REF" "$KTMP" $SDK_SPARSE
for p in $SDK_PATHS; do
	[ -e "$KTMP/$p" ] || { echo "  MISSING in NXP source: $p (ASK-added files come from the patch series)" >&2; exit 1; }
	install_item "$KTMP/$p" "$SDK_DIR/$p" "SDK $p"
done
if [ "$MODE" = check ]; then
	[ "$(cat "$SDK_DIR/.nxp-sdk-ref" 2>/dev/null || true)" = "$NXP_SDK_REF" ] || { echo "  DRIFT: $SDK_DIR/.nxp-sdk-ref != $NXP_SDK_REF"; DRIFT=1; }
else
	printf '%s\n' "$NXP_SDK_REF" > "$SDK_DIR/.nxp-sdk-ref"
fi

# ===== 3. ASK kernel patch series -> patches/ =====
# In sync mode, drop patches ASK has since removed before re-copying (from the manifest).
if [ "$MODE" = sync ] && [ -f "$ASK_MANIFEST" ]; then
	while IFS= read -r old; do
		case "$old" in ''|\#*) continue ;; esac
		case "$LOCAL_PATCHES" in *" $old "*) continue ;; esac   # never clean a vyos-local patch
		case "$LSDK_FWD" in *" $old "*) continue ;; esac        # never clean an LSDK forward-port
		rm -f "$PATCHES_DIR/$old"
	done < "$ASK_MANIFEST"
fi

fetched=""
for f in "$ATMP"/patches/kernel/*.patch; do
	b=$(basename "$f")
	case "$b" in 999-*) continue ;; esac   # legacy 5.4 monolith, never applied
	case "$LOCAL_PATCHES" in *" $b "*) [ "$MODE" = sync ] && echo "  keeping vyos-local patch (not synced): $b"; continue ;; esac
	fetched="$fetched $b"
	case "$LSDK_FWD" in
		*" $b "*) [ "$MODE" = sync ] && echo "  keeping LSDK forward-port (not overwritten): $b" ;;
		*) install_item "$f" "$PATCHES_DIR/$b" "ASK patch $b" ;;
	esac
done

if [ "$MODE" = check ]; then
	# Manifest must pin THIS ASK_VERSION and list exactly the fetched set (catch stale
	# patches that would otherwise linger in the tree).
	grep -q "@ $ASK_VERSION\$" "$ASK_MANIFEST" 2>/dev/null || { echo "  DRIFT: $ASK_MANIFEST not synced from $ASK_VERSION"; DRIFT=1; }
	if [ -f "$ASK_MANIFEST" ]; then
		while IFS= read -r m; do
			case "$m" in ''|\#*) continue ;; esac
			case " $fetched " in *" $m "*) ;; *) echo "  DRIFT: committed ASK patch $m not in ASK@$ASK_VERSION"; DRIFT=1 ;; esac
		done < "$ASK_MANIFEST"
	fi
else
	{ echo "# ASK kernel patches synced from $ASK_REPO @ $ASK_VERSION"
	  for b in $fetched; do echo "$b"; done; } > "$ASK_MANIFEST"
fi

if [ "$MODE" = check ]; then
	[ "$DRIFT" = 0 ] && echo "sync-ask-kernel: OK -- tree matches ASK@$ASK_VERSION + NXP@$NXP_SDK_REF" \
		|| { echo "sync-ask-kernel: DRIFT detected -- run kernel/common/scripts/sync-ask-kernel.sh to refresh" >&2; exit 1; }
else
	echo "sync-ask-kernel: done -- $(find "$SDK_DIR" -type f ! -name '.nxp-sdk-ref' | wc -l) SDK files + $(echo $fetched | wc -w) ASK kernel patches"
fi

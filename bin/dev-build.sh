#!/bin/bash
# bin/dev-build.sh — Fast dev-loop build on the Cobalt 100 ARM64 VM.
#
# Replaces the historical "build on LXC 200" loop. Cobalt 100 is native
# aarch64 (32 cores, 125 GB RAM), so kernel builds are MUCH faster than
# the LXC 200 cross-compile path (~30 s incremental, ~2–3 min full,
# vs. ~2 min / ~8 min on LXC 200). The board's U-Boot still TFTPs/HTTPs
# from 192.168.1.137 (LXC 200), so after each build we rsync artefacts
# to admin@192.168.1.137:/srv/tftp/ via passwordless sudo.
#
# All build steps reuse the EXACT CI scripts under bin/ci-*.sh and
# kernel/common/scripts/stage-kernel.sh — no forked build logic.
#
# Modes:
#   kernel              Stage + build kernel Image natively, push to TFTP.
#   dtb                 Rebuild board/dtb/mono-gw.dtb from DTS, push to TFTP.
#   extract <iso>       Extract vmlinuz/initrd/dtb from an ISO, push to TFTP.
#   iso-live [<iso>]    Extract live artefacts (kernel/initrd/dtb/squashfs)
#                       for the dev_boot_live U-Boot env, push to TFTP.
#                       With no arg, downloads the newest GitHub release.
#   iso                 Run bin/local-build.sh with $FLAVOR set (default | ask
#                       | vpp), then publish the resulting .iso (+ .minisig if
#                       produced) to admin@LXC200:/srv/tftp/iso/ so the board
#                       can pull it with `add system image http://192.168.1.137
#                       :8080/iso/<name>.iso`. Symlinks
#                       /srv/tftp/iso/latest-<flavor>.iso to the just-built
#                       ISO. Roughly ~40 min cold, ~7 min warm caches.
#   push                Just rsync work/dev-tftp/ -> admin@LXC200:/srv/tftp/.
#   help                This message.
#
# Environment:
#   FLAVOR              default | ask | vpp        (default: default)
#   LXC200_HOST         SSH target for TFTP server (default: admin@192.168.1.137)
#   TFTP_DIR_REMOTE     Path on LXC 200             (default: /srv/tftp)
#   USE_CCACHE          1 to wire ccache (default: 1 if /usr/bin/ccache exists)
#   JOBS                make -j N                   (default: nproc)
#   SSH_KEY             SSH identity                (default: ~/.ssh/admin_key)
#
# Local staging area: $REPO_ROOT/work/dev-tftp/  (rsync source).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Defaults ──────────────────────────────────────────────────────────
FLAVOR="${FLAVOR:-default}"
LXC200_HOST="${LXC200_HOST:-admin@192.168.1.137}"
TFTP_DIR_REMOTE="${TFTP_DIR_REMOTE:-/srv/tftp}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/admin_key}"
JOBS="${JOBS:-$(nproc)}"
USE_CCACHE="${USE_CCACHE:-1}"

# Local rsync source — single directory we sync to LXC 200.
TFTP_STAGE="$REPO_ROOT/work/dev-tftp"
mkdir -p "$TFTP_STAGE"

# ── Output helpers ────────────────────────────────────────────────────
B='\033[1m'; G='\033[32m'; Y='\033[33m'; R='\033[31m'; N='\033[0m'
hdr()  { echo -e "\n${B}━━━ $* ━━━${N}"; }
info() { echo -e "${B}[•]${N} $*"; }
ok()   { echo -e "${G}[✓]${N} $*"; }
warn() { echo -e "${Y}[!]${N} $*" >&2; }
die()  { echo -e "${R}[✗]${N} $*" >&2; exit 1; }

# ── Native build env ──────────────────────────────────────────────────
# On native aarch64 we DROP CROSS_COMPILE entirely (much faster). The CI
# scripts honour ARCH/CROSS_COMPILE from env, so just set them here.
export ARCH=arm64
if [ "$(uname -m)" = "aarch64" ]; then
    export CROSS_COMPILE=""
else
    export CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
    warn "Not on aarch64 — falling back to CROSS_COMPILE=$CROSS_COMPILE"
fi

# Wire ccache for kernel C compiles.
if [ "$USE_CCACHE" = "1" ] && command -v ccache >/dev/null 2>&1; then
    export KBUILD_BUILD_TIMESTAMP="${KBUILD_BUILD_TIMESTAMP:-$(date -u +'%Y-%m-%dT%H:%M:%SZ')}"
    export CC="ccache ${CROSS_COMPILE}gcc"
    info "ccache enabled (stats: $(ccache -s 2>/dev/null | awk '/cache hit rate/ {print $0; exit}'))"
fi

# CI scripts expect GITHUB_WORKSPACE etc. Provide minimal shims so they
# can be invoked directly from a developer shell.
export GITHUB_WORKSPACE="$REPO_ROOT"
# Namespace these on EUID so a previous sudo-as-root run cannot leave
# /tmp/dev-gh_output owned-by-root and break a subsequent non-root run
# (and vice versa). The `iso` mode re-invokes the script under sudo, so
# both UIDs touch these in normal operation.
export GITHUB_OUTPUT="${GITHUB_OUTPUT:-/tmp/dev-gh_output.$(id -u)}"
export GITHUB_ENV="${GITHUB_ENV:-/tmp/dev-gh_env.$(id -u)}"
export GITHUB_STEP_SUMMARY="${GITHUB_STEP_SUMMARY:-/tmp/dev-gh_step_summary.$(id -u)}"
: > "$GITHUB_OUTPUT"; : > "$GITHUB_ENV"; : > "$GITHUB_STEP_SUMMARY"
export FLAVOR

# Resolve kernel version (same logic as stage-kernel.sh / common.sh).
KVER=""
[ -f vyos-build/data/defaults.toml ] && \
    KVER=$(awk -F'"' '/^kernel_version/ {print $2; exit}' vyos-build/data/defaults.toml)
# versions.lock uses shell-assignment form `: "${KERNEL_VERSION:=X}"`, so
# source it (in a subshell) rather than grepping a bare `KERNEL_VERSION=` line.
[ -z "$KVER" ] && [ -f versions.lock ] && \
    KVER=$(. versions.lock >/dev/null 2>&1; printf '%s' "$KERNEL_VERSION")
KVER="${KVER:-6.18.34}"
KSRC="$REPO_ROOT/work/linux-$KVER"

# ── SSH/rsync wrappers ────────────────────────────────────────────────
SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
ssh_lxc()  { ssh "${SSH_OPTS[@]}" "$LXC200_HOST" "$@"; }
rsync_lxc() {
    rsync -e "ssh ${SSH_OPTS[*]}" --rsync-path="sudo rsync" "$@"
}

check_lxc_reachable() {
    if ! ssh -o ConnectTimeout=5 "${SSH_OPTS[@]}" "$LXC200_HOST" true 2>/dev/null; then
        die "Cannot reach LXC 200 at $LXC200_HOST (key=$SSH_KEY). \
Check that the VM has LAN/Tailscale connectivity to 192.168.1.137 and that \
$SSH_KEY is authorised on the LXC."
    fi
}

# ── push: rsync staging dir to /srv/tftp on LXC 200 ───────────────────
cmd_push() {
    hdr "Pushing $TFTP_STAGE → $LXC200_HOST:$TFTP_DIR_REMOTE"
    check_lxc_reachable
    if [ -z "$(ls -A "$TFTP_STAGE" 2>/dev/null)" ]; then
        warn "$TFTP_STAGE is empty — nothing to push"
        return 0
    fi
    rsync_lxc -av --info=progress2 "$TFTP_STAGE/" "$LXC200_HOST:$TFTP_DIR_REMOTE/"
    ok "Push complete"
    info "Now from U-Boot console:  run dev_boot   (or: run dev_boot_live)"
}

# ── kernel: stage + build + push ──────────────────────────────────────
cmd_kernel() {
    hdr "Stage kernel tree (FLAVOR=$FLAVOR, KERNEL=$KVER)"
    # stage-kernel.sh handles patches, file injection, defconfig fragment
    # merge AND `make olddefconfig` — it is the single source of truth for
    # producing a ready-to-build kernel tree. We do NOT run ci-setup-kernel.sh
    # here: that script writes into the vyos-build checkout (which is
    # root-owned and only meaningful for the package-build pipeline) and is
    # redundant with stage-kernel.sh for direct `make Image` builds.
    bash bin/ci-stage-kernel.sh

    [ -d "$KSRC" ] || die "Staged kernel tree not at $KSRC (stage-kernel.sh failed?)"

    hdr "Building kernel (native arm64, -j$JOBS)"
    local _T0=$SECONDS
    # LOCALVERSION=-vyos is MANDATORY — it is the vermagic suffix every
    # released kernel uses (build-kernel.sh:42 default, CI ships `*-vyos`
    # .deb packages). Without it the dev-built kernel reports `6.18.31+`
    # which mismatches every signed -vyos OOT module and breaks ask.ko
    # insmod on the DUT. See AGENTS.md "Kernel signing artifacts" and
    # Qdrant memory "kernel LOCALVERSION invariant".
    (
        cd "$KSRC"
        make ARCH="$ARCH" LOCALVERSION=-vyos -j"$JOBS" Image
    )
    ok "Kernel built in $(( SECONDS - _T0 ))s"

    local img="$KSRC/arch/arm64/boot/Image"
    [ -f "$img" ] || die "Image not produced at $img"
    cp "$img" "$TFTP_STAGE/vmlinuz"
    ok "vmlinuz staged ($(du -h "$TFTP_STAGE/vmlinuz" | cut -f1))"

    # DTB always rebuilt alongside kernel for consistency.
    _rebuild_dtb

    cmd_push
}

_rebuild_dtb() {
    hdr "Compiling Mono Gateway DTB"
    bash bin/ci-compile-mono-dtb.sh
    [ -f board/dtb/mono-gw.dtb ] || die "ci-compile-mono-dtb.sh produced no DTB"
    cp board/dtb/mono-gw.dtb "$TFTP_STAGE/mono-gw.dtb"
    ok "mono-gw.dtb staged ($(du -h "$TFTP_STAGE/mono-gw.dtb" | cut -f1))"
}

cmd_dtb() {
    _rebuild_dtb
    cmd_push
}

# ── extract / iso-live: pull artefacts out of an ISO ──────────────────
_find_iso() {
    local explicit="${1:-}"
    if [ -n "$explicit" ]; then
        [ -f "$explicit" ] || die "ISO not found: $explicit"
        echo "$explicit"
        return
    fi
    # Newest local ISO under /tmp or repo root.
    local cand
    cand=$(ls -1t /tmp/vyos-*-LS1046A-*.iso "$REPO_ROOT"/vyos-*-LS1046A-*.iso 2>/dev/null | head -1 || true)
    if [ -n "$cand" ]; then
        echo "$cand"
        return
    fi
    # Fall back to gh release.
    command -v gh >/dev/null || die "No local ISO and gh not installed — pass <iso> explicitly"
    info "Downloading newest GitHub release ISO …"
    cand=$(gh release view --repo mihakralj/vyos-ls1046a-build --json assets \
            --jq '.assets[] | select(.name|endswith("-arm64.iso")) | .name' | head -1)
    [ -n "$cand" ] || die "Could not find an ISO asset in the latest release"
    if [ ! -f "/tmp/$cand" ]; then
        gh release download --repo mihakralj/vyos-ls1046a-build --pattern "$cand" --dir /tmp
    fi
    echo "/tmp/$cand"
}

_extract_from_iso() {
    # $1 = iso path, $2 = "boot" (vmlinuz/initrd/dtb only) or "live" (also squashfs)
    local iso="$1" mode="$2"
    info "Source ISO: $iso"
    local tmp; tmp=$(mktemp -d)
    trap "rm -rf '$tmp'" RETURN

    # xorriso extracts the ISO9660 contents without root/loop mount.
    info "Extracting via xorriso …"
    xorriso -osirrox on -indev "$iso" \
        -extract /live/vmlinuz       "$tmp/vmlinuz" \
        -extract /live/initrd.img    "$tmp/initrd.img" \
        -extract /mono-gw.dtb         "$tmp/mono-gw.dtb" \
        2>/dev/null

    [ -f "$tmp/vmlinuz" ]    || die "ISO has no /live/vmlinuz"
    [ -f "$tmp/initrd.img" ] || die "ISO has no /live/initrd.img"
    [ -f "$tmp/mono-gw.dtb" ] || warn "ISO has no /mono-gw.dtb (legacy ISO?) — keeping existing TFTP copy"

    cp "$tmp/vmlinuz"    "$TFTP_STAGE/vmlinuz"
    cp "$tmp/initrd.img" "$TFTP_STAGE/initrd.img"
    [ -f "$tmp/mono-gw.dtb" ] && cp "$tmp/mono-gw.dtb" "$TFTP_STAGE/mono-gw.dtb"

    if [ "$mode" = "live" ]; then
        xorriso -osirrox on -indev "$iso" \
            -extract /live/filesystem.squashfs "$tmp/filesystem.squashfs" 2>/dev/null
        [ -f "$tmp/filesystem.squashfs" ] || die "ISO has no /live/filesystem.squashfs"
        cp "$tmp/filesystem.squashfs" "$TFTP_STAGE/filesystem.squashfs"
        ok "Staged: vmlinuz, initrd.img, mono-gw.dtb, filesystem.squashfs"
    else
        ok "Staged: vmlinuz, initrd.img, mono-gw.dtb"
    fi
}

cmd_extract() {
    local iso; iso=$(_find_iso "${1:-}")
    hdr "Extract boot artefacts from ISO"
    _extract_from_iso "$iso" boot
    cmd_push
}

cmd_iso_live() {
    local iso; iso=$(_find_iso "${1:-}")
    hdr "Extract live-boot artefacts from ISO (kernel + initrd + DTB + squashfs)"
    _extract_from_iso "$iso" live
    cmd_push
    info "On the board's U-Boot console:  run dev_boot_live"
    info "(squashfs is served via HTTP by LXC 200 on :8080)"
}

# ── iso: full ISO build via bin/local-build.sh + publish to LXC 200 ───
#
# Builds a FLAVOR-tagged ISO locally using the same CI script chain that
# self-hosted CI uses, then rsyncs the .iso (+ .minisig if produced) to
# admin@LXC200:/srv/tftp/iso/ so the running board can pull it with
#   add system image http://192.168.1.137:8080/iso/<file>.iso
# A symlink /srv/tftp/iso/latest-<flavor>.iso is updated to the just-
# built ISO so dev workflows can always grab the freshest build from a
# stable URL.
#
# Requires sudo on this VM (local-build.sh installs host build-deps).
cmd_iso() {
    hdr "Full ISO build (FLAVOR=$FLAVOR) via bin/local-build.sh"

    if [ "$(id -u)" -ne 0 ]; then
        info "local-build.sh installs apt packages — re-invoking under sudo"
        # Preserve FLAVOR + auth env across the sudo boundary.
        exec sudo --preserve-env=FLAVOR,LXC200_HOST,TFTP_DIR_REMOTE,SSH_KEY,JOBS,USE_CCACHE \
            HOME="$HOME" bash "$0" iso "$@"
    fi

    check_lxc_reachable

    # Clean any stale ISO so the post-build `ls` picks up only the new one.
    rm -f "$REPO_ROOT"/vyos-*-LS1046A-*.iso "$REPO_ROOT"/vyos-*-LS1046A-*.iso.minisig

    local _T0=$SECONDS
    FLAVOR="$FLAVOR" bash "$REPO_ROOT/bin/local-build.sh"
    ok "ISO build finished in $(( SECONDS - _T0 ))s"

    # The build leaves exactly one ISO at the repo root; pick it up.
    local iso
    iso=$(ls -1t "$REPO_ROOT"/vyos-*-LS1046A-"$FLAVOR"-arm64.iso 2>/dev/null | head -1)
    [ -n "$iso" ] && [ -f "$iso" ] \
        || die "No ISO matching vyos-*-LS1046A-$FLAVOR-arm64.iso at $REPO_ROOT"
    local iso_name; iso_name=$(basename "$iso")
    info "Built: $iso_name ($(du -h "$iso" | cut -f1))"

    hdr "Publishing to $LXC200_HOST:$TFTP_DIR_REMOTE/iso/"
    ssh_lxc "sudo install -d -m 0755 -o admin -g admin $TFTP_DIR_REMOTE/iso"
    rsync_lxc -av --info=progress2 "$iso" "$LXC200_HOST:$TFTP_DIR_REMOTE/iso/$iso_name"
    if [ -f "$iso.minisig" ]; then
        rsync_lxc -av "$iso.minisig" "$LXC200_HOST:$TFTP_DIR_REMOTE/iso/$iso_name.minisig"
    fi
    # Refresh the stable "latest" symlink for this flavor.
    ssh_lxc "sudo ln -sfn '$iso_name' '$TFTP_DIR_REMOTE/iso/latest-$FLAVOR.iso'"
    ok "Published"

    local lxc_ip="${LXC200_HOST#*@}"
    echo
    info "On the running board (vyos@192.168.1.190):"
    echo "    add system image http://${lxc_ip}:8080/iso/$iso_name"
    info "Or via the stable 'latest' alias:"
    echo "    add system image http://${lxc_ip}:8080/iso/latest-$FLAVOR.iso"
}

# ── Dispatch ──────────────────────────────────────────────────────────
case "${1:-help}" in
    kernel)     shift; cmd_kernel    "$@" ;;
    dtb)        shift; cmd_dtb       "$@" ;;
    extract)    shift; cmd_extract   "$@" ;;
    iso-live)   shift; cmd_iso_live  "$@" ;;
    iso)        shift; cmd_iso       "$@" ;;
    push)       shift; cmd_push      "$@" ;;
    help|-h|--help)
        # Print only the leading header comment block (lines starting with #).
        awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"
        ;;
    *)
        die "Unknown mode: $1   (try: $0 help)"
        ;;
esac

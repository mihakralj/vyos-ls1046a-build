#!/usr/bin/env bash
# apply-to-tree.sh — wet-run counterpart of patch-health.sh.
#
# Takes a clean linux-6.6.y source tree and turns it into an ASK-ready tree by:
#   1. Copying release/patches/kernel/sdk-sources/ into the tree (266 files)
#   2. Applying every release/patches/{vyos,ask,fixes}/*.patch in subdir + name
#      sort order (-p1). Layout mirrors ASK-mono:
#        vyos/001..003-vyos-*.patch                     (VyOS deltas)
#        ask/010-ask-fman-dpaa-ehash.patch              (FMan/DPAA wiring)
#        ask/020-ask-bridge-hooks.patch                 (bridge fast-path hooks)
#        ask/030-ask-ipv4-ipv6-forwarding.patch         (IPv4/IPv6 fast-path)
#        ask/040-ask-xfrm-ipsec-offload.patch           (IPsec offload, gated)
#        ask/050-ask-conntrack-offload.patch            (conntrack offload)
#        ask/060-ask-netfilter-qosmark.patch            (netfilter + QOS xt)
#        ask/070-ask-ppp-hooks.patch                    (PPP fast-path hooks)
#        ask/080-wext-core-restore-ndo_do_ioctl.patch   (wext core restore)
#        fixes/093-netlink-name-L2FLOW-cb-mutex.patch   (lockdep mutex name)
#        fixes/094-swphy-10g-fixed-link.patch           (10G swphy fixed-link)
#   3. Assembling .config via merge_config.sh chain:
#        release/vyos-base/arm64/vyos_defconfig   (VyOS arm64 base)
#      + release/vyos-base/*.config               (VyOS feature snippets)
#      + release/ask.config                       (LS1046A/DPAA delta, wins last)
#      (falls back to `make defconfig` + `cat ask.config` if vyos-base/ is absent)
#   4. Running `make ARCH=arm64 olddefconfig` to resolve new symbols
#
# Source of truth: release/ (committed in this repo).
#
# Usage:
#   ./scripts/apply-to-tree.sh                      # auto-fetch kernel + apply
#   ./scripts/apply-to-tree.sh 6.6.123              # fetch/pin kernel first
#   ./scripts/apply-to-tree.sh --kdir /path/to/src  # apply to an external tree
#   ./scripts/apply-to-tree.sh --defconfig foo      # seed config (default: defconfig)
#
# Exit codes:
#   0  tree is ASK-ready
#   1  patch rejects, missing source, or make olddefconfig failed

set -euo pipefail
source "$(dirname "$0")/common.sh"

need git find cp make jq

VERSION_ARG=""
KDIR_ARG=""
DEFCONFIG="${KERNEL_DEFCONFIG:-defconfig}"

while (( $# )); do
    case "$1" in
        --source)    shift 2 ;;  # accepted-and-ignored for back-compat
        --kdir)      KDIR_ARG="${2:?--kdir needs arg}";  shift 2 ;;
        --defconfig) DEFCONFIG="${2:?--defconfig needs arg}"; shift 2 ;;
        -h|--help)   sed -n '1,28p' "$0"; exit 0 ;;
        *)           VERSION_ARG="$1"; shift ;;
    esac
done

# ── Resolve kernel tree ─────────────────────────────────────────────────
if [[ -n "$KDIR_ARG" ]]; then
    KDIR="$KDIR_ARG"
    [[ -f "$KDIR/Makefile" ]] || err "$KDIR is not a kernel source tree (no Makefile)"
    KVER=$(awk '/^VERSION/{v=$3} /^PATCHLEVEL/{p=$3} /^SUBLEVEL/{s=$3} END{print v"."p"."s}' "$KDIR/Makefile")
else
    if [[ -n "$VERSION_ARG" || ! -f "$WORK_DIR/.kernel-version" ]]; then
        "$SCRIPTS_DIR/fetch-kernel.sh" $VERSION_ARG
    fi
    KVER=$(cat "$WORK_DIR/.kernel-version")
    KDIR="$WORK_DIR/linux-$KVER"
    [[ -d "$KDIR" ]] || err "kernel source missing: $KDIR"
fi

# ── Resolve artefact source (release/ is the only source of truth) ──────
SOURCE="release"
PATCH_ROOT="$REPO_ROOT/release/patches"
CFG_FRAG="$REPO_ROOT/release/ask.config"

[[ -d "$PATCH_ROOT" ]]           || err "patch dir missing: $PATCH_ROOT"
[[ -f "$CFG_FRAG" ]]             || err "config fragment missing: $CFG_FRAG"
# SDK sources live under kernel/sdk-sources/ (kept there to avoid churn on
# 266 unchanged files). Every other *.patch lives under vyos/, ask/, or fixes/.
SDK_DIR="$PATCH_ROOT/kernel/sdk-sources"
[[ -d "$SDK_DIR" ]] || err "SDK source dir missing: $SDK_DIR"
# Apply order: vyos/ first (so ASK hooks stack on top of VyOS deltas),
# then ask/ (numeric prefix 010..080 = ASK-mono buckets), then fixes/
# (090+ = lockdep/concurrency repairs that touch ASK-introduced files).
# Within each subdir, sort by filename.
mapfile -t PATCH_FILES < <(
    for sub in vyos ask fixes; do
        [[ -d "$PATCH_ROOT/$sub" ]] || continue
        find "$PATCH_ROOT/$sub" -maxdepth 1 -type f -name '*.patch' | sort
    done
)
(( ${#PATCH_FILES[@]} > 0 )) || err "no *.patch files found under $PATCH_ROOT/{vyos,ask,fixes}/"

info "applying ASK artefacts to kernel tree"
dim  "   kernel:  linux-$KVER ($KDIR)"
dim  "   source:  release/ ($PATCH_ROOT)"

# ── Idempotence guard ───────────────────────────────────────────────────
# Record a marker so we don't re-apply onto an already-ASK tree (the hooks
# patch is not idempotent — re-applying produces rejects).
MARKER="$KDIR/.ask-applied"
if [[ -f "$MARKER" ]]; then
    warn "tree is already ASK-applied (found $MARKER); refusing to re-apply"
    dim  "   remove the marker and re-extract the kernel tree to retry"
    exit 1
fi

# ── Step 1: copy SDK sources ────────────────────────────────────────────
info "step 1/4: copying SDK sources"
SDK_COUNT=0
while IFS= read -r f; do
    dst="$KDIR/$f"
    mkdir -p "$(dirname "$dst")"
    cp "$SDK_DIR/$f" "$dst"
    SDK_COUNT=$((SDK_COUNT+1))
done < <(cd "$SDK_DIR" && find . -type f | sed 's|^\./||')
ok "copied $SDK_COUNT SDK file(s)"

# ── Step 2: apply all patches in order ──────────────────────────────────
# Use `git apply` instead of `patch`. Advantages:
#   - zero fuzz by default (refuses to guess if context drifts)
#   - uniform behaviour with patch-health.sh (`git apply --check` dry-run)
#   - better error messages (names failing file + hunk, not cryptic
#     ".rej written to <path>" scatter)
# If a hunk fails, we fall back to `git apply --reject` which writes
# conflict markers into .rej files just like the old `patch` path did —
# so the maintainer-workflow on failure is unchanged.
info "step 2/4: applying ${#PATCH_FILES[@]} patch(es) in order"
for P in "${PATCH_FILES[@]}"; do
    PNAME=$(basename "$P")
    dim "   → $PNAME"
    # --unsafe-paths: $KDIR is an absolute path and we are intentionally applying
    # outside any git worktree, which is what the flag unlocks.
    if ! git apply -p1 --unsafe-paths --directory="$KDIR" "$P" 2>&1; then
        warn "strict apply failed for $PNAME — retrying with --reject to surface failing hunks"
        git apply -p1 --unsafe-paths --directory="$KDIR" --reject "$P" || true
        err "$PNAME failed to apply — see *.rej files under $KDIR"
    fi
done
ok "all patches applied"

# ── Step 3: assemble config via merge_config.sh chain ───────────────────
# Goal: produce a .config that is a strict superset of VyOS's kernel config
# (so every VyOS-visible sysctl/module is present), with our LS1046A/DPAA
# delta layered on top as the *last* fragment so it wins on conflicts.
#
# Order (later files override earlier values):
#   1. release/vyos-base/arm64/vyos_defconfig   — VyOS arm64 base
#   2. release/vyos-base/*.config               — VyOS feature snippets
#   3. release/ask.config                       — LS1046A/DPAA delta (authoritative)
#
# If release/vyos-base/ is absent (e.g. building an old tree without VyOS
# alignment), fall back to the legacy $DEFCONFIG + ask.config path.
VYOS_BASE="$REPO_ROOT/release/vyos-base"
MERGE_CONFIG="$KDIR/scripts/kconfig/merge_config.sh"

info "step 3/4: assembling config"
if [[ -d "$VYOS_BASE" && -x "$MERGE_CONFIG" && -f "$VYOS_BASE/arm64/vyos_defconfig" ]]; then
    dim "   source: vyos-base + ask.config (merge_config.sh chain)"
    VYOS_SNIPPETS=( "$VYOS_BASE"/*.config )
    (
        cd "$KDIR"
        # merge_config.sh -m in-place merges fragments into $KCONFIG_CONFIG
        # (defaults to .config in cwd). -r prints conflicting-value warnings
        # (informational — last fragment wins).
        ARCH=arm64 "$MERGE_CONFIG" -m -r \
            "$VYOS_BASE/arm64/vyos_defconfig" \
            "${VYOS_SNIPPETS[@]}" \
            "$CFG_FRAG" 2>&1 | tail -20
    ) || err "merge_config.sh chain failed"
    ok "   merged vyos_defconfig + ${#VYOS_SNIPPETS[@]} snippet(s) + ask.config"
else
    warn "vyos-base/ not found — falling back to legacy $DEFCONFIG + ask.config path"
    if [[ ! -f "$KDIR/.config" ]]; then
        dim "   running: make ARCH=arm64 $DEFCONFIG"
        (cd "$KDIR" && make ARCH=arm64 "$DEFCONFIG" 2>&1 | tail -5) \
            || err "make $DEFCONFIG failed"
        ok "   seeded .config from $DEFCONFIG"
    fi
    echo ""                     >> "$KDIR/.config"
    echo "# ── ASK fragment ──" >> "$KDIR/.config"
    cat "$CFG_FRAG"             >> "$KDIR/.config"
fi

# Disable conflicting mainline DPAA ETH (the merge_config.sh above may have
# re-enabled it via vyos_defconfig; ask.config's "# is not set" override is
# applied last but only takes effect after olddefconfig resolves deps).
if grep -q '^CONFIG_FSL_DPAA_ETH=y' "$KDIR/.config" 2>/dev/null; then
    sed -i 's/^CONFIG_FSL_DPAA_ETH=y/# CONFIG_FSL_DPAA_ETH is not set/' "$KDIR/.config"
    dim "   disabled conflicting CONFIG_FSL_DPAA_ETH"
fi

# ── Step 4: olddefconfig to resolve new symbols ─────────────────────────
info "step 4/4: resolving config (make ARCH=arm64 olddefconfig)"
# Surface olddefconfig output — this is where you'd see "symbol foo is
# obsolete" or "new symbol bar, set to N" lines that reveal ask.config drift.
(cd "$KDIR" && make ARCH=arm64 olddefconfig 2>&1) \
    || err "make olddefconfig failed"

# ── Stamp the tree ──────────────────────────────────────────────────────
{
    echo "source=$SOURCE"
    echo "kernel=$KVER"
    echo "applied_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [[ -f "$REPO_ROOT/release/manifest.json" ]]; then
        echo "ask_iteration=$(jq -r '.ask_iteration // ""' "$REPO_ROOT/release/manifest.json")"
    fi
} > "$MARKER"

ok "tree is ASK-ready: $KDIR"
echo
info "next: ./scripts/build-kernel.sh"
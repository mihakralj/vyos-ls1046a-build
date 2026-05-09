#!/usr/bin/env bash
# diff-vyos-config.sh — compare the current ASK kernel config against a
# merged VyOS config for the same kernel version, category by category.
#
# Intent:
#   Produce a human-reviewable report showing every CONFIG_* symbol whose
#   value differs between:
#
#     VyOS merged config  =  merge_config.sh \
#                              release/vyos-base/arm64/vyos_defconfig \
#                              release/vyos-base/*.config \
#                            + make ARCH=arm64 olddefconfig
#
#     ASK merged config   =  arch/arm64/configs/defconfig \
#                            + release/ask.config
#                            + make ARCH=arm64 olddefconfig
#
#   Each differing symbol is classified so the maintainer can decide per
#   category whether ASK should adopt the VyOS value.
#
# Output format (one line per symbol, tab-separated):
#
#     CONFIG_FOO    vyos=y     ask=n     category=netfilter
#
# The report is grouped by category and written to work/vyos-config-diff.txt.
#
# Usage:
#   ./scripts/diff-vyos-config.sh                      # uses work/.kernel-version
#   ./scripts/diff-vyos-config.sh 6.6.137              # fetch/pin kernel first
#
# Exit codes:
#   0  report generated (zero or non-zero deltas; the report tells you)
#   1  kernel tree missing, merge_config.sh not available, etc.

set -euo pipefail
source "$(dirname "$0")/common.sh"

need make awk sort comm

VERSION_ARG=""
while (( $# )); do
    case "$1" in
        -h|--help) sed -n '1,35p' "$0"; exit 0 ;;
        *) VERSION_ARG="$1"; shift ;;
    esac
done

# ── Resolve kernel tree (read-only) ─────────────────────────────────────
if [[ -n "$VERSION_ARG" || ! -f "$WORK_DIR/.kernel-version" ]]; then
    "$SCRIPTS_DIR/fetch-kernel.sh" $VERSION_ARG
fi
KVER=$(cat "$WORK_DIR/.kernel-version")
KDIR="$WORK_DIR/linux-$KVER"
[[ -d "$KDIR" ]] || err "kernel source missing: $KDIR"

MERGE_CONFIG="$KDIR/scripts/kconfig/merge_config.sh"
[[ -x "$MERGE_CONFIG" ]] || err "merge_config.sh not found in kernel tree: $MERGE_CONFIG"

VYOS_BASE="$REPO_ROOT/release/vyos-base"
[[ -d "$VYOS_BASE" ]] || err "vyos-base not vendored — run scripts/sync-vyos-base.sh first"

ASK_FRAG="$REPO_ROOT/release/ask.config"
[[ -f "$ASK_FRAG" ]] || err "release/ask.config missing"

OUT="$WORK_DIR/vyos-config-diff.txt"
SANDBOX="$WORK_DIR/config-diff-sandbox"
rm -rf "$SANDBOX"
mkdir -p "$SANDBOX"

info "generating VyOS + ASK merged configs"
dim  "   kernel:    linux-$KVER"
dim  "   vyos-base: $VYOS_BASE"
dim  "   ask frag:  $ASK_FRAG"

# Start from a pristine kernel tree for deterministic kconfig resolution.
# The tree may carry stale artefacts from a previous patch-health or build
# run; mrproper strips them without touching sources.
(cd "$KDIR" && make ARCH=arm64 mrproper >/dev/null 2>&1) \
    || err "make mrproper failed in $KDIR"

# ── Helper: normalize a .config into "CONFIG_FOO=val" lines, sorted ─────
normalize_config() {
    local src="$1"
    # Drop blanks/comments but keep "# CONFIG_FOO is not set" lines and
    # rewrite them as "CONFIG_FOO=n" for easy diffing.
    awk '
        /^# CONFIG_[A-Za-z0-9_]+ is not set$/ {
            sym = $2
            print sym "=n"
            next
        }
        /^CONFIG_[A-Za-z0-9_]+=/ { print; next }
    ' "$src" | sort -u
}

# ── Build VyOS-flavoured merged config ──────────────────────────────────
# Both branches are built in-tree with KCONFIG_CONFIG overriding where
# .config is written. mrproper is run before each branch to strip the
# Kconfig scratch files that the previous branch left behind.
info "merging vyos_defconfig + config/*.config via kernel's merge_config.sh"
VYOS_OUT="$SANDBOX/vyos.config"
(
    cd "$KDIR"
    # merge_config.sh -m writes $KCONFIG_CONFIG.
    ARCH=arm64 KCONFIG_CONFIG="$VYOS_OUT" \
        "$MERGE_CONFIG" -m \
        "$VYOS_BASE/arm64/vyos_defconfig" \
        "$VYOS_BASE"/*.config 2>&1 | tail -5 \
        || err "merge_config.sh failed for VyOS set"
    make ARCH=arm64 KCONFIG_CONFIG="$VYOS_OUT" olddefconfig 2>&1 | tail -5 \
        || err "olddefconfig failed for VyOS set"
    # Clean kconfig scratch so the next branch starts fresh.
    make ARCH=arm64 mrproper >/dev/null 2>&1 || true
)
[[ -s "$VYOS_OUT" ]] || err "VyOS merged config is empty: $VYOS_OUT"
dim "   vyos merged config: $(wc -l < "$VYOS_OUT") lines"

# ── Build ASK-flavoured merged config ───────────────────────────────────
info "seeding arm64 defconfig + appending release/ask.config"
ASK_OUT="$SANDBOX/ask.config"
(
    cd "$KDIR"
    make ARCH=arm64 KCONFIG_CONFIG="$ASK_OUT" defconfig 2>&1 | tail -5 \
        || err "make defconfig failed (ASK base)"
    if grep -q '^CONFIG_FSL_DPAA_ETH=y' "$ASK_OUT" 2>/dev/null; then
        sed -i 's/^CONFIG_FSL_DPAA_ETH=y/# CONFIG_FSL_DPAA_ETH is not set/' "$ASK_OUT"
    fi
    {
        echo ""
        echo "# ── ASK fragment ──"
        cat "$ASK_FRAG"
    } >> "$ASK_OUT"
    make ARCH=arm64 KCONFIG_CONFIG="$ASK_OUT" olddefconfig 2>&1 | tail -5 \
        || err "olddefconfig failed for ASK set"
    make ARCH=arm64 mrproper >/dev/null 2>&1 || true
)
[[ -s "$ASK_OUT" ]] || err "ASK merged config is empty: $ASK_OUT"
dim "   ask merged config:  $(wc -l < "$ASK_OUT") lines"

# ── Normalize + diff ────────────────────────────────────────────────────
V_NORM="$SANDBOX/vyos.norm"
A_NORM="$SANDBOX/ask.norm"
normalize_config "$VYOS_OUT" > "$V_NORM"
normalize_config "$ASK_OUT"  > "$A_NORM"

# Build a map: symbol -> vyos_val, ask_val
#   present in both but differ   → true delta
#   present in only one          → "absent" on the other side
awk -F= '
    NR==FNR { v[$1] = $2; next }
    { a[$1] = $2 }
    END {
        for (k in v) syms[k] = 1
        for (k in a) syms[k] = 1
        for (k in syms) {
            vv = (k in v) ? v[k] : "absent"
            aa = (k in a) ? a[k] : "absent"
            if (vv != aa) print k "\t" vv "\t" aa
        }
    }
' "$V_NORM" "$A_NORM" | sort > "$SANDBOX/raw-diff.tsv"

# ── Categorize ──────────────────────────────────────────────────────────
categorize() {
    local sym="$1"
    case "$sym" in
        CONFIG_FSL_*|CONFIG_DPAA*|CONFIG_QBMAN*|CONFIG_FMAN*) echo "dpaa-fman" ;;
        CONFIG_NETFILTER*|CONFIG_NF_*|CONFIG_IP_NF_*|CONFIG_IP6_NF_*|CONFIG_NFT_*|CONFIG_XT_*) echo "netfilter" ;;
        CONFIG_OVERLAY_FS|CONFIG_SQUASHFS*|CONFIG_FUSE*|CONFIG_FS_*|CONFIG_EXT4*|CONFIG_BTRFS*|CONFIG_XFS*) echo "filesystems" ;;
        CONFIG_CRYPTO*|CONFIG_XFRM*) echo "crypto-ipsec" ;;
        CONFIG_NET_SCH_*|CONFIG_NET_CLS_*|CONFIG_NET_ACT_*) echo "traffic-control" ;;
        CONFIG_BRIDGE*|CONFIG_VLAN*|CONFIG_VXLAN*|CONFIG_GENEVE*|CONFIG_BATMAN*) echo "l2-encap" ;;
        CONFIG_IP_*|CONFIG_IPV6*|CONFIG_INET*|CONFIG_TCP_*|CONFIG_UDP_*) echo "l3-l4" ;;
        CONFIG_WIREGUARD|CONFIG_OPENVPN|CONFIG_L2TP*|CONFIG_PPTP|CONFIG_PPP*) echo "vpn" ;;
        CONFIG_USB*|CONFIG_MMC*|CONFIG_SCSI*|CONFIG_SATA*|CONFIG_ATA*) echo "storage-usb" ;;
        CONFIG_HWMON*|CONFIG_SENSORS_*|CONFIG_SFP|CONFIG_PHYLINK|CONFIG_PHY_*) echo "hwmon-phy" ;;
        CONFIG_SND_*|CONFIG_SOUND|CONFIG_DRM*|CONFIG_FB_*|CONFIG_VIDEO_*) echo "media-gfx" ;;
        CONFIG_MODULE_SIG*|CONFIG_MODVERSIONS|CONFIG_MODULE_*) echo "module-system" ;;
        CONFIG_DEBUG_*|CONFIG_FTRACE*|CONFIG_KASAN*|CONFIG_KGDB*) echo "debug" ;;
        *) echo "other" ;;
    esac
}

# ── Emit grouped report ─────────────────────────────────────────────────
{
    echo "=== VyOS ↔ ASK kernel config diff ==="
    echo "Kernel:     linux-$KVER"
    echo "VyOS base:  $VYOS_BASE (vyos_defconfig + $(ls "$VYOS_BASE"/*.config | wc -l) snippets)"
    echo "ASK frag:   $ASK_FRAG"
    echo "Generated:  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo
    echo "Columns: <symbol> <vyos-value> <ask-value>"
    echo "Values:  y | m | n | \"<string>\" | <number> | absent"
    echo
    TOTAL=$(wc -l < "$SANDBOX/raw-diff.tsv")
    echo "Total differing symbols: $TOTAL"
    echo
    # Group
    while IFS=$'\t' read -r sym vv aa; do
        cat=$(categorize "$sym")
        printf '%s\t%s\t%s\t%s\n' "$cat" "$sym" "$vv" "$aa"
    done < "$SANDBOX/raw-diff.tsv" | sort | {
        current=""
        while IFS=$'\t' read -r cat sym vv aa; do
            if [[ "$cat" != "$current" ]]; then
                echo
                echo "── $cat ──"
                current="$cat"
            fi
            printf '  %-48s vyos=%-8s ask=%s\n' "$sym" "$vv" "$aa"
        done
    }
} | tee "$OUT"

echo
ok "report written to: $OUT"
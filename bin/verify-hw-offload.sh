#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
#
# verify-hw-offload.sh — ASK2 PR14u (M2.5u) HW_OFFLOAD verification.
#
# Companion to bin/verify-ask-flow-offload.sh — that harness measures
# end-to-end throughput vs CPU; this harness inspects the silicon
# state to confirm flows are actually being offloaded into the FMan
# PCD chain rather than processed in software.
#
# Specifically answers four questions:
#
#   1. Did the OH-port pool come up?      (dmesg "PR14s OH-port pool
#                                          ready (claimed N/6 ports)")
#   2. How many flows did ask.ko insert
#      into silicon vs SW-fallback?       (dmesg counts of
#                                          "hw_insert OK" vs
#                                          "hw_insert=-EOPNOTSUPP / -ENODEV
#                                          / -ENOSPC / -EAGAIN (SW-fallback)")
#   3. Are conntrack entries marked
#      HW_OFFLOAD?                         (/proc/net/nf_conntrack)
#   4. Are there silent failure signals?  (cc_node_add_key failures,
#                                          unknown-cookie removes,
#                                          oh_port_set_chain failures)
#
# Run on the DUT (or remotely via ssh + cat) — it only inspects
# /proc, /sys, dmesg.  Zero side effects, safe to run during a live
# M2 iperf3 sweep.
#
# Inputs (env-overridable):
#   DUT             — SSH alias for the device under test       [vyos]
#   DMESG_LINES     — recent dmesg lines to scan                [4000]
#   CONNTRACK_MAX   — max conntrack rows to dump for sample      [20]
#
# Exit code:
#   0  HW offload is functioning (≥1 flow OK + OH pool ≥1 + no fatal log)
#   1  HW offload is degraded   (warnings present, see report)
#   2  HW offload is broken     (zero HW-offloaded flows, or pool ==0)

set -u

DUT="${DUT:-vyos}"
DMESG_LINES="${DMESG_LINES:-4000}"
CONNTRACK_MAX="${CONNTRACK_MAX:-20}"
LOCAL_RUN="${LOCAL_RUN:-0}"   # set to 1 if running directly on DUT

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

run() {
        if [[ "$LOCAL_RUN" == "1" ]]; then
                bash -c "$*"
        else
                ssh -o BatchMode=yes "$DUT" -- "$@"
        fi
}

hdr() {
        printf '\n=== %s ===\n' "$*"
}

# ----------------------------------------------------------------------------
# 1. ask.ko load + OH-port pool status
# ----------------------------------------------------------------------------

hdr "1. ask.ko / FMan PCD bring-up"

ASK_LOADED=$(run "lsmod | grep -c '^ask '" || echo 0)
echo "ask.ko loaded: $ASK_LOADED"
if [[ "$ASK_LOADED" == "0" ]]; then
        echo "FATAL: ask.ko is not loaded"
        exit 2
fi

PCD_UP=$(run "dmesg | grep -c 'FMan PCD chain up'" || echo 0)
echo "FMan PCD chain up events: $PCD_UP"

OH_POOL_LINE=$(run "dmesg | grep -E 'PR14[sj] OH-port (pool|chain) ready' | tail -1" || true)
echo "OH-port bring-up: ${OH_POOL_LINE:-<none>}"

OH_POOL_COUNT=$(echo "$OH_POOL_LINE" | grep -oE 'claimed [0-9]+' | awk '{print $2}')
OH_POOL_COUNT="${OH_POOL_COUNT:-0}"
echo "OH-port pool count: $OH_POOL_COUNT"

# ----------------------------------------------------------------------------
# 2. Flow insert / remove accounting from dmesg
# ----------------------------------------------------------------------------

hdr "2. Flow insert/remove accounting (last $DMESG_LINES dmesg lines)"

DMESG=$(run "dmesg | tail -n $DMESG_LINES" || true)

count() { echo "$DMESG" | grep -cE "$1" || true; }

HW_OK=$(count 'ask: flow: hw_insert OK')
SW_NODEV=$(count 'hw_insert=-19 \(SW-fallback\)')        # -ENODEV
SW_NOSPC=$(count 'hw_insert=-28 \(SW-fallback\)')        # -ENOSPC (PR14s pool empty)
SW_NOSUP=$(count 'hw_insert=-95 \(SW-fallback\)')        # -EOPNOTSUPP
SW_AGAIN=$(count 'hw_insert=-EAGAIN')                    # neigh unresolved
CC_FAIL=$(count 'cc_node_add_key v4-TCP failed')
CHAIN_FAIL=$(count 'oh_port_set_chain.*failed')
UNK_COOKIE=$(count 'remove: unknown cookie')
PR14T_ABSORBED=$(count 'remove: unknown cookie 0x[0-9a-f]+ \(already freed\?\)')

printf '  HW inserts (silicon armed):    %s\n' "$HW_OK"
printf '  SW fallback (-ENODEV):         %s\n' "$SW_NODEV"
printf '  SW fallback (-ENOSPC pool):    %s\n' "$SW_NOSPC"
printf '  SW fallback (-EOPNOTSUPP):     %s\n' "$SW_NOSUP"
printf '  Neigh defer  (-EAGAIN):        %s\n' "$SW_AGAIN"
printf '  CC add_key failures:           %s\n' "$CC_FAIL"
printf '  OH set_chain failures:         %s\n' "$CHAIN_FAIL"
printf '  Unknown-cookie removes:        %s\n' "$UNK_COOKIE"
printf '    of which PR14t-absorbed:     %s\n' "$PR14T_ABSORBED"

# ----------------------------------------------------------------------------
# 3. Conntrack HW_OFFLOAD inspection
# ----------------------------------------------------------------------------

hdr "3. /proc/net/nf_conntrack HW_OFFLOAD markers"

CT=$(run "cat /proc/net/nf_conntrack 2>/dev/null" || true)
CT_TOTAL=$(echo "$CT" | grep -c . || echo 0)
CT_OFFLOAD=$(echo "$CT" | grep -c HW_OFFLOAD || echo 0)
CT_OFFLOAD_SW=$(echo "$CT" | grep -c '\[OFFLOAD\]' || echo 0)

printf '  conntrack rows total:          %s\n' "$CT_TOTAL"
printf '  rows with HW_OFFLOAD:          %s\n' "$CT_OFFLOAD"
printf '  rows with [OFFLOAD] (SW only): %s\n' "$CT_OFFLOAD_SW"

if [[ "$CT_OFFLOAD" -gt 0 ]]; then
        echo
        echo "  sample HW_OFFLOAD rows (up to $CONNTRACK_MAX):"
        echo "$CT" | grep HW_OFFLOAD | head -n "$CONNTRACK_MAX" | sed 's/^/    /'
fi

# ----------------------------------------------------------------------------
# 4. Optional: ask debugfs (only present if CONFIG_DEBUG_FS=y + ask_debugfs)
# ----------------------------------------------------------------------------

hdr "4. /sys/kernel/debug/ask (if present)"

DEBUGFS=$(run "ls /sys/kernel/debug/ask 2>/dev/null" || true)
if [[ -z "$DEBUGFS" ]]; then
        echo "  (not present — debugfs disabled or ask_debugfs not wired)"
else
        echo "$DEBUGFS"
        for f in $DEBUGFS; do
                printf '\n  --- %s ---\n' "$f"
                run "cat /sys/kernel/debug/ask/$f 2>/dev/null | head -40" || true
        done
fi

# ----------------------------------------------------------------------------
# 5. Verdict
# ----------------------------------------------------------------------------

hdr "5. Verdict"

RC=0
REASONS=()

if [[ "$OH_POOL_COUNT" == "0" ]]; then
        RC=2
        REASONS+=("OH-port pool empty — no HW offload possible (-ENOSPC for every insert)")
fi

if [[ "$HW_OK" == "0" ]]; then
        if [[ "$RC" -lt 2 ]]; then RC=2; fi
        REASONS+=("zero successful HW inserts in last $DMESG_LINES dmesg lines")
fi

if [[ "$CC_FAIL" -gt 0 || "$CHAIN_FAIL" -gt 0 ]]; then
        if [[ "$RC" -lt 1 ]]; then RC=1; fi
        REASONS+=("silicon errors logged: $CC_FAIL cc_add_key, $CHAIN_FAIL set_chain")
fi

# PR14t: unknown-cookie removes are info-only; only flag if they're NOT
# the absorbed (already-freed) variant.
UNK_LEFTOVER=$((UNK_COOKIE - PR14T_ABSORBED))
if [[ "$UNK_LEFTOVER" -gt 0 ]]; then
        if [[ "$RC" -lt 1 ]]; then RC=1; fi
        REASONS+=("$UNK_LEFTOVER non-PR14t unknown-cookie removes")
fi

if [[ "$RC" == "0" ]]; then
        echo "HW offload: HEALTHY"
        echo "  OH pool: $OH_POOL_COUNT/6"
        echo "  HW inserts: $HW_OK"
        echo "  conntrack HW_OFFLOAD rows: $CT_OFFLOAD"
elif [[ "$RC" == "1" ]]; then
        echo "HW offload: DEGRADED"
        for r in "${REASONS[@]}"; do echo "  - $r"; done
else
        echo "HW offload: BROKEN"
        for r in "${REASONS[@]}"; do echo "  - $r"; done
fi

exit "$RC"
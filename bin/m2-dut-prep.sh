#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
#
# m2-dut-prep.sh — idempotent DUT prerequisites for the ASK2 M2 gate.
#
# The PR14g/M2 acceptance gate (bin/verify-ask-flow-offload.sh) measures
# performance but does NOT bring up the DUT-side prerequisites. This
# script bakes the three out-of-band steps that the operator otherwise
# has to do by hand on every fresh boot:
#
#   1. Delete the VyOS-generated `notrack` rule on the FORWARD path
#      (handle is dynamic — looked up by content match). Without this,
#      conntrack offload never engages because nft sees the flow as
#      untracked before the flowtable can claim it.
#
#   2. Enable hw-tc-offload on both ingress/egress netdevs. Required
#      for the kernel to deliver FLOW_BLOCK_BIND down through
#      ndo_setup_tc to dpaa_eth, which is the path ask.ko hooks.
#
#   3. Install an `inet ask_offload` table containing:
#        - flowtable ft1 hook ingress priority 0
#          devices = {iface_in, iface_out}
#          flags offload
#        - chain forward type filter hook forward priority -200 policy accept
#        - rule: ip protocol {tcp, udp} flow add @ft1
#
#      This is the netfilter-flowtable bind that ask.ko's indr_dev
#      callback consumes (PR14n) to install HW flow entries via the
#      FMan PCD chain.
#
# Idempotency: every step checks current state and re-applies only if
# needed. Safe to run repeatedly. Safe to run after a reboot. Safe to
# run after a fresh `add system image` install.
#
# Inputs (env-overridable):
#   DUT          — SSH alias for the device under test  [vyos]
#   IFACE_IN     — DUT ingress port                     [eth3]
#   IFACE_OUT    — DUT egress  port                     [eth4]
#
# Exit status:
#   0 — all prereqs in place
#   1 — one or more steps failed
#   2 — DUT unreachable

set -euo pipefail

DUT="${DUT:-vyos}"
IFACE_IN="${IFACE_IN:-eth3}"
IFACE_OUT="${IFACE_OUT:-eth4}"

log()  { printf '[m2-dut-prep] %s\n' "$*" >&2; }
fail() { log "FAIL: $*"; exit 1; }

ssh_dut() { ssh -o BatchMode=yes "$DUT" -- "$@"; }

log "DUT=$DUT iface_in=$IFACE_IN iface_out=$IFACE_OUT"

# 0. Reachability
ssh_dut "true" 2>/dev/null || { log "DUT $DUT unreachable"; exit 2; }

# 1. Delete VyOS notrack rule on FORWARD (handle is dynamic — find by content).
log "step 1/3: remove VyOS notrack rule from vyos_conntrack PREROUTING (if present)"
NOTRACK_HANDLE=$(ssh_dut "sudo -n nft -a list chain ip vyos_conntrack PREROUTING 2>/dev/null | awk '/notrack/ {for(i=1;i<=NF;i++) if(\$i==\"handle\") {print \$(i+1); exit}}'" || true)
if [ -n "${NOTRACK_HANDLE:-}" ]; then
        ssh_dut "sudo -n nft delete rule ip vyos_conntrack PREROUTING handle $NOTRACK_HANDLE" \
                || fail "could not delete notrack rule handle $NOTRACK_HANDLE"
        log "       deleted notrack rule (handle $NOTRACK_HANDLE)"
else
        log "       no notrack rule present (already removed or VyOS conntrack chain absent)"
fi

# 2. hw-tc-offload on both ports
log "step 2/3: ensure hw-tc-offload is on for $IFACE_IN and $IFACE_OUT"
for iface in "$IFACE_IN" "$IFACE_OUT"; do
        state=$(ssh_dut "sudo -n ethtool -k $iface 2>/dev/null | awk -F': ' '/hw-tc-offload/ {print \$2; exit}'" || true)
        case "$state" in
                on*) log "       $iface: already on" ;;
                off*)
                        ssh_dut "sudo -n ethtool -K $iface hw-tc-offload on" \
                                || fail "ethtool -K $iface hw-tc-offload on failed"
                        log "       $iface: enabled"
                        ;;
                *)
                        log "WARN: $iface: hw-tc-offload state unknown ('$state') — interface may not exist"
                        ;;
        esac
done

# 3. ask_offload table + flowtable + forward chain
log "step 3/3: install inet ask_offload table with flowtable ft1 + forward chain"
if ssh_dut "sudo -n nft list table inet ask_offload" >/dev/null 2>&1; then
        log "       table inet ask_offload already exists — leaving it in place"
        log "       (run: sudo nft delete table inet ask_offload  to force re-create)"
else
        ssh_dut "sudo -n nft -f - <<'EOF'
table inet ask_offload {
        flowtable ft1 {
                hook ingress priority 0
                devices = { $IFACE_IN, $IFACE_OUT }
                flags offload
        }
        chain forward {
                type filter hook forward priority -200; policy accept;
                ip protocol { tcp, udp } flow add @ft1
        }
}
EOF" || fail "could not install ask_offload table"
        log "       installed ask_offload table"
fi

# Sanity: confirm flowtable lists both devices with offload flag
LIST=$(ssh_dut "sudo -n nft list flowtable inet ask_offload ft1" 2>/dev/null || true)
if ! grep -q "$IFACE_IN"  <<<"$LIST"; then fail "flowtable ft1 missing $IFACE_IN";  fi
if ! grep -q "$IFACE_OUT" <<<"$LIST"; then fail "flowtable ft1 missing $IFACE_OUT"; fi
if ! grep -q "offload"    <<<"$LIST"; then fail "flowtable ft1 missing offload flag"; fi

log "all prereqs in place — run bin/verify-ask-flow-offload.sh to measure"
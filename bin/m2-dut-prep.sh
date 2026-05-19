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
#   SINK_IP      — M2 sink IP (lxc200)                  [192.168.1.137]
#
# Exit status:
#   0 — all prereqs in place
#   1 — one or more steps failed
#   2 — DUT unreachable

set -euo pipefail

DUT="${DUT:-vyos}"
IFACE_IN="${IFACE_IN:-eth3}"
IFACE_OUT="${IFACE_OUT:-eth4}"
SINK_IP="${SINK_IP:-192.168.1.137}"

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

# 4. Pin SINK_IP/32 to IFACE_OUT (override /16 ECMP that picks eth1 1G over eth4 10G).
#
# Without this, the kernel route lookup for ${SINK_IP} on a DUT with multiple
# interfaces in 192.168.1.0/16 spreads via ECMP and frequently chooses the
# 1G RJ45 port (eth1) over the 10G SFP+ port (eth4). The flow then tops out
# at ~0.94 Gbps regardless of how well the silicon classifier is working,
# because the SLOW path is single-1G-capped. Installing a /32 host route
# forces the egress to IFACE_OUT and lets the M2 gate actually measure
# silicon-offload throughput.
log "step 4/4: pin $SINK_IP/32 to dev $IFACE_OUT (override /16 ECMP)"
IFACE_OUT_SRC=$(ssh_dut "ip -4 -o addr show dev $IFACE_OUT 2>/dev/null | awk '{print \$4}' | cut -d/ -f1 | head -n1" 2>/dev/null || true)
# Defensive fallback: if the awk \$4 substitution got mangled by quoting,
# re-extract from the raw `ip` output with a sed pipeline that has no \$.
if [ -z "${IFACE_OUT_SRC:-}" ]; then
        IFACE_OUT_SRC=$(ssh_dut "ip -4 -o addr show dev $IFACE_OUT" 2>/dev/null \
                | sed -nE 's@.*inet ([0-9.]+)/[0-9]+.*@\1@p' \
                | head -n1 || true)
fi
if [ -z "${IFACE_OUT_SRC:-}" ]; then
        log "WARN: could not read IPv4 src addr from $IFACE_OUT — skipping /32 pin"
else
        EXISTING=$(ssh_dut "ip -4 route show $SINK_IP/32 2>/dev/null" || true)
        WANT="$SINK_IP dev $IFACE_OUT src $IFACE_OUT_SRC"
        if grep -q "dev $IFACE_OUT" <<<"$EXISTING" && grep -q "src $IFACE_OUT_SRC" <<<"$EXISTING"; then
                log "       $SINK_IP/32 already pinned: $EXISTING"
        else
                if [ -n "$EXISTING" ]; then
                        ssh_dut "sudo -n ip route del $SINK_IP/32" \
                                || fail "could not delete stale $SINK_IP/32 route"
                fi
                ssh_dut "sudo -n ip route add $SINK_IP/32 dev $IFACE_OUT src $IFACE_OUT_SRC" \
                        || fail "could not add $SINK_IP/32 dev $IFACE_OUT src $IFACE_OUT_SRC"
                log "       added: $SINK_IP/32 dev $IFACE_OUT src $IFACE_OUT_SRC"
        fi
fi

log "all prereqs in place — run bin/verify-ask-flow-offload.sh to measure"

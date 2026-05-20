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
#   SINK_IP      — M2 sink IP (lxc202 on the 10.11.1.0/30 leg)  [10.11.1.2]
#
# Topology (rebuilt 2026-05-19 — pure L3, no NAT, no /32 pin):
#   lxc201 (10.99.1.2/30) -> eth3 (10.99.1.1/30) -> DUT -> eth4 (10.11.1.1/30) -> lxc202 (10.11.1.2/30)
#
# Exit status:
#   0 — all prereqs in place
#   1 — one or more steps failed
#   2 — DUT unreachable

set -euo pipefail

DUT="${DUT:-vyos}"
IFACE_IN="${IFACE_IN:-eth3}"
IFACE_OUT="${IFACE_OUT:-eth4}"
SINK_IP="${SINK_IP:-10.11.1.2}"

log()  { printf '[m2-dut-prep] %s\n' "$*" >&2; }
fail() { log "FAIL: $*"; exit 1; }

ssh_dut() { ssh -o BatchMode=yes "$DUT" -- "$@"; }

log "DUT=$DUT iface_in=$IFACE_IN iface_out=$IFACE_OUT"

# 0. Reachability
ssh_dut "true" 2>/dev/null || { log "DUT $DUT unreachable"; exit 2; }

# 1. Delete VyOS notrack rule on FORWARD (handle is dynamic — find by content).
#
# VyOS's vyos_conntrack PREROUTING chain ends with a `notrack` rule that short-
# circuits conntrack for any forwarded traffic that hasn't matched an earlier
# rule. Without removing it, the iperf3 data flows are never tracked by
# conntrack, never reach the ASSURED state, and the nftables flowtable
# refuses to offload them (`nf_flow_offload_tuple()` only acts on
# ASSURED-state conntrack entries).
log "step 1/4: remove VyOS notrack rule from vyos_conntrack PREROUTING (if present)"
NOTRACK_HANDLE=$(ssh_dut "sudo -n nft -a list chain ip vyos_conntrack PREROUTING 2>/dev/null | awk '/notrack/ {for(i=1;i<=NF;i++) if(\$i==\"handle\") {print \$(i+1); exit}}'" || true)
if [ -n "${NOTRACK_HANDLE:-}" ]; then
        ssh_dut "sudo -n nft delete rule ip vyos_conntrack PREROUTING handle $NOTRACK_HANDLE" \
                || fail "could not delete notrack rule handle $NOTRACK_HANDLE"
        log "       deleted notrack rule (handle $NOTRACK_HANDLE)"
else
        log "       no notrack rule present (already removed or VyOS conntrack chain absent)"
fi

# 2. hw-tc-offload on both ports
log "step 2/4: ensure hw-tc-offload is on for $IFACE_IN and $IFACE_OUT"
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

# 3. ask_offload table + flowtable + forward chain + conntrack-promoting rule.
#
# The forward chain references @ft1 (flowtable lookup) but the flowtable
# only sees ASSURED conntrack entries.  conntrack itself only confirms a
# flow when *some* rule in the netfilter pipeline touches `ct state`.
# We add a dedicated `conntrack_promote` chain at PREROUTING priority -150
# (BEFORE the VyOS `vyos_conntrack` chain at -200 used to short-circuit
# with `notrack`, after that's deleted in step 1) whose only job is to
# match iperf3 5201/tcp NEW packets and `accept` them — the `ct state new`
# match alone is sufficient to register the flow with conntrack so it
# walks NEW -> ESTABLISHED -> ASSURED and the flowtable then promotes it.
#
# Diagnosed 2026-05-20 (qdrant "M2-flowtable-conntrack-blocker"): without
# this rule, `conntrack -L` returned ZERO entries during active 7 Gbps
# forwarding, the flowtable never engaged, and the 4.5-6.3% softirq we
# measured pre-fix WAS the full unaccelerated kernel forward plane.
log "step 3/4: install inet ask_offload table with flowtable ft1 + forward + conntrack_promote"
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
        chain conntrack_promote {
                type filter hook prerouting priority -150; policy accept;
                ct state new tcp dport 5201 counter accept
                ct state new tcp sport 5201 counter accept
        }
}
EOF" || fail "could not install ask_offload table"
        log "       installed ask_offload table (incl. conntrack_promote chain)"
fi

# Defensive: if the table existed already from a previous run that pre-dates
# the conntrack_promote chain, install the chain via heredoc (the `nft -f -`
# pattern handles semicolons & braces cleanly, unlike escaped shell args).
# `add chain` with a pre-existing chain definition is idempotent in nft.
ssh_dut "sudo -n nft -f - <<'EOF'
add chain inet ask_offload conntrack_promote { type filter hook prerouting priority -150; policy accept; }
flush chain inet ask_offload conntrack_promote
add rule inet ask_offload conntrack_promote ct state new tcp dport 5201 counter accept
add rule inet ask_offload conntrack_promote ct state new tcp sport 5201 counter accept
EOF" || fail "could not arm conntrack_promote chain"
log "       conntrack_promote chain armed (ct state new tcp {dport,sport} 5201)"

# Sanity: confirm flowtable lists both devices with offload flag
LIST=$(ssh_dut "sudo -n nft list flowtable inet ask_offload ft1" 2>/dev/null || true)
if ! grep -q "$IFACE_IN"  <<<"$LIST"; then fail "flowtable ft1 missing $IFACE_IN";  fi
if ! grep -q "$IFACE_OUT" <<<"$LIST"; then fail "flowtable ft1 missing $IFACE_OUT"; fi
if ! grep -q "offload"    <<<"$LIST"; then fail "flowtable ft1 missing offload flag"; fi

# 4. Verify SINK_IP egress path matches IFACE_OUT.
#
# As of 2026-05-19 the M2 rig was rebuilt with dedicated /30 subnets:
#   eth3 = 10.99.1.1/30 -> lxc201 (10.99.1.2)
#   eth4 = 10.11.1.1/30 -> lxc202 (10.11.1.2)
# Each /30 is its own connected route, so a kernel route lookup for the
# sink IP unambiguously selects IFACE_OUT — no /32 host-route pin is
# needed. We just confirm the route resolves to the expected device, so
# a future re-cabling or DHCP regression is caught early.
log "step 4/4: verify $SINK_IP routes out $IFACE_OUT (connected /30, no ECMP)"
ROUTE_DEV=$(ssh_dut "ip -4 route get $SINK_IP 2>/dev/null | awk '/dev/ {for(i=1;i<=NF;i++) if(\$i==\"dev\") {print \$(i+1); exit}}'" || true)
if [ -z "${ROUTE_DEV:-}" ]; then
        fail "no route to $SINK_IP from DUT (check IFACE_OUT IP + cabling)"
elif [ "$ROUTE_DEV" != "$IFACE_OUT" ]; then
        fail "$SINK_IP routes via $ROUTE_DEV, expected $IFACE_OUT (check /30 subnet assignment)"
else
        log "       $SINK_IP -> dev $IFACE_OUT (connected route OK)"
fi

log "all prereqs in place — run bin/verify-ask-flow-offload.sh to measure"

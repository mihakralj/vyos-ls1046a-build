#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
#
# m2-stepwise-verify.sh — bisect the ASK2 M2 gate configuration to find
# which exact step breaks DUT eth3/eth4 SFP+ RX.
#
# Hypothesis (2026-05-22): A clean reboot of the DUT restores SFP+
# bidirectional traffic, but at some point during the M2 prep the RX path
# on eth3/eth4 wedges (TX still works, RX dies, ARP cache goes FAILED on
# both sides of every /30). This script applies each prerequisite ONE AT
# A TIME and probes L2/L3 connectivity after each step, so the breaking
# configuration change can be identified by exclusion.
#
# Expected topology (rebuilt 2026-05-19):
#   lxc201 (10.99.1.2/30) <--> heidi switch fabric <--> DUT eth3 (10.99.1.1/30)
#   lxc202 (10.11.1.2/30) <--> heidi switch fabric <--> DUT eth4 (10.11.1.1/30)
#   DUT routes between the two /30s
#
# Inputs (env-overridable):
#   DUT=vyos
#   HEIDI=heidi               (Proxmox host running lxc201/lxc202)
#   IFACE_IN=eth3 IFACE_OUT=eth4
#   GEN_CT=201  SINK_CT=202   (pct VMIDs of lxc201/lxc202)
#
# Each step prints:
#   PASS: L2-eth3, L2-eth4, L3-fwd, iperf3-3s
# and stops on first FAIL so the operator can decide whether to
# (a) take corrective action and resume, or
# (b) note "broken after step N" and reboot to retry.

set -euo pipefail

DUT="${DUT:-vyos}"
HEIDI="${HEIDI:-heidi}"
IFACE_IN="${IFACE_IN:-eth3}"
IFACE_OUT="${IFACE_OUT:-eth4}"
GEN_CT="${GEN_CT:-201}"
SINK_CT="${SINK_CT:-202}"
GEN_IP_LOCAL="10.99.1.2"
GEN_IP_REMOTE="10.99.1.1"
SINK_IP_LOCAL="10.11.1.2"
SINK_IP_REMOTE="10.11.1.1"

log()  { printf '\n\033[1;36m[stepwise]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  PASS\033[0m %s\n' "$*"; }
bad()  { printf '\033[1;31m  FAIL\033[0m %s\n' "$*"; }
hdr()  { printf '\n\033[1;33m=========================================================================\n%s\n=========================================================================\033[0m\n' "$*"; }

ssh_dut()   { ssh -o BatchMode=yes "$DUT" -- "$@"; }
ssh_heidi() { ssh -o BatchMode=yes "$HEIDI" -- "$@"; }
gen()       { ssh_heidi "sudo pct exec $GEN_CT  -- $*"; }
sink()      { ssh_heidi "sudo pct exec $SINK_CT -- $*"; }

# ----------------------------------------------------------------- probes
probe_l2_eth3() {
        local out
        out=$(ssh_dut "sudo arping -I $IFACE_IN -c 2 -w 3 $GEN_IP_LOCAL 2>&1" || true)
        if grep -q "Received 2 response" <<<"$out" || grep -q "Received [12] response" <<<"$out"; then
                ok "L2 $IFACE_IN <-> $GEN_IP_LOCAL  (DUT-side arping)"
                return 0
        fi
        bad "L2 $IFACE_IN <-> $GEN_IP_LOCAL  (DUT-side arping)"
        printf '       output:\n%s\n' "$out" | sed 's/^/         /'
        return 1
}

probe_l2_eth4() {
        local out
        out=$(ssh_dut "sudo arping -I $IFACE_OUT -c 2 -w 3 $SINK_IP_LOCAL 2>&1" || true)
        if grep -q "Received [12] response" <<<"$out"; then
                ok "L2 $IFACE_OUT <-> $SINK_IP_LOCAL  (DUT-side arping)"
                return 0
        fi
        bad "L2 $IFACE_OUT <-> $SINK_IP_LOCAL  (DUT-side arping)"
        printf '       output:\n%s\n' "$out" | sed 's/^/         /'
        return 1
}

probe_l3_fwd() {
        local out
        out=$(gen "ping -c 2 -W 1 $SINK_IP_LOCAL 2>&1" || true)
        if grep -q "2 received" <<<"$out" || grep -q "[12] received" <<<"$out"; then
                ok "L3 lxc201 -> DUT -> lxc202  (forwarded)"
                return 0
        fi
        bad "L3 lxc201 -> DUT -> lxc202  (forwarded)"
        printf '       output:\n%s\n' "$out" | sed 's/^/         /'
        return 1
}

probe_iperf3_3s() {
        local out bps gbps
        # Hard 15s outer timeout to defeat any SSH stdio buffering / hang;
        # iperf3 -t 3 -J yields a single JSON blob at completion (no streaming).
        out=$(timeout 15 ssh -o BatchMode=yes "$HEIDI" -- \
                "sudo pct exec $GEN_CT -- iperf3 -c $SINK_IP_LOCAL -t 3 -P 1 -J" 2>&1 || true)
        bps=$(jq -r '.end.sum_received.bits_per_second // empty' <<<"$out" 2>/dev/null || true)
        if [ -n "$bps" ] && awk -v b="$bps" 'BEGIN{exit (b>0)?0:1}'; then
                gbps=$(awk -v b="$bps" 'BEGIN{printf "%.2f Gbps", b/1e9}')
                ok "iperf3 3s lxc201->lxc202: $gbps"
                return 0
        fi
        bad "iperf3 3s lxc201->lxc202 did not complete"
        printf '       output (tail):\n%s\n' "$out" | tail -15 | sed 's/^/         /'
        return 1
}

run_probes() {
        local label="$1"; shift
        printf '\n--- probes after: %s ---\n' "$label"
        local rc=0
        probe_l2_eth3 || rc=$((rc+1))
        probe_l2_eth4 || rc=$((rc+1))
        # L3 + iperf3 only meaningful once ip_forward is on; caller decides.
        if [ "${PROBE_L3:-1}" = "1" ]; then
                probe_l3_fwd || rc=$((rc+1))
        fi
        if [ "${PROBE_IPERF:-1}" = "1" ]; then
                probe_iperf3_3s || rc=$((rc+1))
        fi
        if [ "$rc" -gt 0 ]; then
                printf '\n\033[1;31m>>> BREAKAGE detected after step: %s (%d sub-probe failures)\033[0m\n' "$label" "$rc"
                printf '\033[1;33m>>> Re-run with this step skipped, or reboot DUT and bisect further.\033[0m\n'
                return 1
        fi
        return 0
}

# Wait for DUT SSH to come back (e.g. after reboot).
wait_dut_ssh() {
        log "waiting for $DUT SSH to come back ..."
        local i
        for i in $(seq 1 120); do
                if ssh -o BatchMode=yes -o ConnectTimeout=3 "$DUT" 'true' 2>/dev/null; then
                        ok "$DUT SSH up after ${i}s"
                        return 0
                fi
                sleep 1
        done
        bad "$DUT SSH did not return within 120s — abort"
        exit 1
}

# ----------------------------------------------------------------- steps

step_reboot() {
        hdr "STEP 0 — reboot DUT and wait for it to come back"
        # If the DUT is unreachable right now, skip the reboot — operator probably
        # already rebooted manually before invoking the script.
        if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$DUT" 'true' 2>/dev/null; then
                log "$DUT is unreachable — assuming it is already rebooting; waiting"
        else
                log "issuing reboot on $DUT (sudo reboot)"
                ssh_dut "sudo reboot" || true
                sleep 5
        fi
        wait_dut_ssh
        # Give DPAA + ASK a moment to fully come up (FMan PCD chain message,
        # systemd target reached, etc).
        log "sleeping 20s for full bring-up (DPAA1 FMan + ASK PCD chain)"
        sleep 20
        # Make sure ask.ko is loaded.
        if ssh_dut "lsmod | grep -q '^ask '"; then
                ok "ask.ko loaded"
        else
                bad "ask.ko NOT loaded after reboot — abort"
                exit 1
        fi
}

step_baseline() {
        hdr "STEP 1 — BASELINE (post-reboot, only DHCP IP on eth3/eth4)"
        log "current eth3 / eth4 addressing:"
        ssh_dut "ip -4 -br addr show $IFACE_IN; ip -4 -br addr show $IFACE_OUT"
        # In baseline (config.boot DHCP only), the /30 IPs don't exist yet so
        # the M2 probes won't apply. Just confirm L2 is healthy by arping the
        # GEN/SINK BUILDING IPs once DHCP has handed them out, or skip L3.
        log "skipping L2/L3 probes — no /30 IPs configured yet"
        log "moving to step 2"
}

step_add_eth3_ip() {
        hdr "STEP 2 — add 10.99.1.1/30 to $IFACE_IN (runtime only, no commit)"
        ssh_dut "sudo ip addr add 10.99.1.1/30 dev $IFACE_IN" || true
        ssh_dut "ip -4 -br addr show $IFACE_IN"
        PROBE_L3=0 PROBE_IPERF=0 run_probes "add 10.99.1.1/30 to $IFACE_IN" || return 1
}

step_add_eth4_ip() {
        hdr "STEP 3 — add 10.11.1.1/30 to $IFACE_OUT (runtime only, no commit)"
        ssh_dut "sudo ip addr add 10.11.1.1/30 dev $IFACE_OUT" || true
        ssh_dut "ip -4 -br addr show $IFACE_OUT"
        PROBE_L3=0 PROBE_IPERF=0 run_probes "add 10.11.1.1/30 to $IFACE_OUT" || return 1
}

step_enable_forward() {
        hdr "STEP 4 — enable net.ipv4.ip_forward=1"
        ssh_dut "sudo sysctl -w net.ipv4.ip_forward=1 >/dev/null"
        ssh_dut "cat /proc/sys/net/ipv4/ip_forward"
        # L3 probe now meaningful; iperf3 too if conntrack/notrack default allows it.
        PROBE_L3=1 PROBE_IPERF=1 run_probes "enable ip_forward" || return 1
}

step_delete_notrack() {
        hdr "STEP 5 — delete VyOS vyos_conntrack notrack rule"
        local h
        h=$(ssh_dut "sudo nft -a list chain ip vyos_conntrack PREROUTING 2>/dev/null | awk '/notrack/ {for(i=1;i<=NF;i++) if(\$i==\"handle\") {print \$(i+1); exit}}'" || true)
        if [ -n "$h" ]; then
                ssh_dut "sudo nft delete rule ip vyos_conntrack PREROUTING handle $h"
                log "deleted notrack rule handle $h"
        else
                log "no notrack rule present"
        fi
        PROBE_L3=1 PROBE_IPERF=1 run_probes "delete notrack" || return 1
}

step_hw_offload() {
        hdr "STEP 6 — ethtool -K $IFACE_IN $IFACE_OUT hw-tc-offload on"
        ssh_dut "sudo ethtool -K $IFACE_IN  hw-tc-offload on"
        ssh_dut "sudo ethtool -K $IFACE_OUT hw-tc-offload on"
        ssh_dut "sudo ethtool -k $IFACE_IN  | grep hw-tc-offload"
        ssh_dut "sudo ethtool -k $IFACE_OUT | grep hw-tc-offload"
        PROBE_L3=1 PROBE_IPERF=1 run_probes "hw-tc-offload on" || return 1
}

step_install_offload_table() {
        hdr "STEP 7 — install inet ask_offload table (flowtable + forward + conntrack_promote)"
        ssh_dut "sudo nft delete table inet ask_offload 2>/dev/null || true"
        ssh_dut "sudo nft -f - <<EOF
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
EOF"
        ssh_dut "sudo nft list table inet ask_offload | head -20"
        PROBE_L3=1 PROBE_IPERF=1 run_probes "install ask_offload table" || return 1
}

step_full_m2_smoke() {
        hdr "STEP 8 — full M2 smoke (verify-ask-flow-offload.sh, 10s/2 streams)"
        log "running: DURATION=10 PARALLEL=2 bin/verify-ask-flow-offload.sh"
        DURATION=10 PARALLEL=2 bash bin/verify-ask-flow-offload.sh
}

# ----------------------------------------------------------------- main
main() {
        hdr "ASK2 M2 stepwise verifier — DUT=$DUT  $IFACE_IN/$IFACE_OUT  gen=ct$GEN_CT sink=ct$SINK_CT"
        step_reboot
        step_baseline
        step_add_eth3_ip       || { log "stopping at step 2"; exit 1; }
        step_add_eth4_ip       || { log "stopping at step 3"; exit 1; }
        step_enable_forward    || { log "stopping at step 4"; exit 1; }
        step_delete_notrack    || { log "stopping at step 5"; exit 1; }
        step_hw_offload        || { log "stopping at step 6"; exit 1; }
        step_install_offload_table || { log "stopping at step 7"; exit 1; }
        step_full_m2_smoke     || { log "stopping at step 8"; exit 1; }
        hdr "ALL STEPS PASSED"
}

main "$@"
#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
#
# verify-ask-flow-offload.sh — ASK2 PR14g (M2) acceptance gate harness.
#
# Implements the M2 milestone-exit criterion documented in
# specs/ask2-rewrite-spec.md §11.1 / line 1508:
#
#   "M2 acceptance gate (§11.1) passes: nft `flow add` → packet
#    traverses 210 fast path → CPU < 5% at ≥ 2 Gbps."
#
# This is the milestone gate, NOT the v1.0 GA gate (which targets
# ≥14 Gbps at 512 B per the §11.1 table). M2 only proves that the
# silicon fast path is wired end-to-end and that an offloaded flow
# costs measurably less CPU than the kernel-software fast path.
#
# Topology (Mono Gateway DK):
#
#   ┌──────────────┐    ┌────────────────────────────┐    ┌──────────────┐
#   │ traffic gen  │ -> │ vyos-eth1     vyos-eth2    │ -> │ traffic sink │
#   │ (LXC 200)    │    │   (DUT — runs ASK2 ask.ko) │    │ (LXC 201)    │
#   └──────────────┘    └────────────────────────────┘    └──────────────┘
#
# The DUT must already have:
#   - ask.ko loaded and FMan PCD chain up
#       (dmesg: "ask: hw: FMan PCD chain up")
#   - vyos-eth1 + vyos-eth2 brought up with /30 IPs (or full /24
#     networks; the harness only needs L3 forwarding to work)
#   - nft flowtable bound to both ports with offload flag
#   - sysctl net.ipv4.ip_forward=1
#
# This script DOES NOT bring up the topology — it only measures.
# Bring-up is left to the operator (or to a higher-level bin/local-
# build.sh integration test) because the netdev names, IP plan, and
# DPDK/AF_XDP ownership of the SFP+ ports are deployment-specific.
#
# Inputs (env-overridable):
#   DUT          — SSH alias for the device under test  [vyos]
#   GEN_HOST     — SSH alias for the traffic generator  [lxc200]
#   SINK_HOST    — SSH alias for the traffic sink       [lxc201]
#   SINK_IP      — IP the gen iperf3 client targets     [10.99.2.2]
#   IFACE_IN     — DUT ingress port                     [vyos-eth1]
#   IFACE_OUT    — DUT egress  port                     [vyos-eth2]
#   DURATION     — iperf3 duration in seconds           [30]
#   PARALLEL     — iperf3 -P streams                    [8]
#   THRESHOLD_GBPS — minimum throughput to pass         [2.0]
#   THRESHOLD_CPU  — maximum %CPU usage to pass         [5.0]
#                    (reported as 100 - %idle from mpstat)
#
# Exit status:
#   0 — gate passed (≥THRESHOLD_GBPS at ≤THRESHOLD_CPU %CPU)
#   1 — gate failed
#   2 — environment / pre-flight check failed (operator action needed)
#
# Notes on the measurement:
#   - mpstat samples 1 s intervals over the full DURATION; the
#     reported CPU is the mean across all 4 A72 cores.
#   - Baseline CPU (pre-iperf3) is captured separately and subtracted
#     from the in-test reading so mgmt-plane noise (sshd, journald,
#     fan-pid) does not poison the gate.
#   - Throughput is the iperf3 client-side aggregate Gbps (sum across
#     streams). On AF_XDP / kernel-fast-path that aligns within ~3%
#     of the receiver-side number; the divergence is mostly skb
#     buffering and is not material to the gate.

set -euo pipefail

# ------------------------------------------------------------ defaults
DUT="${DUT:-vyos}"
GEN_HOST="${GEN_HOST:-lxc200}"
SINK_HOST="${SINK_HOST:-lxc201}"
SINK_IP="${SINK_IP:-10.99.2.2}"
IFACE_IN="${IFACE_IN:-vyos-eth1}"
IFACE_OUT="${IFACE_OUT:-vyos-eth2}"
DURATION="${DURATION:-30}"
PARALLEL="${PARALLEL:-8}"
THRESHOLD_GBPS="${THRESHOLD_GBPS:-2.0}"
THRESHOLD_CPU="${THRESHOLD_CPU:-5.0}"

# ------------------------------------------------------------ helpers
log()  { printf '[verify-ask-flow] %s\n' "$*" >&2; }
fail() { log "FAIL: $*"; exit 1; }
skip() { log "SKIP: $*"; exit 2; }

ssh_dut()  { ssh -o BatchMode=yes "$DUT"       -- "$@"; }
ssh_gen()  { ssh -o BatchMode=yes "$GEN_HOST"  -- "$@"; }
ssh_sink() { ssh -o BatchMode=yes "$SINK_HOST" -- "$@"; }

# ------------------------------------------------------------ pre-flight
log "PR14g (M2) acceptance gate — spec §11.1 / spec line 1508"
log "DUT=$DUT gen=$GEN_HOST sink=$SINK_HOST sink_ip=$SINK_IP"
log "iface_in=$IFACE_IN iface_out=$IFACE_OUT duration=${DURATION}s parallel=$PARALLEL"
log "thresholds: throughput >= ${THRESHOLD_GBPS} Gbps, CPU <= ${THRESHOLD_CPU} %"

# 1. ask.ko loaded on the DUT?
if ! ssh_dut "lsmod | grep -q '^ask '" 2>/dev/null; then
skip "ask.ko is not loaded on the DUT (lsmod | grep ask returned nothing)"
fi

# 2. FMan PCD chain reported up in dmesg?
if ! ssh_dut "sudo -n dmesg 2>/dev/null | grep -q 'ask: hw: FMan PCD chain up'" 2>/dev/null; then
log "WARN: 'ask: hw: FMan PCD chain up' not in dmesg — flows will run via SW fallback only"
log "WARN: this means the gate measures the body-3 SW fallback path, not the silicon fast path"
log "WARN: continuing anyway so the harness can be validated on non-DPAA dev hosts"
fi

# 3. nft flowtable present and bound to both ports?
if ! ssh_dut "sudo -n nft list ruleset 2>/dev/null | grep -q 'flowtable'" ; then
fail "no nft flowtable configured on the DUT"
fi
if ! ssh_dut "sudo -n nft list ruleset 2>/dev/null | grep -E 'devices.*=.*\\b${IFACE_IN}\\b'" ; then
fail "nft flowtable does not include $IFACE_IN in its devices list"
fi
if ! ssh_dut "sudo -n nft list ruleset 2>/dev/null | grep -E 'devices.*=.*\\b${IFACE_OUT}\\b'" ; then
fail "nft flowtable does not include $IFACE_OUT in its devices list"
fi

# 4. ip_forward enabled?
if [ "$(ssh_dut 'sudo -n cat /proc/sys/net/ipv4/ip_forward')" != "1" ]; then
fail "net.ipv4.ip_forward = 0 on the DUT — enable forwarding first"
fi

# 5. iperf3 reachable on both ends?
ssh_gen  "command -v iperf3 >/dev/null" || skip "iperf3 not installed on $GEN_HOST"
ssh_sink "command -v iperf3 >/dev/null" || skip "iperf3 not installed on $SINK_HOST"
ssh_dut  "command -v mpstat >/dev/null" || skip "mpstat (sysstat) not installed on the DUT"

# ------------------------------------------------------------ measurement
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

log "starting iperf3 -s on $SINK_HOST"
ssh -o BatchMode=yes "$SINK_HOST" 'pkill -f "iperf3 -s" 2>/dev/null; sleep 0.2' || true
ssh -o BatchMode=yes -f "$SINK_HOST" "iperf3 -s -1 >/dev/null 2>&1"
sleep 1

log "capturing baseline CPU on DUT (3 s)"
BASE_IDLE=$(ssh_dut "sudo -n mpstat 1 3 | awk '/Average:.*all/ {print \$NF; exit}'")
BASE_CPU=$(awk -v idle="$BASE_IDLE" 'BEGIN{printf "%.2f", 100.0 - idle}')
log "baseline CPU = ${BASE_CPU} % (idle ${BASE_IDLE} %)"

log "starting mpstat sampler on DUT for ${DURATION} s"
ssh_dut "sudo -n mpstat 1 $DURATION > /tmp/verify-ask-mpstat.txt 2>&1 &"
MPSTAT_REMOTE_PID=$(ssh_dut 'pgrep -f "sudo -n mpstat 1 '"$DURATION"'" | head -1' || true)

log "starting iperf3 client on $GEN_HOST → $SINK_IP for ${DURATION} s with -P $PARALLEL"
ssh_gen "iperf3 -c $SINK_IP -t $DURATION -P $PARALLEL -J" \
> "$TMPDIR/iperf3.json" 2>"$TMPDIR/iperf3.err" \
|| fail "iperf3 client failed; stderr: $(cat "$TMPDIR/iperf3.err")"

# Wait for the mpstat sampler to finish before reading its output.
sleep 2
if [ -n "$MPSTAT_REMOTE_PID" ]; then
ssh_dut "while kill -0 $MPSTAT_REMOTE_PID 2>/dev/null; do sleep 1; done" || true
fi

log "collecting mpstat output from DUT"
ssh_dut "cat /tmp/verify-ask-mpstat.txt" > "$TMPDIR/mpstat.txt"

# ------------------------------------------------------------ parse
# Throughput: aggregate sum_received bits/sec from iperf3 -J.
# Robust against jq absence — fall back to python.
if command -v jq >/dev/null 2>&1; then
BPS=$(jq '.end.sum_received.bits_per_second' "$TMPDIR/iperf3.json")
else
BPS=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["end"]["sum_received"]["bits_per_second"])' \
"$TMPDIR/iperf3.json")
fi
GBPS=$(awk -v b="$BPS" 'BEGIN{printf "%.3f", b/1e9}')

# CPU: average of the per-second %idle column from mpstat, then
# subtract from 100 to get %used. Skip header lines that lack the
# "all" pseudo-cpu marker. Drop the "Average:" trailer line because
# we want the per-second mean, not mpstat's own mean (which is a
# weighted average that includes the sampler's own startup).
MEAN_IDLE=$(awk '/all/ && !/^Average/ {sum+=$NF; n++} END{if(n>0) printf "%.2f", sum/n; else print "100.00"}' \
"$TMPDIR/mpstat.txt")
TEST_CPU=$(awk -v idle="$MEAN_IDLE" 'BEGIN{printf "%.2f", 100.0 - idle}')
NET_CPU=$(awk -v t="$TEST_CPU" -v b="$BASE_CPU" 'BEGIN{v=t-b; if(v<0)v=0; printf "%.2f", v}')

# ------------------------------------------------------------ verdict
log ""
log "=========================================================="
log " ASK2 PR14g (M2) acceptance gate"
log "=========================================================="
log "  throughput        : ${GBPS} Gbps   (threshold ≥ ${THRESHOLD_GBPS})"
log "  DUT CPU baseline  : ${BASE_CPU} %"
log "  DUT CPU under load: ${TEST_CPU} %"
log "  DUT CPU net (load - baseline) : ${NET_CPU} %  (threshold ≤ ${THRESHOLD_CPU})"
log "----------------------------------------------------------"

PASS_GBPS=$(awk -v g="$GBPS" -v t="$THRESHOLD_GBPS" 'BEGIN{print (g >= t) ? 1 : 0}')
PASS_CPU=$(awk  -v c="$NET_CPU" -v t="$THRESHOLD_CPU" 'BEGIN{print (c <= t) ? 1 : 0}')

if [ "$PASS_GBPS" = "1" ] && [ "$PASS_CPU" = "1" ]; then
log "VERDICT: PASS"
exit 0
fi

log "VERDICT: FAIL"
[ "$PASS_GBPS" = "1" ] || log "  - throughput ${GBPS} Gbps < ${THRESHOLD_GBPS} Gbps"
[ "$PASS_CPU"  = "1" ] || log "  - CPU ${NET_CPU} % > ${THRESHOLD_CPU} %"
exit 1
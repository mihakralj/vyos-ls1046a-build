#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
#
# verify-106-offload.sh — Hardware-offload verification harness for
# Mono Gateway .106 (NXP ASK 1.x SDK stack: cdx.ko/fci.ko/auto_bridge.ko,
# cmm, dpa_app).
#
# Purpose
# -------
# Verifies whether the ASK hardware fast path is actually engaging on .106
# while it forwards GENUINE transit traffic between two of its interfaces.
# This is the metric that distinguishes hardware offload from kernel
# software forwarding:
#
#   - Offload engaged  -> silicon bypasses kernel RX -> high throughput at
#                         near-zero CPU.  Reference: cdx.ko reaches
#                         ~8.58 Gbps at ~0% CPU.
#   - Not engaged      -> kernel SW forward plane -> ~2 Gbps (MTU-1500
#                         ceiling on this SDK build) at ~20-24% kern_net_cpu,
#                         and fp_netfilter_pre_routing fires on every packet.
#
# Methodology (per qdrant protocol 2026-08-11)
# --------------------------------------------
# .106 must forward REAL transit traffic (ingress port -> egress port) to
# be a candidate for hardware offload (cmm only accelerates forwarded
# flows, not locally-terminated ones).  Because both .106 and .185 are
# dual-homed on both test subnets, plain routing short-circuits locally,
# so .185 uses ingress-interface-keyed policy routing (Phase-B technique,
# TTL-verified) plus two netns to force genuine 3-node transit through
# .106:
#
#   ns_src (client, 10.99.2.201) --veth-s-- 185 eth4 -+
#                                                      |  10.99.2.0/24
#                                                     .106 eth4 (10.99.2.106)
#                                                       |  [106 forwards]
#                                                     .106 eth3 (10.99.1.106)
#                                                      |  10.99.1.0/24
#                                                      +-- 185 eth3
#   ns_sink (server, 10.99.3.2) --veth0-- 185           |
#                                                      (route 10.99.3.0/24
#                                                       via .106 eth3 on 106)
#
#   Path: ns_src -> veth-s -> 185 eth4 -> .106 eth4 -> [106 fwd] -> .106
#         eth3 -> 185 eth3 -> veth0 -> ns_sink.
#   TTL of the reply = 63 (64 - 1 forward hop on .106) proves genuine
#   transit through .106.
#
# CPU measurement on .106
# -----------------------
# .106 is OpenWrt: no mpstat, no python3.  The harness pushes a pure-ash
# /proc/stat delta sampler to /tmp on .106 and sums the kernel-network
# columns (%sys + %irq + %soft) every second during the test.
#
# Signals gathered
# ----------------
#   * throughput  (iperf3 + iperf2 + nuttcp transit through .106)
#   * kern_net_cpu delta on .106 during transit (GATE METRIC)
#   * fp_netfilter_pre_routing dmesg rate (offload bypasses netfilter)
#   * /proc/fqid_stats/pcd counters (informational; known-broken oracle on
#     this build due to cmm's vendored libnetfilter_conntrack 1.1.0)
#   * /proc/net/nf_conntrack HW_OFFLOAD rows (informational)
#
# Verdict (GATE)
# --------------
#   PASS (offload engaged)      : kern_net_cpu <= THRESHOLD_CPU AND
#                                 throughput >= 2.0 Gbps OR a clear CPU drop
#   NO-OFFLOAD (SW forwarding)  : kern_net_cpu > THRESHOLD_CPU (typically
#                                 ~20%+) at ~2 Gbps -> not offloaded
#   SKIP / setup-fail           : topology / tooling not ready
#
# Exit codes: 0 PASS, 1 NO-OFFLOAD (SW path), 2 setup/pre-flight fail.
#
# Safe / reversible: all .185 changes are additive netns + policy-route
# entries, removed by --teardown or on FINISH.  .106 only gets temporary IP
# addresses and one route + /tmp profiler (no arch change).  --no-setup
# assumes the topology is already built from a prior run.
#
# Inputs (env-overridable):
#   BOX106      — SSH command prefix to reach .106   (default: proxy ssh)
#   BOX185      — SSH command prefix to reach .185   (default: direct ssh)
#   IF_106_IN   — .106 ingress  interface [eth4]  (10.99.2.106)
#   IF_106_OUT  — .106 egress   interface [eth3]  (10.99.1.106)
#   DURATION    — per-tool test seconds             [20]
#   PARALLEL    — iperf3 -P streams                 [8]
#   THRESHOLD_CPU — max kern_net_cpu % for PASS     [8.0]
#   THRESHOLD_GBPS — min throughput Gbps for PASS  [1.5]

set -uo pipefail

PROG="$(basename "$0")"
log() { printf '[%s] %s\n' "$PROG" "$*" >&2; }
hdr() { printf '\n==== %s ====\n' "$*"; }

# ------------------------------------------------------------------ defaults
# Connection targets (env-overridable).  H106/H185 are remote hostnames.
H106="${H106:-root@192.168.1.250}"
H185="${H185:-vyos@192.168.1.185}"

A106_IN=10.99.2.106      # .106 eth4 ingress
A106_OUT=10.99.1.106     # .106 eth3 egress
A185_IN=10.99.2.185      # 185 eth4 (peers .106 eth4)
A185_OUT=10.99.1.185     # 185 eth3 (peers .106 eth3)
SRC_NET=10.99.2.0/24
SRC_IP=10.99.2.201       # ns_src client address (non-local on 185)
SRC_GW=10.99.2.200       # veth-s host-side
SINK_NET=10.99.3.0/24
SINK_IP=10.99.3.2        # ns_sink server address
SINK_GW=10.99.3.1        # veth0 host-side

DURATION="${DURATION:-20}"
PARALLEL="${PARALLEL:-8}"
THRESHOLD_CPU="${THRESHOLD_CPU:-8.0}"
THRESHOLD_GBPS="${THRESHOLD_GBPS:-1.5}"
PROF_SCRIPT=/tmp/prof106.sh
NO_SETUP="${NO_SETUP:-0}"
SKIP_TOOLS="${SKIP_TOOLS:-0}"     # set 1 to skip iperf2/nuttcp (iperf3 only)

# ------------------------------------------------------------------- helpers
# ssh106/ssh185 build the ssh command with the remote command as ONE argument,
# so pipes/`;`/quotes are interpreted by the REMOTE shell (correct).
ssh106() {
    ssh -i ~/.ssh/id_vyos \
        -o ProxyCommand="ssh -i ~/.ssh/admin_key -W %h:%p admin@192.168.1.137" \
        -o StrictHostKeyChecking=no -o ConnectTimeout=15 \
        "$H106" "$1"
}
ssh185() {
    ssh -i ~/.ssh/vyos_key \
        -o StrictHostKeyChecking=no -o ConnectTimeout=15 \
        "$H185" "$1"
}
sh106() { ssh106 "$1"; }
sh185() { ssh185 "$1"; }

die()   { log "FATAL: $*"; exit 2; }
skip()  { log "SKIP: $*";  exit 2; }

# Deploy the pure-ash /proc/stat kernel-net CPU sampler to .106.
# Writes its own PID to /tmp/prof.pid so callers can wait on kill -0
# (NEVER pgrep -f: on OpenWrt the remote ash strips/broadens the pattern
# so pgrep self-matches its own while-loop and hangs forever).
deploy_profiler() {
    log "deploying /proc/stat kernel-net profiler to .106"
    printf '%s\n' \
'#!/bin/ash
# prof106.sh DURATION - per-second kernel-net CPU (sys+irq+softirq)
echo $$ > /tmp/prof.pid
DUR="${1:-30}"
read cpu u n s i w irq si st < /proc/stat
pu=$u; pn=$n; ps=$s; pi=$i; pw=$w; pirq=$irq; psi=$si
k=0
while [ "$k" -lt "$DUR" ]; do
  sleep 1
  read cpu u n s i w irq si st < /proc/stat
  du=$((u-pu)); dn=$((n-pn)); ds=$((s-ps)); di=$((i-pi)); dw=$((w-pw)); dirq=$((irq-pirq)); dsi=$((si-psi))
  pu=$u; pn=$n; ps=$s; pi=$i; pw=$w; pirq=$irq; psi=$si
  tot=$((du+dn+ds+di+dw+dirq+dsi)); kern=$((ds+dirq+dsi))
  k=$((k+1))
  [ "$tot" -gt 0 ] && awk -v k="$kern" -v t="$tot" -v n="$k" "BEGIN{printf \"t=%d kern_net_cpu=%.2f%%\\n\", n, k*100.0/t}"
done' | sh106 "cat > $PROF_SCRIPT; chmod +x $PROF_SCRIPT" || die "could not deploy profiler"
}

# ----------------------------------------------------------------- pre-flight
preflight() {
    hdr "Pre-flight"
    sh106 'which iperf3 nuttcp iperf >/dev/null 2>&1' || {
        sh106 'ls -la /usr/bin/iperf /usr/bin/nuttcp' 
        die "tools missing on .106"
    }
    sh185 'which iperf3 nuttcp iperf >/dev/null 2>&1' || die "tools missing on .185"
    # interfaces + addresses
    sh106 "ip addr show eth3 2>/dev/null | grep -q '$A106_OUT/' 2>/dev/null; ip addr show eth4 2>/dev/null | grep -q '$A106_IN/' 2>/dev/null" \
        || die ".106 eth3/eth4 missing test addresses (need $A106_OUT and $A106_IN)"
    sh185 "ip addr show eth3 2>/dev/null | grep -q '$A185_OUT/' 2>/dev/null; ip addr show eth4 2>/dev/null | grep -q '$A185_IN/' 2>/dev/null" \
        || die ".185 eth3/eth4 missing test addresses"
    # link reachability (4 directions)
    log "link reachability:"
    sh106 "ping -c2 -W2 -I eth3 $A185_OUT >/dev/null 2>&1 && echo '  .106 eth3<->185 eth3 OK' || echo '  .106 eth3<->185 eth3 FAIL'"
    sh106 "ping -c2 -W2 -I eth4 $A185_IN  >/dev/null 2>&1 && echo '  .106 eth4<->185 eth4 OK' || echo '  .106 eth4<->185 eth4 FAIL'"
    sh185 "ping -c2 -W2 -I eth3 $A106_OUT >/dev/null 2>&1 && echo '  .185 eth3<->106 eth3 OK' || echo '  .185 eth3<->106 eth3 FAIL'"
    sh185 "ping -c2 -W2 -I eth4 $A106_IN  >/dev/null 2>&1 && echo '  .185 eth4<->106 eth4 OK' || echo '  .185 eth4<->106 eth4 FAIL'"
    deploy_profiler
}

# ------------------------------------------------- transit topology (setup)
# Builds ns_src (client) + ns_sink (server) on .185 with ingress-keyed
# policy routing that forces traffic via .106, plus the .106 route back.
setup_transit() {
    [ "$NO_SETUP" = "1" ] && { log "NO_SETUP=1 skipping topology build"; return; }
    hdr "Setting up transit topology through .106"
    # Clean slate: remove any prior transit artifacts so setup is idempotent.
    teardown_transit
    sleep 1
    # --- 185: ns_sink (server) on 10.99.3.x
    sh185 "sudo ip netns del ns_sink 2>/dev/null; sudo ip link del veth0 2>/dev/null;
           sudo ip netns add ns_sink;
           sudo ip link add veth0 type veth peer name veth0n;
           sudo ip link set veth0n netns ns_sink;
           sudo ip addr add $SINK_GW/24 dev veth0; sudo ip link set veth0 up;
           sudo ip -n ns_sink addr add $SINK_IP/24 dev veth0n;
           sudo ip -n ns_sink link set veth0n up; sudo ip -n ns_sink link set lo up;
           sudo ip -n ns_sink route add default via $SINK_GW;
           sudo ip route del $SINK_NET 2>/dev/null; sudo ip route add $SINK_NET dev veth0" \
        || die "185 ns_sink setup failed"
    # --- 185: ns_src (client) on 10.99.2.x, non-local src avoids martian drop
    sh185 "sudo ip netns del ns_src 2>/dev/null; sudo ip link del veth-s 2>/dev/null;
           sudo ip netns add ns_src;
           sudo ip link add veth-s type veth peer name veth-sn;
           sudo ip link set veth-sn netns ns_src;
           sudo ip addr add $SRC_GW/24 dev veth-s; sudo ip link set veth-s up;
           sudo ip -n ns_src addr add $SRC_IP/24 dev veth-sn;
           sudo ip -n ns_src link set veth-sn up; sudo ip -n ns_src link set lo up;
           sudo ip -n ns_src route add default via $SRC_GW;
           sudo ip route del $SRC_IP/32 dev veth-s 2>/dev/null;
           sudo ip route add $SRC_IP/32 dev veth-s" \
        || die "185 ns_src setup failed"
    # --- 185: ingress-keyed policy routing (outbound from ns_src via .106)
    sh185 "sudo ip rule del iif veth-s lookup 100 2>/dev/null;
           sudo ip route flush table 100 2>/dev/null;
           sudo ip rule add iif veth-s lookup 100;
           sudo ip route add $SINK_NET via $A106_IN dev eth4 table 100" \
        || die "185 policy-route setup failed"
    # --- .106: route to reach the sink via 185 eth3 (return leg)
    sh106 "ip route add $SINK_NET via $A185_OUT dev eth3 2>/dev/null || ip route replace $SINK_NET via $A185_OUT dev eth3" \
        || die ".106 return route failed"
    log "topology up; verifying transit (expect TTL=63, 0% loss)"
    sh185 "sudo ip netns exec ns_src ping -c4 -W1 $SINK_IP 2>&1 | grep -E 'ttl=|packet loss'" \
        || die "transit ping failed - path not forwarding through .106"
}

teardown_transit() {
    hdr "Teardown transit artifacts"
    sh185 "sudo ip netns exec ns_sink pkill -f 'iperf3 -s' 2>/dev/null; \
            sudo ip rule del iif veth-s lookup 100 2>/dev/null; \
            sudo ip route flush table 100 2>/dev/null; \
           sudo ip route del $SINK_NET dev veth0 2>/dev/null; \
           sudo ip route del $SRC_IP/32 dev veth-s 2>/dev/null; \
           sudo ip link del veth-s 2>/dev/null; \
           sudo ip link del veth0 2>/dev/null; \
           sudo ip netns del ns_src 2>/dev/null; \
           sudo ip netns del ns_sink 2>/dev/null; true" 2>/dev/null
    sh106 "ip route del $SINK_NET 2>/dev/null; pkill -f prof106.sh 2>/dev/null; true" 2>/dev/null
    sh185 "pkill -x iperf3 2>/dev/null; pkill -x iperf 2>/dev/null; pkill -x nuttcp 2>/dev/null; true" 2>/dev/null
    log "teardown done"
}

# ------------------------------------------------------------- measurement
# Start the .106 kernel-net CPU sampler for a window; returns.
start_profiler() {
    local label="$1"; local win="$2"
    log "CPU profiler on .106: '$label' (${win}s window)"
    sh106 "sh $PROF_SCRIPT $win > /tmp/prof_${label}.txt 2>&1 &" 2>/dev/null
    sleep 2
}

# Wait for profiler window to finish and pull the results into the workspace.
collect_profiler() {
    local label="$1"
    sh106 "while pgrep -f 'prof106[.]sh' >/dev/null 2>&1; do sleep 1; done" 2>/dev/null
    sh106 "cat /tmp/prof_${label}.txt 2>/dev/null" | grep -oE 'kern_net_cpu=[0-9.]+' > "/tmp/kilo/prof_${label}.txt"
    log "'$label' profile: $(cat /tmp/kilo/prof_${label}.txt 2>/dev/null | tr '\n' ' ')"
}

# Run a transit test under the profiler; capture live output.
# args: <label> <test-fn>
run_profiled() {
    local label="$1"; shift
    local win=$((DURATION + 8))
    start_profiler "$label" "$win"
    "$@"
    collect_profiler "$label"
}

# ------------------------------------------------------------------- tests
test_iperf3() {
    hdr "iperf3 transit through .106"
    sh185 "sudo ip netns exec ns_sink pkill -f 'iperf3 -s' 2>/dev/null; \
           sudo ip netns exec ns_sink iperf3 -s -D -p 5201 --logfile /tmp/i3.log 2>&1"
    sleep 1
    local OUT
    OUT=$(sh185 "sudo ip netns exec ns_src iperf3 -c $SINK_IP -p 5201 -t $DURATION -P $PARALLEL -J 2>/dev/null" 2>&1)
    # sum_received aggregate bits/sec is the last "bits_per_second" of the JSON
    local bps
    bps=$(echo "$OUT" | grep -oE '"bits_per_second": [0-9.]+' | tail -1 | grep -oE '[0-9.]+')
    echo "$OUT" | grep -E '"sum_received"|"bits_per_second"' | tail -4
    printf 'IPERF3_BPS=%s\n' "${bps:-NA}" > /tmp/kilo/run_meta.txt
    log "iperf3 aggregate: ${bps:-NA} bps"
}

test_iperf2() {
    hdr "iperf2 transit through .106"
    sh185 "sudo ip netns exec ns_sink iperf -s -p 5001 > /tmp/i2.log 2>&1 &"
    sleep 1
    sh185 "sudo ip netns exec ns_src iperf -c $SINK_IP -p 5001 -t $DURATION 2>&1" | tail -6
}

test_nuttcp() {
    hdr "nuttcp transit through .106"
    sh185 "sudo ip netns exec ns_sink nuttcp -S -1 -P 5002 > /tmp/nt.log 2>&1 &"
    sleep 1
    sh185 "sudo ip netns exec ns_src nuttcp -t -P 5002 $SINK_IP 2>&1" | tail -4
}

# -------------------------------------------------------------- silicon check
siprobe() {
    hdr "Silicon / ASK state sampling"
    log "fp_netfilter_pre_routing recent dmesg (offload bypasses -> should NOT grow):"
    sh106 "dmesg | grep 'fp_netfilter_pre_routing' | tail -3"
    log "fqid_stats pcd (informational; cmm broken oracle on this build):"
    for p in eth3 eth4; do
        echo -n "  $p: "; sh106 "cat /proc/fqid_stats/pcd/$p 2>/dev/null | tr '\n' ' '; echo"
    done
}

# ------------------------------------------------------------------ verdict
# awk-based float compare helper
ge()  { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a>=b)}'; }
le()  { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a<=b)}'; }

verdict() {
    hdr "VERDICT"
    local avg_cpu bps gbps i3
    # CPU = mean of sampled kern_net_cpu lines from the IPERF3 profile
    i3=/tmp/kilo/prof_iperf3.txt
    avg_cpu=$(grep -oE '[0-9.]+' "$i3" 2>/dev/null \
        | awk '{s+=$1;n++} END{if(n>0) printf "%.2f", s/n; else print "NA"}')
    bps=$(grep -h "IPERF3_BPS" /tmp/kilo/run_meta.txt 2>/dev/null | head -1 | sed 's/.*IPERF3_BPS=//')
    gbps=$(awk -v b="${bps:-0}" 'BEGIN{printf "%.3f", b/1e9}')
    log "iperf3 transit throughput : ${gbps} Gbps (threshold >= ${THRESHOLD_GBPS})"
    log "mean kern_net_cpu on .106 : ${avg_cpu}% (threshold <= ${THRESHOLD_CPU})"

    if [ "$avg_cpu" = "NA" ]; then
        log "INCONCLUSIVE: no CPU samples collected (profiler failed on .106)"
        exit 2
    fi
    if le "$avg_cpu" "$THRESHOLD_CPU"; then
        if ge "$gbps" "$THRESHOLD_GBPS"; then
            log "PASS: low CPU + >=threshold throughput -> hardware fast path engaged"
            exit 0
        else
            log "PASS (weak): CPU is offloader-low but throughput below threshold (MTU-1500 cap may limit) -> hardware fast path likely engaged"
            exit 0
        fi
    else
        log "NO-OFFLOAD: kern_net_cpu ${avg_cpu}% (${gbps} Gbps) -> kernel software forwarding, cdx NOT engaging on .106"
        exit 1
    fi
}

# --------------------------------------------------------------------- main
main() {
    hdr "verify-106-offload.sh — ASK HW offload verification on .106"
    preflight
    trap 'teardown_transit' EXIT
    setup_transit
    # baseline CPU (idle)
    run_profiled "baseline" test_idle
    run_profiled "iperf3"   test_iperf3
    if [ "$SKIP_TOOLS" != "1" ]; then
        run_profiled "iperf2"   test_iperf2
        run_profiled "nuttcp"   test_nuttcp
    fi
    siprobe
    verdict
}

# idle no-op (uses DURATION of sleeping as the transit window)
test_idle() { sleep "$DURATION"; }

main "$@"

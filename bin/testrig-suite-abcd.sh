#!/usr/bin/env bash
# testrig-suite-abcd.sh — frame geometry, flow scale/churn, latency and feature-stacking battery (suites A-D).
# Run it from the control host against one DUT, then against the other, and compare the two result
# directories with bin/testrig-suite-compare.py. Generator: kernel pktgen via bin/testrig-pktgen.sh (X710 MAC
# counters, so neither host's socket path limits the result); latency via bin/testrig-latprobe.py (hardware
# timestamps); connection rate via bin/testrig-cps.py.
#
#   DUT_KIND=vendor bin/testrig-suite-abcd.sh A1 A2 A3 B1 B2 B3 C1 C2 D1 D2 D3     (.70, OWRT + NXP ASK)
#   DUT_KIND=ask2   bin/testrig-suite-abcd.sh A1 ...                               (.185, VyOS + ASK2)
#   no test names = all of them. Results: $RIG_OUT/suite-$DUT_KIND-$TAG/results.csv (+ raw logs).
#
# Every offloaded run is BIDIRECTIONAL (both generators transmit the mirrored tuples, loads and rates are totals
# over both directions): a DUT only offloads a flow once conntrack has seen both directions, and the pktgen
# harness warms every flow that way before it measures. Flows are UDP; every trial reports the hardware share.
#
# Test      What                                               Reference target from the test spec (NOT a verdict; results.csv is measurements only)
#  A1  64 B full-duplex NDR, 64 flows                          NDR >= 4.80 Mpps total at <= 0.001 % loss
#  A2  IMIX 7:4:1 (64/570/1518), 512 flows                     >= 9.2 Gbit/s L2 per direction, 0 loss
#  A3  9000 B frames, MTU 9000 on DUT + generators             >= 99 % of line rate (0.139 Mpps per direction), <= 0.001 % loss
#  B1  64 B, 1k..256k flows at 2.5 Mpps                        <= 0.005 % loss at 65,536 flows (other steps: info, hw share)
#  B2  TCP CPS ramp over 1.5 Mpps background                   >= 25,000 CPS, 0 failed connects, background <= 0.005 % loss
#  B3  32k held TCP flows torn down at 2.0 Mpps                background <= 0.005 % loss, flows alive, p99.9 <= 2x the quiet run
#  C1  latency at 1.32/2.11/2.37/2.61 Mpps (50/80/90/99 %)       one-way P99 <= 12.5 us and jitter p99.9 <= 2.0 us at 90 %
#  C2  jitter, 10 min at 2.0 Mpps                              info (jitter figures), loss <= 0.005 %
#  D1  VLAN vs untagged 64 B NDR (QinQ: needs QINQ_READY=1)    VLAN NDR >= 95 % of untagged
#  D2  line-rate flood + 100 pps ICMP/SSH to the router IP     zero lost ICMP, zero failed SSH connects
#  D3  IPsec/CAAM (needs IPSEC_DST: a host behind the tunnel)  info (throughput)
#
# Method notes (deviations from a literal reading of the test text):
#  * Latency is the one-way estimate (rtt/2) of a PTP-style hardware-timestamped probe at 1000/s that crosses the DUT
#    twice; its own flow is offloaded too. "Jitter" is the spread of successive round-trip differences (packet
#    delay variation), not raw arrival-time deltas, because the probe has no common clock to the generator.
#  * Offered rates are the fixed values below, the same for both DUTs; override with RATES_C1="r1 r2 r3 r4".
#  * Flow counts above the DUT's offload capacity (ASK2 ask.max_flows, default 32768) stay partly on the kernel path;
#    the run continues (WARM_PARTIAL=1) and the hw share column shows how much was offloaded.
# Env: DUT_KIND TAG OUTD TRIAL (pktgen trial seconds, 10) LOSS NDR_PPS (reuse A1's rate), STEPS_B1, RATES_B2.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
rig_require GEN1 GEN2
DUT_KIND=${DUT_KIND:-ask2}
case $DUT_KIND in ask2|vendor) ;; *) echo "DUT_KIND must be ask2 or vendor"; exit 2 ;; esac
TAG=${TAG:-$(date -u +%Y%m%dT%H%M%SZ)}
OUTD=${OUTD:-$RIG_OUT/suite-$DUT_KIND-$TAG}; mkdir -p "$OUTD"
RES=$OUTD/results.csv
[ -f "$RES" ] || echo "test,metric,value" > "$RES"
DUT=${DUT:-$(rig_dut "$DUT_KIND")} || exit 2
SSHO="-o BatchMode=yes -o ConnectTimeout=8 $RIG_SSHK"
if [ "$DUT_KIND" = vendor ]; then IF=$GEN_IF_NS; NS="sudo ip netns exec $VENDOR_NS"; DSUDO=""; else IF=$GEN_IF; NS="sudo"; DSUDO="sudo"; fi
g1(){ ssh -n $SSHO "$GEN1" "$@"; }
g2(){ ssh -n $SSHO "$GEN2" "$@"; }
dut(){ ssh -n $SSHO "$DUT" "$@"; }
PK(){ DUT_KIND=$DUT_KIND "$HERE/testrig-pktgen.sh" "$@"; }
TRIAL=${TRIAL:-10}; LOSS=${LOSS:-0.001}
G1A=10.99.1.112; G2A=10.99.2.113; R1=10.99.1.185; R2=10.99.2.185; R1V=10.99.10.185; R2V=10.99.20.185
CPSPORT=18080

# rec TEST METRIC VALUE [ignored...]: measurements only, no verdicts (trailing args are legacy and ignored)
rec(){
  printf '%-4s %-34s %s\n' "$1" "$2" "$3"
  echo "$1,$2,$3" >> "$RES"
}
skip(){ printf '%-4s SKIP: %s\n' "$1" "$2"; echo "$1,skipped,,$2,SKIP" >> "$RES"; }
csvlast(){ tail -n 1 "$1" 2>/dev/null; }          # last row of a pktgen csv
jget(){ python3 -c 'import json,sys;j=json.loads(sys.stdin.read().strip().splitlines()[-1])
v=j
for k in sys.argv[1].split("."): v=v[k]
print(v)' "$1" 2>/dev/null; }

# a2 result row: ts,dut,mode,path,size,best,median_delivered,mean_delivered,dev_pct,loss_pct,hw
a2row(){ csvlast "$1" | awk -F, '{print $6, $7, $8, $9, $10, $11}'; }

dut_ok(){ dut true >/dev/null 2>&1 || { echo "DUT $DUT unreachable: stopping"; exit 3; }; }
gens_prep(){
  scp -q $SSHO "$HERE/testrig-latprobe.py" "$HERE/testrig-cps.py" "$HERE/testrig-sshprobe.sh" "$GEN1:/tmp/" && scp -q $SSHO "$HERE/testrig-latprobe.py" "$HERE/testrig-cps.py" "$HERE/testrig-sshprobe.sh" "$GEN2:/tmp/" || { echo "cannot copy helpers to the generators"; exit 2; }
}
refl_start(){ refl_stop; g2 "$NS sh -c 'setsid nohup python3 /tmp/testrig-latprobe.py reflect $IF $G2A > /tmp/rig-lat-refl.log 2>&1 < /dev/null &'"; sleep 1; }
refl_stop(){ g2 "sudo pkill -TERM -f '[t]estrig-latprobe.py reflect'; sleep 1; true" >/dev/null 2>&1; }
cps_serve_start(){ cps_serve_stop; g2 "$NS sh -c 'setsid nohup python3 /tmp/testrig-cps.py serve $CPSPORT 4 > /tmp/cps-srv.log 2>&1 < /dev/null &'"; sleep 2; }
cps_serve_stop(){ g2 "sudo pkill -f '[t]estrig-cps.py serve'; true" >/dev/null 2>&1; }
cleanup(){ refl_stop; cps_serve_stop; for h in g1 g2; do $h "sudo pkill -f '[t]estrig-cps.py run'; sudo pkill -f '[t]estrig-latprobe.py probe'; true" >/dev/null 2>&1; done; restore_ctl; }
trap cleanup EXIT

# runtime-only DUT/generator settings, restored on exit
CT_ORIG=""; PORTS_ORIG=""; MTU_SET=0
conntrack_room(){ # N: nf_conntrack_max must hold both directions of N flows
  [ -n "$CT_ORIG" ] || CT_ORIG=$(dut "cat /proc/sys/net/netfilter/nf_conntrack_max")
  local need=$(( $1 * 2 + 65536 ))
  [ "${CT_ORIG:-0}" -ge "$need" ] || dut "$DSUDO sysctl -qw net.netfilter.nf_conntrack_max=$need" >/dev/null
}
restore_ctl(){
  [ -n "$CT_ORIG" ] && dut "$DSUDO sysctl -qw net.netfilter.nf_conntrack_max=$CT_ORIG" >/dev/null 2>&1
  [ -n "$PORTS_ORIG" ] && g1 "sudo sysctl -qw net.ipv4.ip_local_port_range='$PORTS_ORIG'" >/dev/null 2>&1
  [ "$MTU_SET" = 1 ] && set_mtu 1500 >/dev/null 2>&1
  CT_ORIG=""; PORTS_ORIG=""; MTU_SET=0
}
set_mtu(){
  dut "$DSUDO ip link set eth3 mtu $1; $DSUDO ip link set eth4 mtu $1"
  g1 "$NS ip link set $IF mtu $1"; g2 "$NS ip link set $IF mtu $1"
  sleep 4
}

suite_A1(){ # 64 B, 64 flows, both directions, NDR search
  echo "== A1 bidirectional 64 B NDR"
  local o=$OUTD/A1.csv; rm -f "$o"
  BIDIR=1 FLOWS=64 LOSS=$LOSS OUT=$o PK a2 port-v4 64 | tee "$OUTD/A1.log" | tail -n 1
  read -r best med mean dev loss hw <<<"$(a2row "$o")"
  rec A1 ndr_total_mpps "$(awk -v v="${mean:-0}" 'BEGIN{printf "%.3f", v/1e6}')" ge 4.80
  rec A1 loss_pct_at_ndr "${loss:-NA}" le "$LOSS"
  rec A1 hw_share "${hw:-NA}" info
  rec A1 run_to_run_dev_pct "${dev:-NA}" info
  A1_NDR=${mean:-0}
}
suite_A2(){ # IMIX, 512 flows
  echo "== A2 IMIX 7:4:1"
  local o=$OUTD/A2.csv; rm -f "$o"
  BIDIR=1 FLOWS=512 LOSS=$LOSS OUT=$o PK a2 vlan-v4 imix | tee "$OUTD/A2.log" | tail -n 1
  read -r best med mean dev loss hw <<<"$(a2row "$o")"
  local gbps; gbps=$(awk -v p="${mean:-0}" 'BEGIN{printf "%.2f", p/2*((7*64+4*570+1518)/12)*8/1e9}')   # L2 bits per direction
  rec A2 l2_gbps_per_direction "$gbps" ge 9.2
  rec A2 frame_loss_pct "${loss:-NA}" le 0
  rec A2 delivered_mpps_total "$(awk -v v="${mean:-0}" 'BEGIN{printf "%.3f", v/1e6}')" info
  rec A2 hw_share "${hw:-NA}" info
}
suite_A3(){ # jumbo frames: 9000 B, or the largest MTU of A3_MTUS that passes a DF ping end to end
  echo "== A3 jumbo frames"
  local o=$OUTD/A3.csv m ok="" sz
  rm -f "$o"
  for m in ${A3_MTUS:-9000 8000 6000 4000 3562 3000}; do
    MTU_SET=1; set_mtu "$m"
    if [ "$(g1 "$NS ping -M do -c 2 -W 2 -s $((m - 28)) -I $G1A $G2A >/dev/null 2>&1; echo \$?")" = 0 ]; then ok=$m; break; fi
    echo "  MTU $m: DF ping fails"
  done
  if [ -z "$ok" ]; then rec A3 df_ping_largest_mtu none; restore_ctl; return; fi
  sz=$ok
  rec A3 largest_working_mtu "$ok"
  BIDIR=1 FLOWS=64 LOSS=$LOSS OUT=$o PK a2 port-v4 "$sz" | tee "$OUTD/A3.log" | tail -n 1
  read -r best med mean dev loss hw <<<"$(a2row "$o")"
  local lr=$(awk -v s="$sz" 'BEGIN{printf "%d", 2*1e10/((s+20)*8)}')
  rec A3 delivered_pct_of_line "$(awk -v v="${mean:-0}" -v l=$lr 'BEGIN{printf "%.2f", 100*v/l}')"
  rec A3 loss_pct "${loss:-NA}"
  rec A3 delivered_kpps_total "$(awk -v v="${mean:-0}" 'BEGIN{printf "%.1f", v/1e3}')"
  rec A3 hw_share "${hw:-NA}"
  dut_ok; restore_ctl
}
suite_B1(){ # flow scale
  echo "== B1 flow-table scaling at 2.5 Mpps total"
  local n o dk
  for n in ${STEPS_B1:-1024 16384 65536 131072 262144}; do
    o=$OUTD/B1-$n.csv; rm -f "$o"; conntrack_room "$n"
    # the vendor learns flows from conntrack events at its own pace; give it time in proportion
    BIDIR=1 FLOWS=$n WARM_PARTIAL=1 WARM_MAX=${WARM_MAX_B1:-420} WARM_SECS=$(( n / 1000 > 20 ? (n / 1000 > 300 ? 300 : n / 1000) : 20 )) OUT=$o \
      PK trial port-v4 64 2500000 "$TRIAL" > "$OUTD/B1-$n.log" 2>&1 || echo "  trial $n exit $?"
    tail -n 2 "$OUTD/B1-$n.log"
    IFS=, read -r _ _ _ _ _ _ opps tx rx loss hw <<<"$(csvlast "$o")"
    local lim=info; [ "$n" = 65536 ] && lim=le
    rec B1 "loss_pct_flows_$n" "${loss:-NA}" "$lim" 0.005
    rec B1 "hw_share_flows_$n" "${hw:-NA}" info
    rec B1 "delivered_kpps_flows_$n" "$(awk -v v="${opps:-0}" 'BEGIN{printf "%.0f", v/1e3}')" info
    dut_ok
  done
  restore_ctl
}
suite_B2(){ # new connections per second over a 1.5 Mpps background
  echo "== B2 CPS ramp over 1.5 Mpps background"
  cps_serve_start; local rate o secs=15 best=0 bestfail=NA j
  for rate in ${RATES_B2:-5000 10000 25000 40000 60000}; do
    o=$OUTD/B2-$rate.csv; rm -f "$o"; j=$OUTD/B2-$rate.json
    BIDIR=1 FLOWS=1024 OUT=$o DURING_OUT=$j \
      DURING="ssh -n $SSHO $GEN1 \"$NS python3 /tmp/testrig-cps.py run $G1A $G2A $CPSPORT $rate $secs 0 8\"" \
      PK trial port-v4 64 1500000 $((secs + 8)) > "$OUTD/B2-$rate.log" 2>&1
    IFS=, read -r _ _ _ _ _ _ opps tx rx loss hw <<<"$(csvlast "$o")"
    local cps fail p99
    cps=$(jget cps < "$j"); fail=$(jget failed < "$j"); p99=$(jget connect_ms_p99 < "$j")
    printf '  rate=%-6s cps=%-6s failed=%-6s connect_p99=%sms background_loss=%s%% hw=%s\n' "$rate" "${cps:-?}" "${fail:-?}" "${p99:-?}" "${loss:-?}" "${hw:-?}"
    rec B2 "cps_at_request_$rate" "${cps:-NA}" info
    rec B2 "failed_at_request_$rate" "${fail:-NA}" info
    rec B2 "connect_p99_ms_at_$rate" "${p99:-NA}" info
    rec B2 "background_loss_pct_at_$rate" "${loss:-NA}" info
    if [ "${fail:-1}" = 0 ] && awk -v l="${loss:-1}" 'BEGIN{exit !(l<=0.005)}' && [ "${cps:-0}" -gt "$best" ]; then best=$cps; fi
    dut_ok
  done
  rec B2 sustained_cps_no_failure_no_bg_loss "$best" ge 25000
  cps_serve_stop
}
suite_B3(){ # teardown burst of 32k held flows
  echo "== B3 FIN burst of 32k held TCP flows at 2.0 Mpps"
  cps_serve_start; PORTS_ORIG=$(g1 "cat /proc/sys/net/ipv4/ip_local_port_range" | tr '\t' ' ')
  g1 "sudo sysctl -qw net.ipv4.ip_local_port_range='1100 65535'" >/dev/null
  conntrack_room 40000
  local lat="ssh -n $SSHO $GEN1 \"$NS python3 /tmp/testrig-latprobe.py probe $IF $G1A $G2A 20000 1000\"" secs=30 o
  refl_start
  o=$OUTD/B3-quiet.csv; rm -f "$o"
  BIDIR=1 FLOWS=1024 OUT=$o DURING_OUT=$OUTD/B3-quiet.json DURING="$lat" PK trial vlan-v4 64 2000000 $((secs - 0)) > "$OUTD/B3-quiet.log" 2>&1
  local ql qp9; IFS=, read -r _ _ _ _ _ _ _ _ _ ql _ <<<"$(csvlast "$o")"; qp9=$(jget half_rtt_us.p999 < "$OUTD/B3-quiet.json")
  o=$OUTD/B3-fin.csv; rm -f "$o"
  BIDIR=1 FLOWS=1024 OUT=$o DURING_OUT=$OUTD/B3-fin.json \
    DURING="( $lat > $OUTD/B3-fin-lat.json & ssh -n $SSHO $GEN1 \"$NS python3 /tmp/testrig-cps.py run $G1A $G2A $CPSPORT 200 12 32000 8\" ; wait )" \
    PK trial vlan-v4 64 2000000 $((secs - 0)) > "$OUTD/B3-fin.log" 2>&1
  local sl hw; IFS=, read -r _ _ _ _ _ _ _ _ _ sl hw <<<"$(csvlast "$o")"
  local sp9 opened alive; sp9=$(jget half_rtt_us.p999 < "$OUTD/B3-fin-lat.json"); opened=$(jget hold_opened < "$OUTD/B3-fin.json"); alive=$(jget hold_alive < "$OUTD/B3-fin.json")
  rec B3 background_loss_pct_quiet "${ql:-NA}" info
  rec B3 background_loss_pct_teardown "${sl:-NA}" le 0.005
  rec B3 held_flows_opened "${opened:-NA}" info
  rec B3 held_flows_alive_at_end "${alive:-NA}" ge "${opened:-32000}"
  rec B3 oneway_p999_us_quiet "${qp9:-NA}" info
  rec B3 oneway_p999_us_teardown "${sp9:-NA}" le "$(awk -v q="${qp9:-0}" 'BEGIN{printf "%.3f", 2*q}')"
  refl_stop; cps_serve_stop; restore_ctl; dut_ok
}
lat_run(){ # LABEL PPS SECS  -> one latency run under pktgen load (bidirectional, 256 flows, VLAN 10/20)
  local label=$1 pps=$2 secs=$3 o=$OUTD/$1.csv cnt=$(( ($3 - 12) * 1000 ))
  rm -f "$o"
  BIDIR=1 FLOWS=256 OUT=$o DURING_OUT=$OUTD/$label.json \
    DURING="ssh -n $SSHO $GEN1 \"$NS python3 /tmp/testrig-latprobe.py probe $IF $G1A $G2A $cnt 1000\"" \
    PK trial vlan-v4 64 "$pps" "$secs" > "$OUTD/$label.log" 2>&1
  LAT_LOSS=$(csvlast "$o" | awk -F, '{print $10}'); LAT_HW=$(csvlast "$o" | awk -F, '{print $11}')
}
suite_C1(){
  echo "== C1 latency distribution under load"
  refl_start
  local rates=${RATES_C1:-"1320000 2110000 2370000 2610000"} pct i=0 r
  local names=(50 80 90 99)
  for r in $rates; do
    pct=${names[$i]:-x}; i=$((i + 1))
    lat_run "C1-$pct" "$r" 40
    local j=$OUTD/C1-$pct.json
    printf '  %s%% (%s pps): %s\n' "$pct" "$r" "$(tail -n 1 "$j" 2>/dev/null | cut -c1-220)"
    for m in min mean p50 p95 p99 p999 max; do rec C1 "oneway_${m}_us_at_${pct}pct" "$(jget half_rtt_us.$m < "$j")" info; done
    rec C1 "jitter_p999_us_at_${pct}pct" "$(jget jitter_us.p999 < "$j")" "$([ "$pct" = 90 ] && echo le || echo info)" 2.0
    rec C1 "jitter_max_us_at_${pct}pct" "$(jget jitter_us.max < "$j")" info
    rec C1 "probe_lost_at_${pct}pct" "$(jget lost < "$j")" info
    rec C1 "background_loss_pct_at_${pct}pct" "${LAT_LOSS:-NA}" info
    [ "$pct" = 90 ] && rec C1 "oneway_p99_us_at_90pct_limit" "$(jget half_rtt_us.p99 < "$j")" le 12.5
    dut_ok
  done
  refl_stop
}
suite_C2(){
  echo "== C2 jitter profile, ${C2_SECS:-600} s at 2.0 Mpps"
  refl_start
  lat_run C2 2000000 "${C2_SECS:-600}"
  local j=$OUTD/C2.json
  for m in mean p50 p99 p999 max; do rec C2 "oneway_${m}_us" "$(jget half_rtt_us.$m < "$j")" info; done
  for m in mean p99 p999 max; do rec C2 "jitter_${m}_us" "$(jget jitter_us.$m < "$j")" info; done
  rec C2 probe_lost "$(jget lost < "$j")" info
  rec C2 background_loss_pct "${LAT_LOSS:-NA}" le 0.005
  refl_stop; dut_ok
}
suite_D1(){
  echo "== D1 VLAN (and QinQ) penalty at 64 B"
  local a=$OUTD/D1-untagged.csv v=$OUTD/D1-vlan.csv
  rm -f "$a" "$v"
  BIDIR=1 FLOWS=64 LOSS=$LOSS OUT=$a PK a2 port-v4 64 > "$OUTD/D1-untagged.log" 2>&1; tail -n 1 "$OUTD/D1-untagged.log"
  BIDIR=1 FLOWS=64 LOSS=$LOSS OUT=$v PK a2 vlan-v4 64 > "$OUTD/D1-vlan.log" 2>&1; tail -n 1 "$OUTD/D1-vlan.log"
  local am vm vh; am=$(a2row "$a" | awk '{print $3}'); read -r _ _ vm _ _ vh <<<"$(a2row "$v")"
  rec D1 untagged_ndr_mpps "$(awk -v x="${am:-0}" 'BEGIN{printf "%.3f", x/1e6}')" info
  rec D1 vlan_ndr_mpps "$(awk -v x="${vm:-0}" 'BEGIN{printf "%.3f", x/1e6}')" info
  rec D1 vlan_hw_share "${vh:-NA}" info
  rec D1 vlan_vs_untagged_pct "$(awk -v a="${am:-0}" -v v="${vm:-0}" 'BEGIN{printf "%.1f", a>0 ? 100*v/a : 0}')" ge 95
  if [ "${QINQ_READY:-0}" = 1 ]; then
    echo "  QinQ: QINQ_READY=1 set; the 802.1ad interfaces on both generators and the DUT must already exist (path qinq-v4 is not wired into pktgen yet)"
    rec D1 qinq status info
  else
    skip D1 "QinQ needs 802.1ad subinterfaces on the DUT and both generators (ASK2 plan G17 is not implemented); set up the rig and extend path_set with qinq-v4 before enabling"
  fi
  dut_ok
}
suite_D2(){
  echo "== D2 line-rate flood with 100 pps control-plane traffic to the router"
  local o=$OUTD/D2.csv n=2800 j=$OUTD/D2.out; rm -f "$o"
  BIDIR=1 FLOWS=1000 OUT=$o DURING_OUT=$j \
    DURING="( ssh -n $SSHO $GEN1 \"$NS ping -i 0.01 -c $n -W 1 -q $R1V | sed 's/^/P1 /'\" & ssh -n $SSHO $GEN2 \"$NS ping -i 0.01 -c $n -W 1 -q $R2V | sed 's/^/P2 /'\" & ssh -n $SSHO $GEN1 \"bash /tmp/testrig-sshprobe.sh $R1V 28\" & wait )" \
    PK trial vlan-v4 64 0 30 > "$OUTD/D2.log" 2>&1
  tail -n 3 "$OUTD/D2.log"; cat "$j"
  local p1 p2 s_bad lossr hw
  p1=$(sed -n 's/^P1 .* \([0-9.]*\)% packet loss.*/\1/p' "$j"); p2=$(sed -n 's/^P2 .* \([0-9.]*\)% packet loss.*/\1/p' "$j")
  s_bad=$(awk '/^SSH/{print $3}' "$j")
  IFS=, read -r _ _ _ _ _ _ opps tx rx lossr hw <<<"$(csvlast "$o")"
  rec D2 icmp_loss_pct_gen1 "${p1:-NA}" le 0
  rec D2 icmp_loss_pct_gen2 "${p2:-NA}" le 0
  rec D2 ssh_connect_failures "${s_bad:-NA}" le 0
  rec D2 flood_delivered_mpps "$(awk -v v="${opps:-0}" 'BEGIN{printf "%.3f", v/1e6}')" info
  rec D2 flood_loss_pct "${lossr:-NA}" info
  rec D2 flood_hw_share "${hw:-NA}" info
  dut_ok
}
suite_D3(){
  echo "== D3 IPsec ESP / CAAM coexistence"
  if [ -z "${IPSEC_DST:-}" ]; then
    skip D3 "no tunnel configured: set IPSEC_DST to an address behind an established AES-256-GCM tunnel (ASK2 ESP offload is plan G9, not implemented; the vendor needs its CDX/CAAM IPsec config)"
    return
  fi
  local j=$OUTD/D3.json
  g1 "iperf3 -c $IPSEC_DST -P 8 --bidir -t 30 -J" > "$j" 2>/dev/null
  rec D3 throughput_gbps_sum "$(python3 -c 'import json;j=json.load(open("'"$j"'"));print(round((j["end"]["sum_sent"]["bits_per_second"]+j["end"]["sum_received"]["bits_per_second"])/1e9,3))' 2>/dev/null)" info
}

gens_prep
dut_ok
dut "uname -r; $DSUDO sh -c 'cat /proc/sys/net/netfilter/nf_conntrack_max'" > "$OUTD/dut-info.txt" 2>&1
tests=("$@"); [ ${#tests[@]} -gt 0 ] || tests=(A1 A2 A3 B1 B2 B3 C1 C2 D1 D2 D3)
for t in "${tests[@]}"; do
  type "suite_$t" >/dev/null 2>&1 || { echo "unknown test $t"; continue; }
  "suite_$t"
done
echo "== results: $RES"
column -s, -t "$RES" 2>/dev/null | cut -c1-160

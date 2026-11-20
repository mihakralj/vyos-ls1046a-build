#!/usr/bin/env bash
# Quick per-offload throughput check: one DUR-second run per cell (default 30 s),
# iperf3 -Z, duration-weighted complete intervals in [STEADY_FROM, MEASURE_TO].
# Client forward is sender rate; reverse is receiver rate (bidir adds these).
# Cells: unicast, NAT, VLAN-VLAN, PPPoE (down = decap, up = encap), IPv4 and IPv6.
# The `-rev` cells run the same path dell2 -> dell1 (in eth4, out eth3) to check both ports separately.
# Each cell also records an offload proof so a software-forwarded run cannot pass:
# the per-record pkt_count growth in fe_ehash_stats over the steady window, compared
# with an estimate of data frames from iperf3's reported TCP MSS. ACKs and unequal
# bidirectional MSS values can make this ratio approximate.
#
# Rig: gen1 "dell1" (GEN_IF, .112) <-> DUT eth3, gen2 "dell2" (GEN_IF, .113) <-> DUT eth4
# (persistent config: bin/testrig-dell-net.sh). Hosts, key and paths: bin/testrig-env.sh.
# NAT cells use runtime nft tables (table ask2q) masquerading out of eth4; the VyOS
# NAT66 rule is removed for the run and restored on exit. Nothing persistent changes.
#
# Combo cells (opt-in, not in the default run; name them or pass `combo`):
#   nat-pppoe-up-v4  NAT44 masquerade on pppoe10, LAN -> PPPoE (encap + NAT, decap replies)
#   nat-vlan-v4      NAT44 masquerade on eth4.20, VLAN 10 -> VLAN 20
#   nat-vlan-v6      NAT66 masquerade on eth4.20, VLAN 10 -> VLAN 20
# WAN cells (opt-in, name them or pass `wan`; need the environment bin/testrig-ask2-wan.sh sets up):
#   wanfull-*  LAN (dell2) <-> WAN (dell1) over PPPoE on VLAN 7 (pppoe7 on eth3.7), NAT44 out of pppoe7,
#              IPv6 routed over the same session
#   portfwd-*  destination NAT on the WAN port: dell1 connects to the DUT's eth3 address, forwarded to dell2
#   wgwan-*    LAN NAT44 into WireGuard wgw0 whose outer packets ride pppoe7 on VLAN 7; IPv6 inner routed
# A client field ending in R runs the iperf3 client with -R (data from server to client): the download
# of a NAT44 row, where the WAN side cannot open the connection.
#
# PPPoE cells enable RPS on dell1's DUT-facing NIC for the run (see rps_set); other cells leave it off.
# Usage: bin/testrig-offload-quick.sh [cell ...]       (default: the 10 base cells)
# Env:   DUT_KIND DUT DUR STEADY_FROM MEASURE_TO STREAMS OUT BIDIR J SAMPLE RUN_ON OUTDIR IPERF_ARGS IPERF_PORT
#        MEASURE_TO defaults to DUR-1 (one-second terminal reporting guard).
#        PPPOE_SERVER4 PPPOE_SERVER6 (defaults preserve the shared test service)
#        RIG_EXTERNAL_NAT_TOKEN (vendor WAN: verify, but do not modify, the owner's NAT table)
#        RIG_PRESERVE_RPS=1 leaves generator RPS settings untouched
#        QUICK_G{1,2}_IPV{4,6}, QUICK_G{1,2}_VLAN{4,6} override routed test endpoints
#        RIG_VERIFY_PORTS=1 requires traffic on both vendor DUT test ports
#        IPERF_TIMEOUT (default DUR+45); DUT_TRACE=1 captures per-second ASK2 FE/MAC counters.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$(dirname "$0")/testrig-env.sh"
if [ "${RIG_PARITY:-0}" = 1 ]; then . "$HERE/testrig-parity-profile.sh" || exit 2; fi
DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}; STREAMS=${STREAMS:-8}
MEASURE_TO=${MEASURE_TO:-$((DUR-1))}
IPERF_TIMEOUT=${IPERF_TIMEOUT:-$((DUR+45))}
if ! [[ $IPERF_TIMEOUT =~ ^[1-9][0-9]{0,6}$ ]] || (( IPERF_TIMEOUT <= DUR )); then
  echo "IPERF_TIMEOUT must be an integer greater than DUR" >&2; exit 2
fi
IPERF_PORT=${IPERF_PORT:-5201}
if ! [[ $IPERF_PORT =~ ^[1-9][0-9]{0,4}$ ]] || (( IPERF_PORT > 65535 )); then
  echo "IPERF_PORT must be an integer from 1 to 65535" >&2; exit 2
fi
case ${RIG_PRESERVE_RPS:-0} in 0|1) ;; *) echo "RIG_PRESERVE_RPS must be 0 or 1" >&2; exit 2;; esac
case ${RIG_VERIFY_PORTS:-0} in 0|1) ;; *) echo "RIG_VERIFY_PORTS must be 0 or 1" >&2; exit 2;; esac
# BIDIR=1: one client with iperf3 --bidir (both directions' streams connect before data flows);
# Gbit/s and retransmits are the sum of both directions.
BIDIR=${BIDIR:-0}; BIDIR_ARG=""; [ "$BIDIR" = 1 ] && BIDIR_ARG="--bidir"
# RUN_ON=gen2 (alias dell2): the script runs on gen2: gen2 commands run locally, gen1 and the DUT go
# over ssh. Default: a separate controller reaches all three over ssh.
RUN_ON=${RUN_ON:-agent}
OUTDIR=${OUTDIR:-$RIG_OUT}
OUT=${OUT:-$OUTDIR/quick-$(date +%Y%m%d-%H%M).csv}
J=${J:-$OUTDIR/json-quick}; mkdir -p "$J"
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
# DUT_KIND=ask2 (VyOS: sudo, HW proof = fe_ehash_stats record hits) or vendor (OpenWrt
# vendor ASK: root, no sudo/bash, HW proof = frames that never reach the kernel: ethtool -S
# 'rx packets [TOTAL]', as baseline3.sh PROOF=kernelrx; the netdev rx_packets counts fast-path frames too).
# DUT_KIND=opnsense (OPNsense/FreeBSD vendor ASK port: root, pf+CMM): CMM offloads pf states,
# so the OPNsense filter must be enabled with pass rules on the test interfaces and the local
# plugin /usr/local/etc/inc/plugins.inc.d/testrig.inc providing nat-anchor "testrig"; NAT cells
# load that anchor at runtime and flush it on exit.
# HW proof = packets that never reached the FreeBSD IP stack (netstat -s ip/ip6 "total packets
# received"; the dtsec ifnet counters include fast-path frames). CPU from kern.cp_time, where
# interrupt time is reported in the softirq column.
# RIG_NS=$VENDOR_NS runs the generator side inside that netns (the ports facing the second DUT).
DUT_KIND=${DUT_KIND:-ask2}; RIG_NS=${RIG_NS:-}
RIG_EXTERNAL_NAT_TOKEN=${RIG_EXTERNAL_NAT_TOKEN:-}
if [ -n "$RIG_EXTERNAL_NAT_TOKEN" ] &&
   { [ "$DUT_KIND" != vendor ] || ! [[ $RIG_EXTERNAL_NAT_TOKEN =~ ^[0-9a-f]{32}$ ]]; }; then
  echo "RIG_EXTERNAL_NAT_TOKEN requires a vendor DUT and a 32-digit hexadecimal owner token" >&2
  exit 2
fi
PPPOE_SERVER4=${PPPOE_SERVER4:-10.99.50.1}
PPPOE_SERVER6=${PPPOE_SERVER6:-fd99:50::1}
if ! python3 - "$PPPOE_SERVER4" "$PPPOE_SERVER6" <<'PY'
import ipaddress, sys
try:
    versions = [ipaddress.ip_address(value).version for value in sys.argv[1:]]
except ValueError:
    raise SystemExit(1)
if versions != [4, 6] or any("%" in value for value in sys.argv[1:]):
    raise SystemExit(1)
PY
then
  echo "PPPOE_SERVER4 and PPPOE_SERVER6 must be valid unscoped IPv4/IPv6 addresses" >&2
  exit 2
fi
DUT=${DUT:-$(rig_dut "$DUT_KIND")} || exit 2
rig_require GEN1; [ "$RUN_ON" = dell2 ] || rig_require GEN2
NSX=""; [ -n "$RIG_NS" ] && NSX="sudo ip netns exec $RIG_NS"
dut(){ local c=$1; [ "$DUT_KIND" != ask2 ] && c=${c//sudo /}; ssh -n $SSHO $RIG_SSHK $DUT "$c"; }
S=dut
SI="ssh $SSHO $RIG_SSHK $DUT"
D1="ssh -n $SSHO $RIG_SSHK $GEN1"; D2="ssh -n $SSHO $RIG_SSHK ${GEN2:-}"
[ "$RUN_ON" = dell2 ] && D2="bash -c"

QUICK_G1_IPV4=${QUICK_G1_IPV4:-10.99.1.112}
QUICK_G2_IPV4=${QUICK_G2_IPV4:-10.99.2.113}
QUICK_G1_IPV6=${QUICK_G1_IPV6:-fd99:1::112}
QUICK_G2_IPV6=${QUICK_G2_IPV6:-fd99:2::113}
QUICK_G1_VLAN4=${QUICK_G1_VLAN4:-10.99.10.112}
QUICK_G2_VLAN4=${QUICK_G2_VLAN4:-10.99.20.113}
QUICK_G1_VLAN6=${QUICK_G1_VLAN6:-fd99:10::112}
QUICK_G2_VLAN6=${QUICK_G2_VLAN6:-fd99:20::113}
if ! python3 - "$QUICK_G1_IPV4" "$QUICK_G2_IPV4" "$QUICK_G1_IPV6" "$QUICK_G2_IPV6" \
  "$QUICK_G1_VLAN4" "$QUICK_G2_VLAN4" "$QUICK_G1_VLAN6" "$QUICK_G2_VLAN6" <<'PY'
import ipaddress, sys
try:
    versions = [ipaddress.ip_address(value).version for value in sys.argv[1:]]
except ValueError:
    raise SystemExit(1)
if versions != [4, 4, 6, 6, 4, 4, 6, 6] or any("%" in value for value in sys.argv[1:]):
    raise SystemExit(1)
PY
then
  echo "ERROR: invalid routed generator endpoint address" >&2
  exit 2
fi

# name|nat|client|bind(source) address|server address
CELLS_ALL="
unicast-v4|none|d1|$QUICK_G1_IPV4|$QUICK_G2_IPV4
unicast-v6|none|d1|$QUICK_G1_IPV6|$QUICK_G2_IPV6
nat-v4|4|d1|$QUICK_G1_IPV4|$QUICK_G2_IPV4
nat-v6|6|d1|$QUICK_G1_IPV6|$QUICK_G2_IPV6
vlan-v4|none|d1|$QUICK_G1_VLAN4|$QUICK_G2_VLAN4
vlan-v6|none|d1|$QUICK_G1_VLAN6|$QUICK_G2_VLAN6
unicast-v4-rev|none|d2|$QUICK_G2_IPV4|$QUICK_G1_IPV4
unicast-v6-rev|none|d2|$QUICK_G2_IPV6|$QUICK_G1_IPV6
nat-v4-rev|4r|d2|$QUICK_G2_IPV4|$QUICK_G1_IPV4
nat-v6-rev|6r|d2|$QUICK_G2_IPV6|$QUICK_G1_IPV6
vlan-v4-rev|none|d2|$QUICK_G2_VLAN4|$QUICK_G1_VLAN4
vlan-v6-rev|none|d2|$QUICK_G2_VLAN6|$QUICK_G1_VLAN6
vlanport-v4|none|d1|$QUICK_G1_VLAN4|$QUICK_G2_IPV4
portvlan-v4|none|d2|$QUICK_G2_IPV4|$QUICK_G1_VLAN4
vlanport-v6|none|d1|$QUICK_G1_VLAN6|$QUICK_G2_IPV6
portvlan-v6|none|d2|$QUICK_G2_IPV6|$QUICK_G1_VLAN6
pppoe-down-v4|none|d1|$PPPOE_SERVER4|10.99.2.113
pppoe-up-v4|none|d2|10.99.2.113|$PPPOE_SERVER4
pppoe-down-v6|none|d1|$PPPOE_SERVER6|fd99:2::113
pppoe-up-v6|none|d2|fd99:2::113|$PPPOE_SERVER6
"
# opt-in combo cells: only run when named (or via `combo`)
CELLS_COMBO="
nat-pppoe-up-v4|4p|d2|10.99.2.113|10.99.50.1
nat-vlan-v4|4v|d1|10.99.10.112|10.99.20.113
nat-vlan-v6|6v|d1|fd99:10::112|fd99:20::113
"
CELLS_WAN="
wanfull-up-v4|4w|d2|10.99.2.113|10.99.57.1
wanfull-down-v4|4w|d2R|10.99.2.113|10.99.57.1
wanfull-up-v6|none|d2|fd99:2::113|fd99:57::1
wanfull-down-v6|none|d1|fd99:57::1|fd99:2::113
portfwd-up-v4|dnat4|d1R|10.99.1.112|10.99.1.185
portfwd-down-v4|dnat4|d1|10.99.1.112|10.99.1.185
portfwd-up-v6|dnat6|d1R|fd99:1::112|fd99:1::185
portfwd-down-v6|dnat6|d1|fd99:1::112|fd99:1::185
wgwan-up-v4|4wg|d2|10.99.2.113|10.99.230.112
wgwan-down-v4|4wg|d2R|10.99.2.113|10.99.230.112
wgwan-up-v6|none|d2|fd99:2::113|fd99:230::112
wgwan-down-v6|none|d2R|fd99:2::113|fd99:230::112
"
WANT="$*"
case " $WANT " in *" combo "*) WANT="$WANT nat-pppoe-up-v4 nat-vlan-v4 nat-vlan-v6" ;; esac
case " $WANT " in *" wan "*) WANT="$WANT $(echo "$CELLS_WAN" | cut -d'|' -f1 | tr '\n' ' ')" ;; esac
CELLS_RUN="$CELLS_ALL"
[ -n "$WANT" ] && CELLS_RUN="$CELLS_ALL $CELLS_COMBO $CELLS_WAN"

NAT66_RULE='oifname "eth4" ip6 saddr fd99:1::/64 counter masquerade comment "SRC-NAT66-100"'
vyos_nat66_present=0
if [ "$DUT_KIND" = ask2 ]; then
  $S 'sudo nft list chain ip6 vyos_nat POSTROUTING 2>/dev/null' | grep -q SRC-NAT66-100 && vyos_nat66_present=1
fi

pf_nat(){ $S "printf '%s\\n' '$1' | pfctl -q -a testrig -f -"; }
nat_clear(){
  [ -z "$RIG_EXTERNAL_NAT_TOKEN" ] || return 0
  if [ "$DUT_KIND" = opnsense ]; then $S 'pfctl -q -a testrig -F nat 2>/dev/null; true'; return; fi
  $S 'sudo nft delete table ip ask2q 2>/dev/null; sudo nft delete table ip6 ask2q 2>/dev/null; true'; }
nat_set(){
  if [ -n "$RIG_EXTERNAL_NAT_TOKEN" ]; then
    local listing rule iface suffix family=ip source=${2:-} dest=${3:-} owner
    owner=${RIG_EXTERNAL_NAT_OWNER:-dut2-wan-$RIG_EXTERNAL_NAT_TOKEN}
    case $1 in
      none) suffix= ;;
      4w) iface=pppoe7; suffix=ppp ;;
      4wg) iface=wgw0; suffix=wg ;;
      dnat4) suffix=dnat4 ;;
      dnat6) suffix=dnat6; family=ip6 ;;
      4) iface=eth4; suffix=nat4up ;;
      4r) iface=eth3; suffix=nat4down ;;
      6) iface=eth4; suffix=nat6up; family=ip6 ;;
      6r) iface=eth3; suffix=nat6down; family=ip6 ;;
      *) echo "ERROR: external NAT does not support mode $1" >&2; return 1 ;;
    esac
    listing=$($S "nft -a list table $family vwn$RIG_EXTERNAL_NAT_TOKEN") || {
      echo "ERROR: external NAT table is unavailable" >&2; return 1;
    }
    printf '%s\n' "$listing" | grep -Fq "comment \"$owner\"" || {
      echo "ERROR: external NAT table ownership changed" >&2; return 1;
    }
    if [ -n "$suffix" ]; then
      rule=$(printf '%s\n' "$listing" | grep -F "comment \"$owner-$suffix\"") || return 1
      [ "$(printf '%s\n' "$rule" | wc -l)" -eq 1 ] &&
        [ -n "$source" ] && [ -n "$dest" ] || {
          echo "ERROR: external NAT rule $suffix is incomplete" >&2; return 1;
        }
      if [ "$suffix" = dnat4 ] || [ "$suffix" = dnat6 ]; then
        printf '%s\n' "$rule" | awk -v dst="$dest" -v family="$family" -v token="$suffix" '
          { for (i=1; i<=NF; i++) {
              if ($i=="iifname" && $(i+1)=="\"eth3\"") found_if=1
              if ($i==family && $(i+1)=="daddr" && $(i+2)==dst) found_dst=1
              if ($i=="tcp" && $(i+1)=="dport" && $(i+2)=="5201") found_port=1
              if ($i=="dnat" && $(i+1)=="to" &&
                  ((token=="dnat4" && $(i+2)=="10.99.2.113") ||
                   (token=="dnat6" && $(i+2)=="fd99:2::113"))) found_nat=1
            } }
          END { exit !(found_if && found_dst && found_port && found_nat) }
        ' || {
          echo "ERROR: external DNAT rule $suffix changed" >&2; return 1;
        }
      else
        printf '%s\n' "$rule" | awk -v iface="$iface" -v src="$source" -v dst="$dest" -v family="$family" '
          { for (i=1; i<=NF; i++) {
              if ($i=="oifname" && $(i+1)=="\"" iface "\"") found_if=1
              if ($i==family && $(i+1)=="saddr" && $(i+2)==src) found_src=1
              if ($i==family && $(i+1)=="daddr" && $(i+2)==dst) found_dst=1
              if ($i=="masquerade") found_nat=1
            } }
          END { exit !(found_if && found_src && found_dst && found_nat) }
        ' || {
          echo "ERROR: external NAT rule $suffix changed" >&2; return 1;
        }
      fi
    fi
    return 0
  fi
  local routed_source=${2:-}
  case "$1:$routed_source" in
    4:10.99.1.112) routed_source=10.99.1.0/24 ;;
    4r:10.99.2.113) routed_source=10.99.2.0/24 ;;
    6:fd99:1::112) routed_source=fd99:1::/64 ;;
    6r:fd99:2::113) routed_source=fd99:2::/64 ;;
  esac
  case "$1" in
    4|4r|6|6r) [ -n "$routed_source" ] || { echo "ERROR: routed NAT source is missing" >&2; return 1; } ;;
  esac
  nat_clear
  if [ "$DUT_KIND" = opnsense ]; then
    case $1 in
      none) ;;
      4) pf_nat "nat on dtsec4 inet from $routed_source to any -> 10.99.2.185" ;;
      4r) pf_nat "nat on dtsec3 inet from $routed_source to any -> 10.99.1.185" ;;
      4v) pf_nat 'nat on vlan02 inet from 10.99.10.0/24 to any -> 10.99.20.185' ;;
      6) pf_nat "nat on dtsec4 inet6 from $routed_source to any -> fd99:2::185" ;;
      6r) pf_nat "nat on dtsec3 inet6 from $routed_source to any -> fd99:1::185" ;;
      6v) pf_nat 'nat on vlan02 inet6 from fd99:10::/64 to any -> fd99:20::185' ;;
      *) echo "NAT mode $1 not supported on opnsense" >&2; return 1 ;;
    esac
    return
  fi
  case $1 in
    4) $S "sudo nft add table ip ask2q && sudo nft \"add chain ip ask2q post { type nat hook postrouting priority srcnat ; }\" && sudo nft add rule ip ask2q post oifname eth4 ip saddr $routed_source masquerade" ;;
    4p) $S 'sudo nft add table ip ask2q && sudo nft "add chain ip ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip ask2q post oifname pppoe10 ip saddr 10.99.2.0/24 masquerade' ;;
    4v) $S 'sudo nft add table ip ask2q && sudo nft "add chain ip ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip ask2q post oifname eth4.20 ip saddr 10.99.10.0/24 masquerade' ;;
    4r) $S "sudo nft add table ip ask2q && sudo nft \"add chain ip ask2q post { type nat hook postrouting priority srcnat ; }\" && sudo nft add rule ip ask2q post oifname eth3 ip saddr $routed_source masquerade" ;;
    6r) $S "sudo nft add table ip6 ask2q && sudo nft \"add chain ip6 ask2q post { type nat hook postrouting priority srcnat ; }\" && sudo nft add rule ip6 ask2q post oifname eth3 ip6 saddr $routed_source masquerade" ;;
    6v) $S 'sudo nft add table ip6 ask2q && sudo nft "add chain ip6 ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip6 ask2q post oifname eth4.20 ip6 saddr fd99:10::/64 masquerade' ;;
    6) $S "sudo nft add table ip6 ask2q && sudo nft \"add chain ip6 ask2q post { type nat hook postrouting priority srcnat ; }\" && sudo nft add rule ip6 ask2q post oifname eth4 ip6 saddr $routed_source masquerade" ;;
    4w) $S 'sudo nft add table ip ask2q && sudo nft "add chain ip ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip ask2q post oifname pppoe7 ip saddr 10.99.2.0/24 masquerade' ;;
    4wg) $S 'sudo nft add table ip ask2q && sudo nft "add chain ip ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip ask2q post oifname wgw0 ip saddr 10.99.2.0/24 masquerade' ;;
    dnat4) $S "sudo nft add table ip ask2q && sudo nft \"add chain ip ask2q pre { type nat hook prerouting priority dstnat ; }\" && sudo nft add rule ip ask2q pre iifname eth3 ip daddr 10.99.1.185 tcp dport $IPERF_PORT dnat to 10.99.2.113" ;;
    dnat6) $S "sudo nft add table ip6 ask2q && sudo nft \"add chain ip6 ask2q pre { type nat hook prerouting priority dstnat ; }\" && sudo nft add rule ip6 ask2q pre iifname eth3 ip6 daddr fd99:1::185 tcp dport $IPERF_PORT dnat to fd99:2::113" ;;
  esac
}
# dell1's mlx4 NIC cannot RSS-hash PPPoE frames, so every returning ACK lands on RX queue 0 / CPU0
# and the whole TCP send path runs in that one softirq (about 5.9 Gbit/s ceiling, DUT idle).
# RPS hashes on the inner flow instead. Runtime-only; applied for every cell with PPPoE on dell1's side
# (pppoe*, wanfull*, wgwan*), restored on exit.
D1_IF=${D1_IF:-$GEN_IF}
[ -n "$RIG_NS" ] && D1_IF=${D1_IF_NS:-$GEN_IF_NS}   # the gen1 port facing the second DUT lives in the netns; sysfs shows it only from inside
rps_set(){
  [ "${RIG_PRESERVE_RPS:-0}" != 1 ] || return 0
  local m=0
  [ "$1" = on ] && m='$(printf %x $(( (1<<$(nproc)) - 1 )))'
  if [ -n "$RIG_NS" ]; then
    $D1 "sudo ip netns exec $RIG_NS sh -c 'm=$m; for q in /sys/class/net/$D1_IF/queues/rx-*/rps_cpus; do echo \$m > \$q; done'"
  else
    $D1 "m=$m; for q in /sys/class/net/$D1_IF/queues/rx-*/rps_cpus; do echo \$m | sudo -n tee \$q >/dev/null; done"
  fi
}
cleanup(){
  rps_set off 2>/dev/null
  nat_clear
  if [ $vyos_nat66_present = 1 ]; then
    $S "sudo nft list chain ip6 vyos_nat POSTROUTING | grep -q SRC-NAT66-100 || sudo nft add rule ip6 vyos_nat POSTROUTING $NAT66_RULE"
  fi
}
trap cleanup EXIT
if [ $vyos_nat66_present = 1 ]; then
  $S 'h=$(sudo nft -a list chain ip6 vyos_nat POSTROUTING | sed -n "s/.*SRC-NAT66-100.*# handle \([0-9]*\)/\1/p"); [ -n "$h" ] && sudo nft delete rule ip6 vyos_nat POSTROUTING handle $h'
fi

parse(){ PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" python3 - "$1" "$STEADY_FROM" "$DUR" "${MEASURE_TO:-$((DUR-1))}" "$STREAMS" "$BIDIR" "${REV:-}" <<'PY'
import json,sys
from testrig_iperf import measure_tcp, report_json
try:
    with open(sys.argv[1]) as source:
        j=json.load(source)
    report=measure_tcp(j, float(sys.argv[3]), float(sys.argv[2]), float(sys.argv[4]),
                       direction="bidir" if sys.argv[6]=="1" else "down" if sys.argv[7]=="-R" else "up",
                       streams=int(sys.argv[5]))
except (OSError,ValueError,TypeError,KeyError) as exc:
    report_json(sys.argv[1], {"valid":False, "error":str(exc)})
    sys.exit(str(exc))
report_json(sys.argv[1], report)
g=report["gbps"]; bytes_=report["bytes"]; mss=report["tcp_mss"]
e=j.get('end',{}); c=e.get('cpu_utilization_percent',{})
r=e.get('sum_sent',{}).get('retransmits')
if r is None: r=sum(i['sum'].get('retransmits',0) for i in j.get('intervals',[]))
r+=e.get('sum_sent_bidir_reverse',{}).get('retransmits',0) or 0
print(f"{g:.3f} {c.get('host_total',0):.0f} {r} {bytes_:.0f} {mss}")
PY
}

verify_vendor_ports(){
  [ "$DUT_KIND" = vendor ] && [ "${RIG_VERIFY_PORTS:-0}" = 1 ] || return 0
  awk '
    /^cpu / {sec++; next}
    /^P (eth3|eth4) [0-9]+$/ {seen[sec,$2]++; value[sec,$2]=$3}
    END {exit !(sec==2 && seen[1,"eth3"]==1 && seen[1,"eth4"]==1 &&
                seen[2,"eth3"]==1 && seen[2,"eth4"]==1 &&
                value[2,"eth3"]>value[1,"eth3"] && value[2,"eth4"]>value[1,"eth4"])}
  ' "$1"
}

echo "ts,cell,gbps,dut_busy,dut_softirq,gen_cpu,retrans,hw_pkts,data_pkts_est,hw_ratio,verdict" > "$OUT"
printf '%-15s %7s %6s %6s %6s %8s %12s %6s  %s\n' cell Gbit/s busy% sirq% gen% retrans hw_pkts ratio verdict

for line in $CELLS_RUN; do
  IFS='|' read -r name nat cli a b <<<"$line"
  if [ -n "$WANT" ] && ! [[ " $WANT " == *" $name "* ]]; then continue; fi
  case $cli in d1*) G="$D1" ;; *) G="$D2" ;; esac
  REV=""; [[ $cli == *R ]] && REV="-R"
  # NAT first: the path check must see the cell's own translation (wgwan v4 only passes dell1's
  # allowed-ips once masqueraded; without it the check sees whatever the previous cell left)
  if ! nat_set "$nat" "$a" "$b" >/dev/null; then
    echo "ERROR: NAT setup failed for $name" >&2
    exit 1
  fi
  if ! $G "$NSX ping -c2 -W2 -i 0.3 -I $a $b >/dev/null 2>&1"; then
    printf '%-15s %s\n' "$name" "SKIP (no path $a -> $b)"; echo "$(date +%H:%M:%S),$name,0,0,0,0,0,0,0,0,SKIP" >> "$OUT"; continue
  fi
  case $name in *pppoe*|wanfull*|wgwan*) rps_set on ;; *) rps_set off ;; esac
  tag="$name"; [ "$BIDIR" = 1 ] && tag="$name-bidir"
  statfile="$J/$tag.stat"
  if [ "$DUT_KIND" = opnsense ]; then
  if ! $S 'pfctl -si | grep -q "Status: Enabled" && pfctl -sn | grep -q "nat-anchor \"testrig\"" && pfctl -sr | grep -q "pass in quick on dtsec3 inet6"'; then
    echo "ERROR: OPNsense pf with testrig rules/anchor is not active on the DUT" >&2
    exit 1
  fi
  $SI sh -s -- $STEADY_FROM $((DUR-STEADY_FROM-2)) > "$statfile" <<'EOS' &
sf=$1; w=$2
cpu(){ set -- $(sysctl -n kern.cp_time); echo "cpu $1 $2 $3 $5 0 0 $4 0 0 0"; }   # user nice sys idle iowait irq softirq(=intr)
rx(){ netstat -s -p ip | awk '/total packets received/{print "K ip", $1}'; netstat -s -p ip6 | awk '/total packets received/{print "K ip6", $1}'; }
sleep $sf; cpu; rx; sleep $w; cpu; rx
EOS
  elif [ "$DUT_KIND" = vendor ]; then
  $SI sh -s -- $STEADY_FROM $((DUR-STEADY_FROM-2)) > "$statfile" <<'EOS' &
sf=$1; w=$2
rx(){ for i in eth3 eth4; do ethtool -S $i | awk -v i=$i '/rx packets \[TOTAL\]/{print "K", i, $NF}'; echo "P $i $(cat /sys/class/net/$i/statistics/rx_packets)"; done; }   # K: frames handed to the kernel
sleep $sf; head -1 /proc/stat; rx; sleep $w; head -1 /proc/stat; rx
EOS
  else
  $SI bash -s -- $STEADY_FROM $((DUR-STEADY_FROM-2)) > "$statfile" <<'EOS' &
sf=$1; w=$2
hits(){ sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats | sed -n 's/.*record_dma=\(0x[0-9a-f]*\).*pkt_count=\([0-9]*\).*/E \1 \2/p'; }
sleep $sf; head -1 /proc/stat; hits; sleep $w; head -1 /proc/stat; hits
EOS
  fi
  sp=$!
  trace=""
  if [ "${DUT_TRACE:-0}" = 1 ] && [ "$DUT_KIND" = ask2 ]; then
  $SI bash -s -- "$DUR" > "$J/$tag.duttrace.txt" <<'EOS' &
dur=$1
snap(){
  echo "T $(date +%s.%N)"
  for i in eth3 eth4; do
    sudo ethtool -S "$i" | awk -v i="$i" '
      /rx packets \[TOTAL\]|tx packets \[TOTAL\]|tx confirm \[TOTAL\]|rx error \[TOTAL\]|rx dropped \[TOTAL\]|tx dropped \[TOTAL\]|rx dma error|rx frame physical error|rx frame size error|qman fq tdrop|qman cg_tdrop/ {
        key = $0; sub(/:.*/, "", key); gsub(/^[ \t]+/, "", key)
        print "M", i, key, $NF
      }'
  done
  sudo python3 - <<'PY'
import mmap, os, struct
fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
mm = mmap.mmap(fd, 0x100000, mmap.MAP_SHARED, mmap.PROT_READ, offset=0x1a00000)
words = memoryview(mm).cast("I")
for dev, base in (("eth3", 0xf0000), ("eth4", 0xf2000)):
    for name, off in (("rfrm", 0x120), ("rerr", 0x138), ("rdrp", 0x158)):
        raw = words[(base + off) // 4]
        value = struct.unpack(">I", struct.pack("=I", raw))[0]
        print("R", dev, name, value)
PY
  sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats 2>/dev/null |
    sed -n 's/.*record_dma=\(0x[0-9a-f]*\).*pkt_count=\([0-9]*\).*/E \1 \2/p'
}
n=0
while [ "$n" -le "$dur" ]; do
  snap
  [ "$n" -eq "$dur" ] && break
  sleep 1
  n=$((n+1))
done
EOS
  trace=$!
  fi
  # SAMPLE=1 (ASK2 only): per-second DUT sampler -> $J/$tag.dut.txt, lines
  # "S <epoch> <dport-5201 conntrack entries> <HW_OFFLOAD> <software [OFFLOAD]> <cpu fields user..softirq>".
  # Entries with neither flag are not in the flowtable at all (kernel path).
  smp=""
  if [ "${SAMPLE:-0}" = 1 ] && [ "$DUT_KIND" != vendor ]; then
  $SI bash -s -- $DUR > "$J/$tag.dut.txt" <<'EOS' &
for s in $(seq 0 $1); do
  c=$(sudo cat /proc/net/nf_conntrack | awk '/dport=5201/{n++; if($0~/HW_OFFLOAD/)h++; else if($0~/OFFLOAD/)o++} END{printf "%d %d %d",n+0,h+0,o+0}')
  echo "S $(date +%s.%N) $c $(head -1 /proc/stat | cut -d' ' -f3-9)"
  sleep 1
done
EOS
  smp=$!
  fi
  $G "$NSX timeout --signal=TERM --kill-after=5s ${IPERF_TIMEOUT}s iperf3 -c $b -B $a -p $IPERF_PORT -P $STREAMS -t $DUR -i 1 -Z $REV $BIDIR_ARG ${IPERF_ARGS:-} -J" > "$J/$tag.json" &
  p1=$!; client_status=0; wait "$p1" || client_status=$?
  sampler_status=0; wait "$sp" || sampler_status=$?
  [ -z "$smp" ] || wait "$smp" || true
  [ -z "$trace" ] || wait "$trace" || true
  sleep 1
  if [ "$client_status" -ne 0 ]; then
    echo "ERROR: iperf client exited with status $client_status for $tag" >&2
    exit 1
  fi
  if [ "$sampler_status" -ne 0 ]; then
    echo "ERROR: DUT sampler exited with status $sampler_status for $tag" >&2
    exit 1
  fi
  if [ "$DUT_KIND" = vendor ] && ! awk '
    /^cpu / {sec++; next}
    /^K (eth3|eth4) [0-9]+$/ {seen[sec,$2]++; value[sec,$2]=$3}
    END {exit !(sec==2 && seen[1,"eth3"]==1 && seen[1,"eth4"]==1 &&
                seen[2,"eth3"]==1 && seen[2,"eth4"]==1 &&
                value[2,"eth3"]>=value[1,"eth3"] && value[2,"eth4"]>=value[1,"eth4"])}' "$statfile"; then
    echo "ERROR: incomplete or invalid vendor counter samples for $tag" >&2
    exit 1
  fi
  if ! verify_vendor_ports "$statfile"; then
    echo "ERROR: DUT test-port counters did not confirm both directions for $tag" >&2
    exit 1
  fi
  if ! read gbps gen rt bytes mss < <(parse "$J/$tag.json"); then
    echo "ERROR: invalid iperf3 result for $tag" >&2
    exit 1
  fi
  if [ -n "$RIG_EXTERNAL_NAT_TOKEN" ] && ! nat_set "$nat" "$a" "$b" >/dev/null; then
    echo "ERROR: external NAT changed during $tag" >&2
    exit 1
  fi
  win=$((DUR-STEADY_FROM-2))
  read busy sirq < <(awk '!/^cpu /{next} {k++} k==1{for(i=2;i<=11;i++)a[i]=$i} k==2{for(i=2;i<=11;i++){d=$i-a[i];T+=d; if(i==5||i==6)idle+=d; if(i==8)s=d}; if(T>0) printf "%.1f %.1f\n",100*(T-idle)/T,100*s/T; else print "0 0"}' "$statfile")
  # per-record delta: records aged out inside the window must not cancel growth elsewhere
  hw=$(awk '/^cpu /{sec++; next} /^E /{if(sec==1) a[$2]=$3; else b[$2]=$3} END{for(k in b){d=((k in a) && b[k]>=a[k]) ? b[k]-a[k] : b[k]; s+=d}; print s+0}' "$statfile")
  est=$(awk -v g="$gbps" -v w="$win" -v mss="$mss" 'BEGIN{printf "%d", g*1e9/8/mss*w}')
  if [ "$DUT_KIND" != ask2 ]; then   # frames the kernel never saw were forwarded by the fast path
    krx=$(awk '/^cpu /{sec++; next} /^K /{if(sec==1) a[$2]=$3; else b[$2]=$3} END{for(k in b) s+=b[k]-a[k]; print s+0}' "$statfile")
    hw=$(( est > krx ? est - krx : 0 ))
  fi
  ratio=$(awk -v h="$hw" -v e="$est" 'BEGIN{if(e>0) printf "%.2f", h/e; else print "0"}')
  verdict=$(awk -v r="$ratio" -v g="$gbps" 'BEGIN{if(g<0.1) print "FAIL(no traffic)"; else if(r>=0.9) print "HW"; else if(r>=0.1) print "PARTIAL"; else print "SW"}')
  printf '%-15s %7s %6s %6s %6s %8s %12s %6s  %s\n' "$tag" "$gbps" "$busy" "$sirq" "$gen" "$rt" "$hw" "$ratio" "$verdict"
  echo "$(date +%H:%M:%S),$tag,$gbps,$busy,$sirq,$gen,$rt,$hw,$est,$ratio,$verdict" >> "$OUT"
  sleep 8
done
echo "csv: $OUT"

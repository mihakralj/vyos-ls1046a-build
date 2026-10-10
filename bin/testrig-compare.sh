#!/usr/bin/env bash
# testrig-compare.sh — ASK2 against the external "tuned ASK master" Mono DK benchmark (RESULTS.md,
# armbian-ask-master-20261010): PDR, connection rate, latency and the kernel path, on the dell rig with
# the ASK2 DUT .185. Their method: TRex PDR = highest rate with <= 0.5 % loss over both directions,
# 8k sessions, the mean of 3 independent searches (+- max deviation); tsprobe NIC-timestamped latency at
# idle and 10/50/90 % of PDR; 20k new connections/s plus 20k held, with and without NAT.
#
# Before: cold-boot the DUT, `DUT_HOST=192.168.1.185 bin/testrig-combo-matrix.sh setup`. Never while the
# churn soak runs (same dells and DUT; this script refuses). The kpath section floods the kernel path and
# disengages/re-engages ASK on eth3/eth4 through the VyOS CLI; it runs last.
#
#   bin/testrig-compare.sh [pdr] [lat] [kpath] [cps]      (default: all four, in this order; cps last because
#                                                         a connection storm is the hardest on the control plane)
#   bin/testrig-compare-report.py <run dir>               (the results table)
#
# Output: /mnt/builds/ask2-review/oracle/compare-<date>-<time>/: summary.csv (one row per search set:
# label,size,n,mean_pps,maxdev_pct,gbit_l1,hw_share,rdrp_eth3,rdrp_eth4), the per-trial CSVs,
# cps.jsonl, lat.jsonl, run.log. Env: SIZES ("64 570 1518 imix"), SESSIONS (8192), REPS (3), STEPS (10),
# CPS_RATES ("5000 10000 20000"), CPS_SECS (30), CPS_HOLD (20000), LAT_COUNT (5000), LAT_LOADS
# ("10 50 90"), LAT_SIZE (64), KPATH_MAXPPS (1000000), OUT_DIR.
set -u
cd "$(dirname "$0")/.."
SIZES=${SIZES:-64 570 1518 imix}; SESSIONS=${SESSIONS:-8192}; REPS=${REPS:-3}; export STEPS=${STEPS:-10}
CPS_RATES=${CPS_RATES:-5000 10000 20000}; CPS_SECS=${CPS_SECS:-30}; CPS_HOLD=${CPS_HOLD:-20000}
LAT_COUNT=${LAT_COUNT:-5000}; LAT_LOADS=${LAT_LOADS:-10 50 90}; LAT_SIZE=${LAT_SIZE:-64}
KPATH_MAXPPS=${KPATH_MAXPPS:-1000000}
D=${OUT_DIR:-/mnt/builds/ask2-review/oracle/compare-$(date +%Y%m%d-%H%M)}; mkdir -p "$D"
SSHO="-o BatchMode=yes -o ConnectTimeout=8"; KEY=$HOME/.ssh/vyos_key; IF=enp1s0f0np0
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
DUT="ssh -n $SSHO -i $KEY vyos@192.168.1.185"
PG=bin/testrig-pktgen.sh
exec > >(tee -a "$D/run.log") 2>&1
say(){ echo "[$(date -u +%T)] $*"; }

$D2 'cat ~/soak/STATUS 2>/dev/null' | grep -qE '^state=(ARMING|RUNNING|SETTLING)' &&
  { echo "a churn soak is running on dell2 (~/soak/STATUS); not starting"; exit 1; }
scp -q $SSHO -i $KEY /mnt/builds/ask2-review/oracle/fmreg.py vyos@192.168.1.185:/tmp/ || exit 1

nat_on=0; off=0; srv=0; refl=0
cleanup(){
  [ $nat_on = 1 ] && $DUT 'sudo nft delete table ip ask2c' && say "NAT table removed"
  [ $off = 1 ] && offload on
  # by name: the server's forked workers outlive their parent; [t] keeps pkill from matching its own shell
  [ $srv = 1 ] && $D2 "pkill -f '[t]estrig-cps.py serve'"
  [ $refl = 1 ] && $D2 "sudo pkill -TERM -f '[t]estrig-latprobe.py reflect'"   # SIGTERM: timestamping off
}
trap cleanup EXIT

cli(){ # VyOS config commands -> one commit; a script-template commit returns 0 even when it fails
  local out
  { echo '#!/bin/vbash'; echo 'source /opt/vyatta/etc/functions/script-template'; echo configure
    printf '%s\n' "$@"; echo commit; echo exit; } | ssh $SSHO -i $KEY vyos@192.168.1.185 'cat > /tmp/compare-cli.sh'
  out=$($DUT 'vbash /tmp/compare-cli.sh 2>&1')
  if grep -qi 'commit failed' <<<"$out"; then echo "$out"; return 1; fi
}
offload(){ # on|off: ASK engage on eth3/eth4, both families (the rig config)
  local v=set i f c=()
  [ $1 = off ] && v=delete
  for i in eth3 eth4; do for f in ipv4 ipv6; do c+=("$v interfaces ethernet $i offload $f"); done; done
  cli "${c[@]}" || { say "offload $1: commit failed"; exit 1; }
  sleep 3
  if [ $1 = on ]; then $DUT 'sudo cat /sys/kernel/debug/fman_pcd/0/fe_arm' | grep -q 'Armed ports: 0x10 0x11' || { say "re-engage did not arm 0x10 0x11"; exit 1; }; off=0
  else $DUT 'sudo cat /sys/kernel/debug/fman_pcd/0/fe_arm' | grep -qE 'Armed ports:.*0x1[01]' && { say "disengage left a port armed"; exit 1; }; off=1; fi
  say "offload $1"
}
dut_snap(){ # -> "conntrack_count drops table_full cpu_busy_jiffies cpu_total_jiffies"
  $DUT "c=\$(cat /proc/sys/net/netfilter/nf_conntrack_count); d=\$(sudo conntrack -S 2>/dev/null | tr ' ' '\n' | awk -F= '\$1==\"drop\"||\$1==\"insert_failed\"||\$1==\"early_drop\"{s+=\$2} END{print s+0}'); t=\$(sudo dmesg | grep -c 'nf_conntrack: table full'); read -r _ a b c2 i w x y z _ < /proc/stat; echo \$c \$d \$t \$((a+b+c2+x+y+z)) \$((a+b+c2+i+w+x+y+z))"
}
retrans(){ $1 'nstat -az TcpRetransSegs TcpOutSegs' | awk '/TcpRetransSegs/{r=$2} /TcpOutSegs/{o=$2} END{print r, o}'; }
rdrp(){ # MAC RX FIFO overflow drops on eth3 (MAC9) and eth4 (MAC10), low 32 bits
  $DUT 'for o in 0xf0158 0xf2158; do sudo python3 /tmp/fmreg.py r $o | cut -d" " -f3; done' | paste -sd' '
}

search(){ # label size env...: REPS independent a2 searches -> summary.csv row (mean +- max deviation)
  local label=$1 size=$2 csv=$D/$1.csv i a0 b0 a1 b1 da=0 db=0; shift 2
  for i in $(seq 1 $REPS); do
    read -r a0 b0 <<<"$(rdrp)"
    env "$@" CONFIRM=1 OUT="$csv" $PG a2 port-v4 "$size"
    case $? in 3|4) say "$label $size: pktgen stopped (DUT unreachable or flows not offloaded); stopping the suite"; exit 1 ;; esac
    read -r a1 b1 <<<"$(rdrp)"
    da=$((da + (a1 - a0) % 4294967296)); db=$((db + (b1 - b0) % 4294967296))
    say "  $label $size search $i/$REPS: MAC rdrp eth3 +$(( (a1 - a0) % 4294967296 )) eth4 +$(( (b1 - b0) % 4294967296 ))"
  done
  [ -f "$D/summary.csv" ] || echo "label,size,n,mean_pps,maxdev_pct,gbit_l1,hw_share,rdrp_eth3,rdrp_eth4" > "$D/summary.csv"
  awk -F, -v s="$size" -v l="$label" -v da=$da -v db=$db '$3 ~ /^a2-result/ && $5==s {v[++n]=$6; h+=$11; t+=$6}
    END{ if(!n){print l","s",0,0,0,0,0,"da","db; exit}
         m=t/n; for(i=1;i<=n;i++){d=v[i]-m; if(d<0)d=-d; if(d>x)x=d}
         z=(s=="imix")? (7*64+4*570+1518)/12 : s
         printf "%s,%s,%d,%d,%.1f,%.2f,%.3f,%d,%d\n", l, s, n, m, (m>0? 100*x/m : 0), m*(z+20)*8/1e9, h/n, da, db }' \
    "$csv" | tee -a "$D/summary.csv"
}
mean_of(){ # label size -> mean pps from summary.csv
  awk -F, -v l="$1" -v s="$2" '$1==l && $2==s {p=$4} END{print p+0}' "$D/summary.csv" 2>/dev/null
}

sec_pdr(){
  local s
  for s in $SIZES; do
    say "pdr $s: both directions, $SESSIONS flows, <= 0.5 % loss, $REPS searches"
    search firewall "$s" BIDIR=1 FLOWS=$SESSIONS LOSS=0.5
  done
}

sec_cps(){
  local nat r b a rb ab out st
  scp -q $SSHO bin/testrig-cps.py admin@192.168.1.112:/tmp/ && scp -q $SSHO bin/testrig-cps.py admin@192.168.1.113:/tmp/ || exit 1
  $D2 'setsid nohup python3 /tmp/testrig-cps.py serve 8080 8 > /tmp/cps-serve.log 2>&1 < /dev/null &'; srv=1
  sleep 2
  for nat in 0 1; do
    if [ $nat = 1 ]; then
      $DUT 'sudo nft add table ip ask2c && sudo nft "add chain ip ask2c post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip ask2c post oifname eth4 ip saddr 10.99.1.0/24 masquerade' || exit 1
      nat_on=1
    fi
    for r in $CPS_RATES; do
      say "cps nat=$nat rate=$r/s for ${CPS_SECS}s + $CPS_HOLD held"
      $DUT 'sudo conntrack -F >/dev/null 2>&1'; sleep 2
      b=$(dut_snap); rb=$(retrans "$D1")
      out=$($D1 "python3 /tmp/testrig-cps.py run 10.99.1.112 10.99.2.113 8080 $r $CPS_SECS $CPS_HOLD 8")
      a=$(dut_snap); ab=$(retrans "$D1")
      st=$($DUT 'sudo conntrack -L -p tcp 2>/dev/null' | awk '/dport=808[01] /{for(i=1;i<=NF;i++) if($i ~ /^[A-Z_]+$/ && $i!="tcp"){n[$i]++; break}} END{for(k in n) printf "%s=%d ", k, n[k]}')
      echo "$out"
      read -r c0 d0 t0 u0 x0 <<<"$b"; read -r c1 d1 t1 u1 x1 <<<"$a"; read -r r0 o0 <<<"$rb"; read -r r1 o1 <<<"$ab"
      python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d.update(nat=int(sys.argv[2]), dut_conntrack_after=int(sys.argv[3]), dut_ct_drops=int(sys.argv[4]), dut_table_full_msgs=int(sys.argv[5]), dut_cpu_pct=round(100*float(sys.argv[6]),1), client_retrans_pct=round(100*float(sys.argv[7]),3), dut_ct_states=sys.argv[8].strip()); print(json.dumps(d))' \
        "$out" $nat "$c1" $((d1-d0)) $((t1-t0)) "$(awk -v a=$((u1-u0)) -v b=$((x1-x0)) 'BEGIN{print (b>0)? a/b : 0}')" \
        "$(awk -v r=$((r1-r0)) -v o=$((o1-o0)) 'BEGIN{print (o>0)? r/o : 0}')" "$st" | tee -a "$D/cps.jsonl"
    done
    [ $nat = 1 ] && { $DUT 'sudo nft delete table ip ask2c'; nat_on=0; }
  done
  $D2 "pkill -f '[t]estrig-cps.py serve'"; srv=0
}

lat_run(){ # label pdr_total bg_env...: probes at idle and at LAT_LOADS % of pdr, with that background
  local label=$1 pdr=${2:-0} l pps secs bg out loads=$LAT_LOADS; shift 2
  [ "$pdr" -gt 0 ] || { say "lat $label: no PDR, idle only"; loads=""; }   # pps 0 would be an unpaced flood
  for l in 0 $loads; do
    bg=""
    if [ $l != 0 ]; then
      pps=$((pdr * l / 100)); secs=$((LAT_COUNT / 1000 + 15))
      say "lat $label: background $pps pps ($l % of $pdr)"
      env "$@" OUT=$D/lat-bg.csv $PG trial port-v4 "$LAT_SIZE" "$pps" "$secs" > /dev/null & bg=$!
      sleep 9   # BIDIR warm-up (~7 s) then load
    fi
    out=$($D1 "sudo python3 /tmp/testrig-latprobe.py probe $IF 10.99.1.112 10.99.2.113 $LAT_COUNT 1000")
    [ -n "$bg" ] && wait $bg
    python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d.update(path=sys.argv[2], load_pct=int(sys.argv[3]), size=sys.argv[4]); print(json.dumps(d))' \
      "$out" "$label" $l "$LAT_SIZE" | tee -a "$D/lat.jsonl"
    if [ $l = 0 ] && ! python3 -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(d["ok"] < d["count"] // 2)' "$out" 2>/dev/null; then
      say "lat $label: idle probe failed, skipping the loaded steps"; return 1
    fi
  done
}
lat_setup(){
  scp -q $SSHO bin/testrig-latprobe.py admin@192.168.1.112:/tmp/ && scp -q $SSHO bin/testrig-latprobe.py admin@192.168.1.113:/tmp/ || exit 1
  $D2 "sudo setsid nohup python3 /tmp/testrig-latprobe.py reflect $IF 10.99.2.113 > /tmp/lat-refl.log 2>&1 < /dev/null &"; refl=1
  sleep 2
}

sec_lat(){
  local pdr; pdr=$(mean_of firewall "$LAT_SIZE")
  [ "${pdr:-0}" -gt 0 ] || { say "lat: no firewall $LAT_SIZE PDR in summary.csv; run pdr first"; return 1; }
  lat_setup
  lat_run offloaded "$pdr" BIDIR=1 FLOWS=$SESSIONS
}

sec_kpath(){ # kernel path: flows that never get a reply, so nothing is offloaded
  local v r
  for v in engaged off; do
    [ $v = off ] && offload off
    r=$REPS; [ $v = engaged ] && r=1   # engaged = the miss path into the kernel; off = the plain kernel path
    say "kpath $v: <= 0.5 % loss, search capped at $KPATH_MAXPPS pps"
    REPS=$r search kpath-$v-bidir 64 BIDIR=1 NOPRIME=1 FLOWS=1024 LOSS=0.5 MAXPPS=$KPATH_MAXPPS
    REPS=$r search kpath-$v-bidir imix BIDIR=1 NOPRIME=1 FLOWS=1024 LOSS=0.5 MAXPPS=$KPATH_MAXPPS
    REPS=$r search kpath-$v-1flow 64 NOPRIME=1 LOSS=0.5 MAXPPS=$KPATH_MAXPPS
  done
  # kernel-path latency, ports disengaged: background = the kernel path at LAT_LOADS % of its 64 B PDR
  [ $refl = 1 ] || lat_setup
  lat_run kernel "$(mean_of kpath-off-bidir 64)" BIDIR=1 NOPRIME=1 FLOWS=1024
  offload on
}

health(){ # stop the suite if the DUT is unreachable or the FE has stuck (F-256 SYNC timeouts)
  local n; n=$($DUT 'sudo dmesg | grep -c "SYNC timed out"; true') || { say "DUT unreachable; stopping"; exit 1; }
  [ "${n:-0}" = 0 ] || { say "DUT logged $n FE SYNC timeouts; stopping"; exit 1; }
  say "DUT healthy: $($DUT uptime)"
}
secs=${*:-pdr lat kpath cps}
say "compare run -> $D: $secs (SESSIONS=$SESSIONS)"
for s in $secs; do health; "sec_$s"; done
health
say "done"

#!/usr/bin/env bash
# testrig-wgfp-p1.sh — WireGuard fast path phase P1 (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md):
# how many frames per second can the CPUs drain from QMan frame queues without building an sk_buff,
# when FMan flow records enqueue matching frames straight to software-owned queues?
#
# Needs an image with kernel patch 4013 (dpaa_wgfp.c, debugfs /sys/kernel/debug/dpaa_wgfp) on .185 with
# ASK engaged on eth3 (production config: `offload ipv4`). Quiesce the board first: the sink records
# cannot be removed one by one (debugfs `del` only covers table 0), so REBOOT .185 AFTER the run.
#
#   dell1 (pktgen, FLOWS udp flows 10.99.1.112:10000+k -> 10.99.2.113:9000)
#     -> .185 eth3 -> FMan ehash HIT (record per flow: ENQ to sink FQ k % SINKS) -> CPU sink -> BMan release
#
# Usage: bin/testrig-wgfp-p1.sh [run]
# Env:   SINKS=4 (queues, one per CPU)  FLOWS=16  TOUCH=2 (0 none, 1 first+last 64 B, 2 read all, 3 read+write)
#        PPS_LIST="100000 300000 500000 700000 0"  (0 = unpaced)  SECS=8  SIZE=1434 (wire bytes incl FCS)
#        TBLS="0 2 3" (the 50-byte-key ehash tables; a record only matches in the table of the port the frame enters)
#        RECREATE=1 (destroy existing sinks first, to change SINKS; stop traffic before)  OUT=csv
# Gate G1 (plan): >= 600000 frames/s delivered over 4 CPUs at <= 0.1 % loss.  Kill: < 350000 frames/s.
set -u
SINKS=${SINKS:-4}; FLOWS=${FLOWS:-16}; TOUCH=${TOUCH:-2}; SECS=${SECS:-8}; SIZE=${SIZE:-1434}
PPS_LIST=${PPS_LIST:-"100000 300000 500000 700000 0"}; TBLS=${TBLS:-"0 2 3"}
OUT=${OUT:-/mnt/builds/ask2-review/oracle/wgfp-p1-$(date +%Y%m%d-%H%M).csv}
cd "$(dirname "$0")/.."
DUT=vyos@192.168.1.185
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
dut(){ ssh -n $SSHO -i $HOME/.ssh/vyos_key $DUT "$1"; }
DBG=/sys/kernel/debug/dpaa_wgfp
FE=/sys/kernel/debug/fman_pcd/0/fe_flow

fail(){ echo "ABORT: $*" >&2; exit 1; }

dut "sudo test -d $DBG" || fail "no $DBG: the image does not carry kernel patch 4013"
dut 'zcat /proc/config.gz | grep -qE "^CONFIG_(PROVE_LOCKING|DEBUG_SPINLOCK)=y"' && fail "lock debugging kernel"
echo "kernel: $(dut 'uname -v | cut -c1-60')"
dut 'sudo sysctl -qw kernel.softlockup_panic=1 kernel.panic_on_rcu_stall=1'   # a lockup reboots (panic=60) instead of hanging

if [ "${RECREATE:-0}" = 1 ]; then   # destroy old sinks first; the records inserted below replace the old ones (dup-key evict)
  dut "echo destroy | sudo tee $DBG/ctl >/dev/null"
fi
if dut "sudo cat $DBG/stats" | grep -q "sinks=$SINKS"; then echo "reusing $SINKS existing sinks"
else dut "echo 'create $SINKS' | sudo tee $DBG/ctl >/dev/null" || fail "sink create failed (see dmesg)"; fi
dut "echo 'touch $TOUCH' | sudo tee $DBG/ctl >/dev/null"
FQIDS=($(dut "sudo cat $DBG/stats" | sed -n 's/.*fqid=0x\([0-9a-f]*\).*/\1/p'))
[ ${#FQIDS[@]} -eq "$SINKS" ] || fail "expected $SINKS sink fqids, got ${#FQIDS[@]}"
echo "sink fqids: ${FQIDS[*]}"

# 50-byte dual-lane key (ask_internal.h ASK_FE_KEY_SIZE_DUAL): family 0x40 | src | dst | 32 zero (v6 lane) |
# proto | sport | dport | VID (0) | PPPoE session (0)
SIP=0a630170; DIP=0a630271; ZERO32=$(printf '00%.0s' $(seq 1 32))
for k in $(seq 0 $((FLOWS-1))); do
  sport=$(printf '%04x' $((10000+k)))
  key="40${SIP}${DIP}${ZERO32}11${sport}232800000000"
  fq=${FQIDS[$((k % SINKS))]}
  for t in $TBLS; do
    dut "echo 'add $t $key $fq' | sudo tee $FE >/dev/null" || fail "record insert failed (table $t flow $k)"
  done
done
echo "inserted $FLOWS flow records into tables: $TBLS"

stats(){ dut "sudo cat $DBG/stats"; }
cpustat(){ dut 'grep "^cpu[0-9]" /proc/stat'; }
sumf(){ awk -F'frames=' '/^sink/{split($2,a," "); s+=a[1]} END{print s+0}'; }

echo "ts,pps_req,offered_pps,tx_frames,delivered,delivered_pct,fps_delivered,cpu_busy_pct,backlog" > "$OUT"
printf '%-9s %-10s %-12s %-10s %-12s %-8s %s\n' req offered tx delivered fps_deliv cpu_busy backlog
for pps in $PPS_LIST; do
  dut "echo reset | sudo tee $DBG/ctl >/dev/null"
  c0=$(cpustat)
  res=$(DUT_KIND=ask2 FLOWS=$FLOWS THREADS=4 bin/testrig-pktgen.sh trial port-v4 $SIZE $pps $SECS 2>&1 | tail -1)
  c1=$(cpustat)
  s=$(stats)
  off=$(echo "$res" | sed -n 's/.*offered=\([0-9]*\).*/\1/p'); tx=$(echo "$res" | sed -n 's/.* tx=\([0-9]*\).*/\1/p')
  del=$(echo "$s" | sumf); back=$(echo "$s" | sed -n 's/.*frm_cnt=\([0-9]*\).*/\1/p' | awk '{b+=$1} END{print b+0}')
  busy=$( { echo "$c0"; echo SEP; echo "$c1"; } | awk '/^SEP/{s=1;next}
     {t=0; for(i=2;i<=11;i++) t+=$i; idle=$5+$6; if(!s){T0[$1]=t;I0[$1]=idle} else {T1[$1]=t;I1[$1]=idle}}
     END{for(k in T1){d=T1[k]-T0[k]; if(d>0){b=100*(d-(I1[k]-I0[k]))/d; printf "%s=%.0f%% ", k, b}}}')
  pct=$(awk -v d="${del:-0}" -v t="${tx:-0}" 'BEGIN{if(t>0) printf "%.2f", 100*d/t; else print 0}')
  fps=$(awk -v d="${del:-0}" -v s=$SECS 'BEGIN{printf "%d", d/s}')
  printf '%-9s %-10s %-12s %-10s %-12s %-8s %s\n' "$pps" "${off:-?}" "${tx:-?}" "${del:-0}" "$fps ($pct%)" "$busy" "backlog=$back"
  echo "$(date +%H:%M:%S),$pps,${off:-},${tx:-},${del:-0},$pct,$fps,\"$busy\",$back" >> "$OUT"
  sleep 4
done
echo
echo "$s"
echo "csv: $OUT"
echo "Now REBOOT .185 (the sink records stay in the ehash tables until then)."

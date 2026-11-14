#!/usr/bin/env bash
# testrig-trex-ndr.sh <dut-name> <dut-mgmt-ip> <gen: local|dell1> <outdir> [rep list, default "1 2 3"]
# env SIZES (default "64 512 1518"), FLOWS (default "1 1024"; 65536+ flows belong to bin/testrig-flow-ladder.sh)
# The generator hosts' neighbour entries are pinned on the DUT first: TRex answers ARP only in service mode, and an
# expiring entry tore down every flow behind it mid-search (plans/ASK2-FLOW-SCALING-PLAN.md §0.1). TRex profiles and
# drivers: bin/trex/ (installed in /opt/trex on the generator).
# TRex S1 (ASK2 hardware offload): routed IPv4 UDP, bidirectional symmetric, frame 64/512/1518 B x 1/1024/65536 flows.
# Per cell, after priming: ndr binary searches (30 s trials, 0.5 % precision) for
#   NDR loss <= 0.001 %   PDR <= 0.5 %   TP90 <= 10 %
# and MRR = delivered rate at 100 % offered (one 30 s trial). Rep loop is outermost, so every rep covers all cells.
# One CSV row per result in <outdir>/results-<dut>.csv; rates are totals over both directions, measured by TRex
# (tx_pps/rx_pps), throughput from the delivered rate. JSON, logs and DUT snapshots per search beside it.
DUT=$1 DIP=$2 GEN=$3 O=$4 REPS=${5:-1 2 3}
P=/mnt/builds/wgfp/plan; mkdir -p $O; CSV=$O/results-$DUT.csv
. /mnt/builds/vyos-ls1046a-build/bin/testrig-env.sh
st(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a $P/STATUS; }
D="ssh -n -o BatchMode=yes -o ConnectTimeout=20 -o IdentitiesOnly=yes -i $HOME/.ssh/vyos_key vyos@$DIP"
if [ "$GEN" = local ]; then T="bash -c"; fetch(){ cp "$1" "$2"; }
else T="ssh -n -o BatchMode=yes -o ConnectTimeout=20 $RIG_SSHK $GEN1"; fetch(){ scp -q $RIG_SSHK "$GEN1:$1" "$2"; }; fi
$D "sudo ip neigh replace ${GEN_IP3:-10.99.1.112} lladdr ${GEN_MAC3:-3c:fd:fe:2a:f0:20} dev eth3 nud permanent && sudo ip neigh replace ${GEN_IP4:-10.99.2.113} lladdr ${GEN_MAC4:-3c:fd:fe:2a:f0:22} dev eth4 nud permanent" \
  || { st "trexrun $DUT: cannot pin generator neighbours"; exit 2; }
IMG=$($D 'grep -o "BOOT_IMAGE=/boot/[^/ ]*" /proc/cmdline | cut -d/ -f3')
BOOT=$($D 'uptime -s | tr " " T')
snap(){ $D 'date -u +%T; uptime; for i in eth3 eth4; do ip -s -s link show $i; done; sudo ask-check 2>&1 | tail -2
  echo "fe_hit_records=$(sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats | grep -c "pkt_count=[1-9]") fe_records=$(sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats | grep -c pkt_count)"
  echo "sync_timeouts=$(cat /sys/module/fsl_dpaa_fman/parameters/ehash_sync_timeouts) wedgecap=$(systemctl is-active wedgecap)"
  cat /sys/class/thermal/thermal_zone*/temp | tr "\n" " "; echo; sudo dmesg | grep -ciE "BUG:|WARNING:|Oops|hung task|soft lockup"' > $1 2>&1; }
alive(){ $D true || { st "trexrun $DUT: DUT unreachable $* (WEDGE?); stopped, see /config/wedgecap on $DIP"; exit 3; }; }
# row <rep> <size> <flows> <metric> <limit%> <rate_p> <tx_pps> <rx_pps> <drop%> <trial_s> <iters> <elapsed_s>
row(){ python3 - "$@" >> $CSV <<'EOF'
import sys, time
rep, size, flows, metric, lim, rp, tx, rx, drop, trial, it, el = sys.argv[1:13]
s, rxm = int(size), float(rx) / 1e6
line = 2 * 10e9 / ((s + 20) * 8) / 1e6          # both directions, 10GbE
print(",".join(map(str, [time.strftime("%FT%TZ", time.gmtime()), *sys.argv[13:16], rep, size, flows, metric, lim,
      f"{float(rp):.2f}", f"{float(tx)/1e6:.4f}", f"{rxm:.4f}", f"{float(drop):.5f}",
      f"{rxm*s*8/1e3:.3f}", f"{rxm*(s+20)*8/1e3:.3f}", f"{100*rxm/line:.2f}", trial, it, el])))
EOF
}
[ -s $CSV ] || echo "ts,dut,image,boot,rep,size,flows,metric,loss_limit_pct,rate_pct,tx_mpps,rx_mpps,drop_pct,gbps_l2,gbps_l1,pct_line,trial_s,iterations,elapsed_s" > $CSV
st "trexrun $DUT: start $REPS reps, image $IMG, $BOOT -> $CSV"
for rep in $REPS; do for size in ${SIZES:-64 512 1518}; do for flows in ${FLOWS:-1 1024}; do
  c=r$rep-s$size-f$flows; alive "before $c"
  $T "cd /opt/trex && sudo python3 ask2_fixed.py $size $flows 1% 3 2>&1 | grep -E '^size'" > $O/$DUT-$c-prime.txt
  snap $O/$DUT-$c-dut-before.txt; res=""
  for m in NDR:0.001 PDR:0.5 TP90:10; do
    n=${m%%:*} p=${m#*:} J=/tmp/N-$DUT-$c-$n.json
    $T "cd /opt/trex && sudo ./ndr --stl --ports 0 1 --bi-dir --force-map -p $p -t 30 -e 0.5 -x 20 --opt-bin-search \
        --profile stl/ask2_udp.py --prof-tun size=$size,flows=$flows -o $J" > $O/$DUT-$c-$n.log 2>&1
    fetch $J $O/ 2>/dev/null
    v=$(python3 -c "import json,sys; r=json.load(open(sys.argv[1]))['results']
print(r['rate_p'], r['tx_pps'], r['rx_pps'], r['drop_rate_percentage'], r['total_iterations'], int(r['Elapsed Time']))" $O/$(basename $J) 2>/dev/null)
    if [ -n "$v" ]; then set -- $v; row $rep $size $flows $n $p $1 $2 $3 $4 30 $5 $6 $DUT $IMG $BOOT; res="$res $n=$(awk -v x=$3 'BEGIN{printf "%.3f", x/1e6}')"
    else res="$res $n=fail"; fi
    alive "after $c $n"
  done
  $T "cd /opt/trex && sudo python3 ask2_fixed.py $size $flows 100% 30 2>&1 | grep -vE 'Deprecation|cipher='" > $O/$DUT-$c-MRR.txt
  v=$(awk '/^size/{for(i=1;i<=NF;i++){split($i,a,"="); k[a[1]]=a[2]}; d=k["drop"]; sub("%","",d); print k["total_tx"]/30, k["total_rx"]/30, d}' $O/$DUT-$c-MRR.txt)
  if [ -n "$v" ]; then set -- $v; row $rep $size $flows MRR 100 100 $1 $2 $3 30 1 30 $DUT $IMG $BOOT; res="$res MRR=$(awk -v x=$2 'BEGIN{printf "%.3f", x/1e6}')"; fi
  snap $O/$DUT-$c-dut-after.txt; alive "after $c MRR"
  st "trexrun $DUT $c rx Mpps (both dirs):$res"
done; done; done
st "TREXRUN-DONE $DUT -> $CSV"

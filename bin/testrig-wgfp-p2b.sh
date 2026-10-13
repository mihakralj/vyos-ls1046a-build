#!/usr/bin/env bash
# testrig-wgfp-p2b.sh — P2b-1 real-peer fast path throughput (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md).
# Prereq: image with patches 4013-4017, a live tunnel (KEEP=1 bin/testrig-wireguard.sh setup wg-v4), cold boot.
# 4 TCP flows dell1 (cport 41001+k) -> dell2 wg0:5241+k are steered by ehash records to the 4 sink FQs; the
# wireguard hook seals them with the real peer keypair. Env: DUR=20 FLOWS=4 TBLS="0 2 3" OUT=csv
set -u
DUR=${DUR:-20}; FLOWS=${FLOWS:-4}; TBLS=${TBLS:-"0 2 3"}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
dut(){ ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185 "$1"; }
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
DBG=/sys/kernel/debug/dpaa_wgfp; FE=/sys/kernel/debug/fman_pcd/0/fe_flow; WP=/sys/module/wireguard/parameters/wgfp_peer
dut 'sudo sysctl -qw kernel.softlockup_panic=1 kernel.panic_on_rcu_stall=1'
dut "echo off | sudo tee $WP >/dev/null; echo destroy | sudo tee $DBG/ctl >/dev/null; echo 'create 4' | sudo tee $DBG/ctl >/dev/null"
FQIDS=($(dut "sudo cat $DBG/stats" | sed -n 's/.*fqid=0x\([0-9a-f]*\).*/\1/p'))
[ ${#FQIDS[@]} -eq 4 ] || { echo "ABORT: sinks ${#FQIDS[@]}"; exit 1; }
$D2 'for p in 5241 5242 5243 5244; do sudo pkill -f "iperf3 -s -p $p"; iperf3 -s -p $p -D; done'
dut "echo 'wg0 eth4' | sudo tee $WP >/dev/null" || { echo "ABORT: wgfp_peer rejected"; dut 'sudo dmesg | tail -5'; exit 1; }
sleep 2; echo "peer: $(dut "sudo cat $WP")"
SIP=0a630170; DIP=0ac7c871; DIP=$(printf '0a63c8%02x' 113); ZERO32=$(printf '00%.0s' $(seq 1 32))
for k in $(seq 0 $((FLOWS-1))); do
  key="40${SIP}${DIP}${ZERO32}06$(printf '%04x' $((41001+k)))$(printf '%04x' $((5241+k)))00000000"
  for t in $TBLS; do dut "echo 'add $t $key ${FQIDS[$((k % 4))]}' | sudo tee $FE >/dev/null" || { echo "ABORT: record $k tbl $t"; exit 1; }; done
done
echo "steered $FLOWS flows"
c0=$(dut 'grep "^cpu[0-9]" /proc/stat')
for k in $(seq 0 $((FLOWS-1))); do
  $D1 "iperf3 -c 10.99.200.113 -B 10.99.1.112 --cport $((41001+k)) -p $((5241+k)) -t $DUR -J > /tmp/p2b$k.json 2>&1 &"
done
sleep $((DUR/2)); echo "mid: $(dut "sudo cat $WP")"; dut 'top -bn1 -1 | grep "^%Cpu"' 
sleep $((DUR/2+4))
echo "end: $(dut "sudo cat $WP")"; dut "sudo cat $DBG/stats" | head -5
tot=0; for k in $(seq 0 $((FLOWS-1))); do
  b=$($D1 "python3 -c \"import json;d=json.load(open('/tmp/p2b$k.json'));print(d['end']['sum_sent']['bits_per_second'] if 'end' in d and d['end'] else 0)\"" 2>/dev/null); echo "flow $k: ${b:-0} bit/s"
  tot=$(awk -v a=$tot -v b=${b:-0} 'BEGIN{print a+b}')
done
echo "TOTAL up: $(awk -v t=$tot 'BEGIN{printf "%.3f Gbit/s", t/1e9}')"
dut 'sudo dmesg | tail -5'
echo "NOTE: reboot .185 afterwards (sink records persist in ehash tables)."

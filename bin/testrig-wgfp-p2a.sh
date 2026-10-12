#!/usr/bin/env bash
# testrig-wgfp-p2a.sh — WireGuard fast path phase P2a: real in-place ChaCha20-Poly1305 seal + header build +
# transmit through the DPAA queues, no sk_buff (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md).
#
# Needs an image with kernel patches 4013-4015. dell1 pktgen sends UDP flows 10.99.1.112:10000+k -> 10.99.2.113:9000 into
# eth3; ehash records steer them to the per-CPU sinks; the wireguard module's lab hook seals each frame with a fixed
# key into a WireGuard transport packet (UDP 10.99.2.185:51820 -> 10.99.2.113:51820) and transmits it on eth4's
# no-confirm queue.  dell2 counts what arrives (X710 MAC counter) and one captured packet is decrypted in userspace.
# REBOOT .185 AFTER the run (the sink records stay in the ehash tables).
#
# Usage: bin/testrig-wgfp-p2a.sh      Env: SINKS=4 FLOWS=16 SIZE=1438 (wire bytes incl FCS; 1438 = 1420-byte inner IP)
#        PPS_LIST="100000 200000 300000 400000 500000 0"  SECS=8  KEYHEX=<64 hex>  RCV_IDX=7  OUT=csv
# Gates (plan): G2-lite = transmit ceiling. Model: ~13k cycles/pkt => ~440k pkt/s on 4 cores (~5 Gbit/s inner).
set -u
SINKS=${SINKS:-4}; FLOWS=${FLOWS:-16}; SIZE=${SIZE:-1438}; SECS=${SECS:-8}; RCV_IDX=${RCV_IDX:-7}
PPS_LIST=${PPS_LIST:-"100000 200000 300000 400000 500000 0"}
KEYHEX=${KEYHEX:-000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f}
TBLS=${TBLS:-"0 2 3"}
OUT=${OUT:-/mnt/builds/ask2-review/oracle/wgfp-p2a-$(date +%Y%m%d-%H%M).csv}
cd "$(dirname "$0")/.."
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
dut(){ ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185 "$1"; }
D2="ssh -n $SSHO admin@192.168.1.113"
DBG=/sys/kernel/debug/dpaa_wgfp; FE=/sys/kernel/debug/fman_pcd/0/fe_flow
fail(){ echo "ABORT: $*" >&2; exit 1; }

dut "sudo test -d $DBG" || fail "no $DBG"
dut 'sudo test -e /sys/module/wireguard/parameters/wgfp_lab || sudo modprobe wireguard; sudo test -e /sys/module/wireguard/parameters/wgfp_lab' || fail "wireguard module without the fast path hook (patch 4015)"
dut 'zcat /proc/config.gz | grep -qE "^CONFIG_(PROVE_LOCKING|DEBUG_SPINLOCK)=y"' && fail "lock debugging kernel"
echo "kernel: $(dut 'uname -v | cut -c1-60')"
dut 'sudo sysctl -qw kernel.softlockup_panic=1 kernel.panic_on_rcu_stall=1'

# addressing for the sealed packets
dut 'ping -c2 -W1 10.99.2.113 >/dev/null 2>&1'
DMAC=$(dut 'ip neigh show 10.99.2.113 dev eth4' | awk '{for(i=1;i<NF;i++) if($i=="lladdr") print $(i+1)}' | head -1)
SMAC=$(dut 'cat /sys/class/net/eth4/address')
[ -n "$DMAC" ] && [ -n "$SMAC" ] || fail "no MAC for 10.99.2.113 on eth4 (dmac='$DMAC' smac='$SMAC')"
echo "egress eth4: $SMAC -> $DMAC"
D2IF=$($D2 "ip -o addr show | awk '/10.99.2.113/ {print \$2}' | head -1")
[ -n "$D2IF" ] || fail "dell2 interface for 10.99.2.113 not found"
echo "dell2 interface: $D2IF"

if [ "${RECREATE:-0}" = 1 ]; then dut "echo destroy | sudo tee $DBG/ctl >/dev/null"; fi
if dut "sudo cat $DBG/stats" | grep -q "sinks=$SINKS"; then echo "reusing $SINKS sinks"
else dut "echo 'create $SINKS' | sudo tee $DBG/ctl >/dev/null" || fail "sink create failed"; fi
FQIDS=($(dut "sudo cat $DBG/stats" | sed -n 's/.*fqid=0x\([0-9a-f]*\).*/\1/p'))
[ ${#FQIDS[@]} -eq "$SINKS" ] || fail "expected $SINKS sinks, got ${#FQIDS[@]}"

SIP=0a630170; DIP=0a630271; ZERO32=$(printf '00%.0s' $(seq 1 32))
for k in $(seq 0 $((FLOWS-1))); do
  key="40${SIP}${DIP}${ZERO32}11$(printf '%04x' $((10000+k)))232800000000"
  for t in $TBLS; do dut "echo 'add $t $key ${FQIDS[$((k % SINKS))]}' | sudo tee $FE >/dev/null" || fail "record insert (table $t flow $k)"; done
done
echo "inserted $FLOWS records into tables $TBLS -> sinks ${FQIDS[*]}"

dut "echo 'eth4 $DMAC $SMAC 10.99.2.185 10.99.2.113 51820 51820 $RCV_IDX $KEYHEX' | sudo tee /sys/module/wireguard/parameters/wgfp_lab >/dev/null" || fail "lab seal config rejected (dmesg)"
trap 'dut "echo off | sudo tee /sys/module/wireguard/parameters/wgfp_lab >/dev/null" 2>/dev/null' EXIT

d2rx(){ $D2 "ethtool -S $D2IF | awk '/port.rx_unicast:/ {print \$2}'"; }
cpustat(){ dut 'grep "^cpu[0-9]" /proc/stat'; }
sealed(){ dut 'sudo cat /sys/module/wireguard/parameters/wgfp_lab' | sed -n 's/.*sealed=\([0-9]*\).*/\1/p'; }
xmitfail(){ dut "sudo cat $DBG/stats" | sed -n 's/.*xmit_fail=\([0-9]*\).*/\1/p' | head -1; }

echo "ts,pps_req,offered,sealed,d2_rx,xmit_fail,sealed_pct_of_tx,d2_pct_of_sealed,sealed_fps,cpu" > "$OUT"
printf '%-8s %-9s %-10s %-10s %-10s %-9s %-10s %-9s %s\n' req offered tx_frames sealed dell2_rx xmit_fail sealed_fps d2_%sealed cpu_busy
first=1
for pps in $PPS_LIST; do
  s0=$(sealed); r0=$(d2rx); x0=$(xmitfail); c0=$(cpustat)
  if [ $first = 1 ]; then   # capture a few sealed packets at the first (lowest) rate
    $D2 "sudo rm -f /tmp/wgp.pcap; sudo timeout $((SECS+6)) tcpdump -nn -i $D2IF -c 4 -w /tmp/wgp.pcap udp port 51820 >/dev/null 2>&1 &" ; sleep 1
  fi
  res=$(DUT_KIND=ask2 FLOWS=$FLOWS THREADS=4 bin/testrig-pktgen.sh trial port-v4 $SIZE $pps $SECS 2>&1 | tail -1)
  sleep 1
  s1=$(sealed); r1=$(d2rx); x1=$(xmitfail); c1=$(cpustat)
  off=$(echo "$res" | sed -n 's/.*offered=\([0-9]*\).*/\1/p'); tx=$(echo "$res" | sed -n 's/.* tx=\([0-9]*\).*/\1/p')
  ds=$((s1-s0)); dr=$((r1-r0)); dx=$((x1-x0))
  busy=$( { echo "$c0"; echo SEP; echo "$c1"; } | awk '/^SEP/{s=1;next}
     {t=0; for(i=2;i<=11;i++) t+=$i; idle=$5+$6; if(!s){T0[$1]=t;I0[$1]=idle} else {T1[$1]=t;I1[$1]=idle}}
     END{for(k in T1){d=T1[k]-T0[k]; if(d>0){b=100*(d-(I1[k]-I0[k]))/d; printf "%s=%.0f%% ", k, b}}}')
  p1=$(awk -v a=$ds -v b=${tx:-0} 'BEGIN{if(b>0) printf "%.2f", 100*a/b; else print 0}')
  p2=$(awk -v a=$dr -v b=$ds 'BEGIN{if(b>0) printf "%.2f", 100*a/b; else print 0}')
  fps=$((ds / SECS))
  printf '%-8s %-9s %-10s %-10s %-10s %-9s %-10s %-9s %s\n' "$pps" "${off:-?}" "${tx:-?}" "$ds ($p1%)" "$dr" "$dx" "$fps" "$p2%" "$busy"
  echo "$(date +%H:%M:%S),$pps,${off:-},$ds,$dr,$dx,$p1,$p2,$fps,\"$busy\"" >> "$OUT"
  first=0; sleep 4
done

echo; echo "== crypto check: decrypt a captured packet on dell2 with the lab key"
$D2 'python3 - <<PY
import struct,sys
try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
except Exception as e:
    print("python3-cryptography missing on dell2:", e); sys.exit(0)
key=bytes.fromhex("'$KEYHEX'")
d=open("/tmp/wgp.pcap","rb").read()
if len(d)<24: print("no capture"); sys.exit(0)
o=24; n=0
while o+16<=len(d) and n<2:
    ts,tu,cl,ol=struct.unpack("<IIII",d[o:o+16]); f=d[o+16:o+16+cl]; o+=16+cl; n+=1
    ip=f[14:]; ihl=(ip[0]&15)*4; udp=ip[ihl:ihl+8]; wg=ip[ihl+8:]
    typ,idx,ctr=struct.unpack("<IIQ",wg[:16]); ct=wg[16:]
    nonce=b"\0\0\0\0"+struct.pack("<Q",ctr)
    try:
        pt=ChaCha20Poly1305(key).decrypt(nonce,bytes(ct),None)
    except Exception as e:
        print("pkt",n,"DECRYPT FAILED",repr(e)); continue
    inner=pt; iv=inner[0]>>4; tl=struct.unpack(">H",inner[2:4])[0]
    src=".".join(map(str,inner[12:16])); dst=".".join(map(str,inner[16:20])); pr=inner[9]
    sp,dp=struct.unpack(">HH",inner[20:24])
    print("pkt",n,"type",typ,"idx",idx,"counter",ctr,"outer len",len(f),"-> decrypted OK: inner IPv%d %s:%d -> %s:%d proto %d totlen %d, pad bytes %d"%(iv,src,sp,dst,dp,pr,tl,len(pt)-tl))
PY'
echo "csv: $OUT"
echo "Now REBOOT .185 (sink records stay in the ehash tables until then)."

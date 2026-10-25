#!/bin/bash
# testrig-udp-sweep.sh: F-270 threshold sweep. Generators and DUT eth3/eth4 are set to MTU
# (default 2500); a low-rate UDP flow of each payload size crosses the DUT gen1 -> gen2 and
# receiver loss must be 0 (offloaded frames bypass eth3 rx_packets). A wedge (IP length > 1500 pre-F-270) loses everything.
# Restores 1500 on exit. Never run while the A8 soak owns the rig.
#   bin/testrig-udp-sweep.sh
set -u
. "$(dirname "$0")/testrig-env.sh"
DUT=${DUT:-$(rig_dut ask2)} || exit 2
rig_require GEN1; rig_require GEN2
MTU=${MTU:-2500}; SIZES=${SIZES:-"1472 1473 1480 1500 1800 2000 2400"}
GEN2_IP=${GEN2_IP:-10.99.2.113}; SRC_IP=${SRC_IP:-10.99.1.112}
G1IF=${G1IF:-enp1s0f0np0}; G2IF=${G2IF:-enp1s0f0np0}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
d(){ ssh -n $SSHO $RIG_SSHK "$DUT" "$@"; }
g1(){ ssh -n $SSHO $RIG_SSHK "$GEN1" "$@"; }
g2(){ ssh -n $SSHO $RIG_SSHK "$GEN2" "$@"; }
fail=0
setmtu(){
  d "sudo ip link set eth3 mtu $1; sudo ip link set eth4 mtu $1"
  g1 "sudo ip link set $G1IF mtu $1"; g2 "sudo ip link set $G2IF mtu $1"; sleep 4
}
trap 'setmtu 1500 >/dev/null 2>&1' EXIT
setmtu "$MTU"
for l in $SIZES; do
  o=$(g1 "iperf3 -c $GEN2_IP -B $SRC_IP -u -b 30M -l $l -t 2 2>&1 | grep receiver")
  lost=$(echo "$o" | sed -n 's|.* \([0-9]*\)/\([0-9]*\) (.*|\1|p'); tot=$(echo "$o" | sed -n 's|.* \([0-9]*\)/\([0-9]*\) (.*|\2|p')
  if [ -n "$tot" ] && [ "$tot" -gt 1000 ] && [ "${lost:-1}" -eq 0 ]; then echo "PASS payload-$l (lost $lost/$tot)"; else echo "FAIL payload-$l ($o)"; fail=$((fail+1)); break; fi
done
echo "RESULT udp-sweep failures=$fail"; exit $((fail>0))

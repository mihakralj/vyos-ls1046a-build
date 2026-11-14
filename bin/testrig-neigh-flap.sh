#!/usr/bin/env bash
# testrig-neigh-flap.sh <dut-ip> <outdir> <flows> : ASK2 neighbour-change cost (plans/ASK2-FLOW-SCALING-PLAN.md S5).
# Holds <flows> bidirectional 64 B UDP flows from the dell1 TRex (ask2_fixed.py), waits for full offload, then on the
# eth4 generator neighbour: (1) same-MAC flap: delete, 5 s, re-pin the same MAC; (2) MAC change: repoint to a bogus
# MAC, 10 s, repoint back. ftrace function_graph times every ask_flow_neigh_mac_changed() call; the ladder sampler
# streams offloaded flows, CPU and kernel-path pps per second; TRex reports the loss of the whole hold.
# Env: HOLD_RATE=100kpps HOLD_S=150 GEN_MAC3/GEN_IP3 GEN_MAC4/GEN_IP4 BOGUS_MAC.
set -u
DIP=$1 O=$2 N=$3
HOLD_RATE=${HOLD_RATE:-100kpps} HOLD_S=${HOLD_S:-150} BOGUS_MAC=${BOGUS_MAC:-02:00:00:00:00:01}
GEN_IP3=${GEN_IP3:-10.99.1.112} GEN_MAC3=${GEN_MAC3:-3c:fd:fe:2a:f0:20}
GEN_IP4=${GEN_IP4:-10.99.2.113} GEN_MAC4=${GEN_MAC4:-3c:fd:fe:2a:f0:22}
. /mnt/builds/vyos-ls1046a-build/bin/testrig-env.sh
mkdir -p "$O"
D="ssh -n -o BatchMode=yes -o ConnectTimeout=20 -o IdentitiesOnly=yes -i $HOME/.ssh/vyos_key vyos@$DIP"
T="ssh -n -o BatchMode=yes -o ConnectTimeout=20 $RIG_SSHK $GEN1"
TR=/sys/kernel/tracing
ev(){ echo "$(date +%s) $*" | tee -a "$O/events.txt"; $D "echo $1 > /tmp/ladder-phase"; }
pin4(){ $D "sudo ip neigh replace $GEN_IP4 lladdr $1 dev eth4 nud permanent"; }

scp -q -o IdentitiesOnly=yes -i $HOME/.ssh/vyos_key "$(dirname "$0")/ask2-ladder-sampler.py" vyos@$DIP:/tmp/ladder-sampler.py || exit 2
$D "sudo ip neigh replace $GEN_IP3 lladdr $GEN_MAC3 dev eth3 nud permanent" && pin4 $GEN_MAC4 || exit 2
$D "sudo sh -c 'cd $TR && echo 0 > tracing_on && echo function_graph > current_tracer &&
    echo ask_flow_neigh_mac_changed > set_graph_function && echo 1 > max_graph_depth && echo > trace && echo 1 > tracing_on'" || exit 2

ev hold; $D 'sudo python3 /tmp/ladder-sampler.py' > "$O/samples.txt" 2>/dev/null & samp=$!
$T "cd /opt/trex && sudo timeout $((HOLD_S + 60)) python3 ask2_fixed.py 64 $N $HOLD_RATE $HOLD_S 2>&1 | grep -E '^size'" > "$O/hold.txt" & gen=$!
for i in $(seq 1 90); do sleep 1; awk -v n=$N '$3>=n{f=1} END{exit !f}' "$O/samples.txt" && break; done
ev full; sleep 10
ev same-del;   $D "sudo ip neigh del $GEN_IP4 dev eth4"; sleep 5
ev same-pin;   pin4 $GEN_MAC4; sleep 15
ev mac-bogus;  pin4 $BOGUS_MAC; sleep 10
ev mac-back;   pin4 $GEN_MAC4; sleep 20
ev settle; wait $gen
ev post; sleep 3; kill $samp 2>/dev/null; wait $samp 2>/dev/null
$D 'sudo pkill -f "[l]adder-sampler.py"; rm -f /tmp/ladder-phase'
$D "sudo sh -c 'cd $TR && echo 0 > tracing_on && cat trace && echo nop > current_tracer && echo > set_graph_function'" > "$O/ftrace.txt"
pin4 $GEN_MAC4
echo "== hold: $(cat "$O/hold.txt")"
echo "== ask_flow_neigh_mac_changed calls (duration):"; grep -E "ask_flow_neigh_mac_changed" "$O/ftrace.txt" | head -20

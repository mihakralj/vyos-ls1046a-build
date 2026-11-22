#!/usr/bin/env bash
# testrig-gen-tune.sh — apply and verify the generator (dell1/dell2) runtime tuning before a benchmark.
#   Per data port (root-namespace GEN_IF and VENDOR_NS GEN_IF_NS): rings 4096/4096, one TxRx IRQ per CPU
#   (queue k -> CPU k), adaptive coalescing; per host: performance governor and C-states deeper than C1
#   disabled to reduce wake-up latency. Runtime only and idempotent; a reboot or a
#   driver reset can undo it, which is why the matrix drivers run it first.
# Usage: bin/testrig-gen-tune.sh [check]    (check = report only, exit 1 on drift)
set -eu
. "$(dirname "$0")/testrig-env.sh"
MODE=${1:-apply}
case $MODE in apply|check) ;; *) echo "Usage: $0 [apply|check]" >&2; exit 2 ;; esac
rig_require GEN1 GEN2
ssh_opts=(-o BatchMode=yes -o ConnectTimeout=5)
[ -z "$RIG_KEY" ] || ssh_opts+=(-i "$RIG_KEY")
printf -v remote 'sudo bash -s -- %q %q %q %q' "$MODE" "$GEN_IF" "$GEN_IF_NS" "$VENDOR_NS"
rc=0
for g in "$GEN1" "$GEN2"; do
  out=$(ssh "${ssh_opts[@]}" "$g" "$remote" 2>&1 <<'EOS'
set -euo pipefail
mode=$1; P1=$2; P2=$3; NS=$4; bad=0
E=/usr/sbin/ethtool
port(){
  local namespace=$1 i=$2 cur coal cpus channels entry irq queue want have carrier t n=0
  local -a ns=() irqs=()
  local -A queues=()
  [ -z "$namespace" ] || ns=(ip netns exec "$namespace")
  if ! "${ns[@]}" "$E" -i "$i" >/dev/null 2>&1; then
    echo "ERROR cannot access interface $i${namespace:+ in namespace $namespace}" >&2
    return 1
  fi
  rings(){
    "${ns[@]}" "$E" -g "$i" | awk '/^Current/{f=1; next} f && /^(RX|TX):/{printf "%s/", $2}'
  }
  coalescing(){
    "${ns[@]}" "$E" -c "$i" | awk '/^Adaptive RX:/{print $3 "/" $5}'
  }
  cur=$(rings) || { echo "ERROR cannot read $i rings" >&2; return 1; }
  [[ $cur =~ ^[0-9]+/[0-9]+/$ ]] || { echo "ERROR cannot parse $i rings: $cur" >&2; return 1; }
  if [ "$cur" != "4096/4096/" ]; then
    if [ "$mode" = check ]; then
      echo "DRIFT $i rings $cur"; bad=1
    else
      carrier=$("${ns[@]}" cat "/sys/class/net/$i/carrier")
      "${ns[@]}" "$E" -G "$i" rx 4096 tx 4096 || { echo "ERROR cannot set $i rings" >&2; return 1; }
      if [ "$carrier" = 1 ]; then
        for ((t=0; t<30; t++)); do
          carrier=$("${ns[@]}" cat "/sys/class/net/$i/carrier")
          [ "$carrier" = 1 ] && break
          sleep 1
        done
        [ "$carrier" = 1 ] || { echo "ERROR $i carrier did not return" >&2; return 1; }
      fi
      cur=$(rings) || { echo "ERROR cannot reread $i rings" >&2; return 1; }
      [ "$cur" = "4096/4096/" ] || { echo "ERROR $i rings remain $cur" >&2; return 1; }
    fi
  fi
  coal=$(coalescing) || { echo "ERROR cannot read $i coalescing" >&2; return 1; }
  [[ $coal =~ ^(on|off)/(on|off)$ ]] || { echo "ERROR cannot parse $i adaptive coalescing: $coal" >&2; return 1; }
  if [ "$coal" != "on/on" ]; then
    if [ "$mode" = check ]; then
      echo "DRIFT $i adaptive coalescing $coal"; bad=1
    else
      "${ns[@]}" "$E" -C "$i" adaptive-rx on adaptive-tx on || { echo "ERROR cannot set $i coalescing" >&2; return 1; }
      coal=$(coalescing) || { echo "ERROR cannot reread $i coalescing" >&2; return 1; }
      [ "$coal" = "on/on" ] || { echo "ERROR $i adaptive coalescing remains $coal" >&2; return 1; }
    fi
  fi
  cpus=$(nproc)
  [ "$cpus" -gt 0 ] || { echo "ERROR no CPUs available" >&2; return 1; }
  channels=$("${ns[@]}" "$E" -l "$i" | awk '/^Current/{f=1; next} f && /^Combined:/{print $2}') ||
    { echo "ERROR cannot read $i channels" >&2; return 1; }
  [[ $channels =~ ^[1-9][0-9]*$ ]] || { echo "ERROR cannot parse $i combined channels: $channels" >&2; return 1; }
  mapfile -t irqs < <(awk -F: -v p="i40e-$i-TxRx-" '
    (at=index($0,p)) {
      q=substr($0,at+length(p))
      if (match(q,/^[0-9]+/)) printf "%d:%s\n", $1+0, substr(q,1,RLENGTH)
    }' /proc/interrupts)
  for entry in "${irqs[@]}"; do
    queue=${entry#*:}; queue=$((10#$queue))
    if [ "$queue" -ge "$channels" ] || [ -n "${queues[$queue]:-}" ]; then
      echo "ERROR invalid or duplicate $i queue IRQ $entry" >&2; return 1
    fi
    queues[$queue]=1
    n=$((n+1))
  done
  [ "$n" -eq "$channels" ] || { echo "ERROR $i has $n queue IRQs for $channels channels" >&2; return 1; }
  for entry in "${irqs[@]}"; do
    irq=${entry%%:*}; queue=${entry#*:}; queue=$((10#$queue))
    want=$((queue % cpus))
    have=$(cat "/proc/irq/$irq/effective_affinity_list")
    if [ "$have" != "$want" ]; then
      if [ "$mode" = check ]; then
        echo "DRIFT $i irq $irq on $have"; bad=1
      else
        echo "$want" > "/proc/irq/$irq/smp_affinity_list"
        have=$(cat "/proc/irq/$irq/effective_affinity_list")
        [ "$have" = "$want" ] || { echo "ERROR $i irq $irq remains on $have" >&2; return 1; }
      fi
    fi
  done
  echo "$i rings $cur adaptive $coal irqs $n"
}
port "" "$P1"
port "$NS" "$P2"
deep=0; governors=0
for c in /sys/devices/system/cpu/cpu[0-9]*; do
  governor=$c/cpufreq/scaling_governor
  if [ -e "$governor" ]; then
    governors=$((governors+1))
    if [ "$(cat "$governor")" != performance ]; then
      if [ "$mode" = check ]; then
        echo "DRIFT ${c##*/} governor"; bad=1
      else
        echo performance > "$governor"
        [ "$(cat "$governor")" = performance ] || { echo "ERROR ${c##*/} governor unchanged" >&2; exit 1; }
      fi
    fi
  fi
  for s in "$c"/cpuidle/state[0-9]*; do
    [ -e "$s/disable" ] || continue
    state=${s##*/state}
    (( 10#$state >= 2 )) || continue
    [ "$(cat "$s/disable")" = 1 ] && continue
    if [ "$mode" = check ]; then
      deep=$((deep+1))
    else
      echo 1 > "$s/disable"
      [ "$(cat "$s/disable")" = 1 ] || { echo "ERROR $s remains enabled" >&2; exit 1; }
    fi
  done
done
[ "$governors" -gt 0 ] || { echo "ERROR no CPU governors found" >&2; exit 1; }
[ "$deep" = 0 ] || { echo "DRIFT $deep idle states deeper than C1 enabled"; bad=1; }
if systemctl is-active -q irqbalance; then
  if [ "$mode" = check ]; then
    echo "DRIFT irqbalance running"; bad=1
  else
    systemctl stop irqbalance
    if systemctl is-active -q irqbalance; then
      echo "ERROR irqbalance is still running" >&2; exit 1
    else
      status=$?
      [ "$status" = 3 ] || [ "$status" = 4 ] || { echo "ERROR cannot verify irqbalance (status $status)" >&2; exit 1; }
    fi
  fi
else
  status=$?
  [ "$status" = 3 ] || [ "$status" = 4 ] || { echo "ERROR cannot query irqbalance (status $status)" >&2; exit 1; }
fi
echo "deep idle states enabled: $deep; governors: $(sort -u /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor | tr '\n' ' ')"
exit "$bad"
EOS
) || rc=1
  while IFS= read -r line; do printf '%s: %s\n' "$g" "$line"; done <<< "$out"
done
exit "$rc"

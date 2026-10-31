#!/usr/bin/env bash
# testrig-sshprobe.sh IP SECS: one TCP connect to port 22 per second, each must return an "SSH-" banner.
# Prints "SSH <ok> <failed>". Used by testrig-suite-abcd.sh D2 (control-plane isolation under a flood).
ip=${1:?ip}; secs=${2:-28}; ok=0; bad=0
for _ in $(seq 1 "$secs"); do
  if timeout 3 bash -c 'exec 3<>/dev/tcp/$0/22; head -c4 <&3' "$ip" 2>/dev/null | grep -q '^SSH-'; then ok=$((ok + 1)); else bad=$((bad + 1)); fi
  sleep 1
done
echo "SSH $ok $bad"

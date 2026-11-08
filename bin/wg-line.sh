#!/usr/bin/env bash
# wg-line.sh — ASK2-only WireGuard line: up / down / bidir x inner IPv4 / IPv6, six medians on one line.
#   1 tunnel, 4 TCP streams, MTU 1420, 30 s runs (first 10 s discarded), a fresh tunnel every rep
#   (bin/wg-parity.sh ask2 does the runs; no vendor DUT is touched).
# Usage: [WGFP=1] [REPS=3] [PORT=eth4] bin/wg-line.sh
#   WGFP=1 binds the fast path for the run (mode "wgfp"), otherwise the software path (mode "sw").
#   PORT is the DUT port carrying the encrypted leg (eth4 default, eth3 mirrors the topology).
# Output: the line on stdout, appended to $OUTDIR/wg-line.txt (header written once); raw rows in
#   $OUTDIR/wgline-<stamp>-<mode>-185-<port>.csv. Never run it next to other rig traffic.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
rig_require ASK2_DUT
PORT=${PORT:-eth4}
OUTDIR=${OUTDIR:-$RIG_OUT}
MODE=sw; [ "${WGFP:-0}" = 1 ] && MODE=wgfp
TAG=wgline-$(date +%Y%m%d-%H%M)-$MODE
IMG=$(ssh -o BatchMode=yes $RIG_SSHK "$ASK2_DUT" "tr ' ' '\n' </proc/cmdline | sed -n 's#^vyos-union=/boot/##p'")
[ -n "$IMG" ] || IMG=unknown-image
export OUTDIR
PORTS=$PORT TAG=$TAG CELLS="wg-v4 wg-v6" DIRS="up down bidir" "$HERE/wg-parity.sh" ask2 || exit
CSV=$OUTDIR/$TAG-185-$PORT.csv
[ -s "$CSV" ] || { echo "no results in $CSV" >&2; exit 1; }
HIST=$OUTDIR/wg-line.txt
LABEL=$(printf '%-24s %-4s %-4s' "$IMG" "$MODE" "$PORT")
[ -s "$HIST" ] || printf '%-24s %-4s %-4s  %s\n' image mode port "$(python3 "$HERE/wg-line.py" --header "$CSV" | head -1)" > "$HIST"
L=$(python3 "$HERE/wg-line.py" "$CSV" --label "$LABEL")
echo "$L" | tee -a "$HIST"

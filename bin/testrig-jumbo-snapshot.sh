#!/usr/bin/env bash
# Capture both generator paths while one DUT is configured for jumbo frames.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
rig_require GEN1 GEN2 GEN_IF GEN_IF_NS VENDOR_NS

[ "$#" = 2 ] || { echo "usage: $0 absolute-directory before|after" >&2; exit 2; }
directory=$1 phase=$2
[[ $directory = /* && -d $directory && ! -L $directory ]] ||
  { echo "ERROR: snapshot directory must be an existing absolute directory" >&2; exit 2; }
case "$phase" in before|after) ;; *) echo "ERROR: invalid snapshot phase" >&2; exit 2 ;; esac
for name in "$GEN_IF" "$GEN_IF_NS" "$VENDOR_NS"; do
  [[ $name =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "ERROR: invalid interface or namespace" >&2; exit 2; }
done
[[ -z $RIG_KEY || -r $RIG_KEY ]] || { echo "ERROR: cannot read RIG_KEY" >&2; exit 2; }

for name in "generator-tuning-$phase.txt" "gen1-$phase.json" "gen2-$phase.json"; do
  [[ ! -e $directory/$name && ! -L $directory/$name ]] ||
    { echo "ERROR: snapshot already exists: $directory/$name" >&2; exit 1; }
done
set -C
"$HERE/testrig-gen-tune.sh" check > "$directory/generator-tuning-$phase.txt"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8)
[[ -z $RIG_KEY ]] || SSH+=(-i "$RIG_KEY")
index=0
for host in "$GEN1" "$GEN2"; do
  index=$((index + 1))
  vlan=$((index * 10))
  "${SSH[@]}" "$host" "sudo -n python3 - snapshot $GEN_IF $GEN_IF_NS $VENDOR_NS $vlan" \
    < "$HERE/testrig_parity.py" > "$directory/gen$index-$phase.json"
  python3 -m json.tool "$directory/gen$index-$phase.json" >/dev/null
done

#!/bin/sh
# Manage a uniquely tagged NOTRACK table for one WireGuard test lease.
set -eu

operation=${1:-}
token=${2:-}
if ! printf '%s\n' "$token" | grep -Eq '^[0-9a-f]{32}$'; then
  echo "ERROR: invalid NOTRACK ownership token" >&2
  exit 2
fi
table="wgn$token"
comment="wgrig-$token"

case $operation in
  add)
    if [ "$#" -ne 4 ]; then
      echo "ERROR: expected add <token> <mode> <port>" >&2
      exit 2
    fi
    mode=$3
    port=$4
    case $port in ''|*[!0-9]*) echo "ERROR: invalid UDP port" >&2; exit 2;; esac
    [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || exit 2
    case $mode in
      1) incoming="udp dport $port notrack"; outgoing="udp sport $port notrack";;
      2) incoming=notrack; outgoing=notrack;;
      *) echo "ERROR: invalid NOTRACK mode" >&2; exit 2;;
    esac
    nft -f - <<EOF
create table inet $table { comment "$comment"; }
add chain inet $table pre { type filter hook prerouting priority -300; }
add chain inet $table out { type filter hook output priority -300; }
add rule inet $table pre $incoming
add rule inet $table out $outgoing
EOF
    ;;
  delete)
    [ "$#" -eq 2 ] || { echo "ERROR: expected delete <token>" >&2; exit 2; }
    listing=$(nft -a list table inet "$table") || {
      echo "ERROR: cannot inspect owned NOTRACK table $table" >&2
      exit 1
    }
    handle=$(printf '%s\n' "$listing" | awk -v table="$table" -v token="$comment" '
      NR == 1 && $1 == "table" && $2 == "inet" && $3 == table {
        for (i = 1; i < NF; i++) if ($i == "handle" && $(i + 1) ~ /^[0-9]+$/) handle = $(i + 1)
      }
      $1 == "comment" && $2 == "\"" token "\"" && NF == 2 { owned = 1 }
      END { if (handle && owned) print handle; else exit 1 }') || {
        echo "ERROR: NOTRACK table ownership changed; refusing to remove $table" >&2
        exit 1
      }
    nft delete table inet handle "$handle"
    ;;
  *)
    echo "ERROR: expected add or delete" >&2
    exit 2
    ;;
esac

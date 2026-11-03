#!/bin/sh
# Manage test links by a unique kernel alternative name, never by a fixed name.
set -eu

if [ "$#" -ne 3 ]; then
  echo "usage: $0 create|delete <interface> <ownership-name>" >&2
  exit 2
fi
operation=$1
name=$2
owner=$3
if ! printf '%s\n' "$name" | grep -Eq '^[A-Za-z0-9_.-]{1,15}$' ||
   ! printf '%s\n' "$owner" | grep -Eq '^wgo-[0-9a-f]{32}-[a-z0-9]+$'; then
  echo "ERROR: invalid test interface or ownership name" >&2
  exit 2
fi

case $operation in
  create)
    suffix=${owner#wgo-}
    tmp="wt$(printf '%s' "$suffix" | cut -c1-10)"
    links=$(ip -o link show) || { echo "ERROR: cannot inspect links" >&2; exit 1; }
    if printf '%s\n' "$links" | awk -v name="$tmp" '
      { current=$2; sub(/:$/, "", current); sub(/@.*/, "", current); if (current == name) found=1 }
      END { exit !found }'; then
      echo "ERROR: temporary link $tmp already exists" >&2
      exit 10
    fi
    if ! ip link add "$tmp" type wireguard; then
      echo "ERROR: temporary link creation outcome is uncertain; retain the lease" >&2
      exit 1
    fi
    if ! ip link property add dev "$tmp" altname "$owner"; then
      echo "ERROR: cannot verify ownership of temporary link $tmp; retain the lease" >&2
      exit 1
    fi
    if ! ip link set dev "$tmp" name "$name"; then
      echo "ERROR: cannot rename owned link to $name" >&2
      if ip link del "$owner"; then exit 12; fi
      echo "ERROR: owned link $owner requires cleanup" >&2
      exit 1
    fi
    ;;
  delete)
    links=$(ip -o link show) || { echo "ERROR: cannot inspect links" >&2; exit 1; }
    if printf '%s\n' "$links" | awk -v owner="$owner" '
      { for (i=1; i<NF; i++) if ($i == "altname" && $(i+1) == owner) found=1 }
      END { exit !found }'; then
      ip link del "$owner"
    elif printf '%s\n' "$links" | awk -v name="$name" '
      { current=$2; sub(/:$/, "", current); sub(/@.*/, "", current); if (current == name) found=1 }
      END { exit !found }'; then
      echo "ERROR: $name exists without its ownership name; refusing cleanup" >&2
      exit 1
    fi
    ;;
  *)
    echo "ERROR: expected create or delete" >&2
    exit 2
    ;;
esac

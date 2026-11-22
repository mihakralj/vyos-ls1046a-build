#!/bin/sh
set -eu

die() { echo "ERROR: $*" >&2; exit 1; }
[ "$#" -eq 4 ] || die "expected join|leave, port, bridge and owner"
op=$1 port=$2 bridge=$3 owner=$4
case "$port" in eth3|eth4) ;; *) die "invalid bridge test port" ;; esac
printf '%s\n' "$owner" | grep -Eq '^ask2wan-[0-9a-f]{16}-br$' || die "invalid bridge owner"
token=${owner#ask2wan-}
token=${token%-br}
expected="brt$(printf '%s' "$token" | cut -c1-8)"
[ "$bridge" = "$expected" ] || die "bridge name does not match owner"

master() {
    link=$(ip -o link show dev "$port") || die "cannot inspect $port"
    printf '%s\n' "$link" | awk '{for (i=1; i<NF; i++) if ($i=="master") {print $(i+1); exit}}'
}

check_bridge() {
    link=$(ip -d -o link show dev "$bridge") || die "cannot inspect $bridge"
    printf '%s\n' "$link" | awk -v owner="$owner" '
      {for (i=1; i<=NF; i++) {if ($i=="bridge") type=1; if ($i=="altname" && $(i+1)==owner) owned=1}}
      END {exit !(type && owned)}' || die "bridge ownership changed"
}

current=$(master)
case "$op" in
    join)
        [ -z "$current" ] || die "$port already has a master"
        check_bridge
        ip link set dev "$port" master "$bridge"
        [ "$(master)" = "$bridge" ] || die "$port did not join the owned bridge"
        ;;
    leave)
        [ -n "$current" ] || exit 0
        [ "$current" = "$bridge" ] || die "$port has a different master"
        check_bridge
        ip link set dev "$port" nomaster
        [ -z "$(master)" ] || die "$port did not leave the bridge"
        ;;
    *) die "expected join or leave" ;;
esac

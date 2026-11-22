#!/bin/sh
# Claim an existing test link by altname and change only its owned MTU.
set -eu

die() { echo "ERROR: $*" >&2; exit 1; }
[ "$#" -eq 8 ] || die "expected operation, device, ifindex, iflink, MAC, owner, old MTU and new MTU"
op=$1 dev=$2 index=$3 link=$4 mac=$5 owner=$6 before=$7 after=$8
printf '%s\n' "$dev" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$' || die "invalid device"
case "$dev" in eth0|eth0.*) die "eth0 is not a test port";; esac
for n in "$index" "$link"; do
  case "$n" in ''|*[!0-9]*) die "invalid interface identity";; esac
  [ "$n" -gt 0 ] || die "invalid interface identity"
done
printf '%s\n' "$mac" | grep -Eq '^([0-9a-f]{2}:){5}[0-9a-f]{2}$' || die "invalid MAC"
printf '%s\n' "$owner" | grep -Eq '^t2mtu-[0-9a-f]{16}-[a-z0-9]{1,6}$' || die "invalid owner"
case "$before:$after" in 1500:9000|9000:1500) ;; *) die "invalid MTU transition";; esac

identity() {
  actual_index=$(cat "/sys/class/net/$dev/ifindex") || die "cannot read $dev ifindex"
  actual_link=$(cat "/sys/class/net/$dev/iflink") || die "cannot read $dev iflink"
  actual_mac=$(cat "/sys/class/net/$dev/address") || die "cannot read $dev MAC"
  [ "$actual_index" = "$index" ] && [ "$actual_link" = "$link" ] && [ "$actual_mac" = "$mac" ] ||
    die "$dev identity changed"
}
state() {
  identity
  mtu=$(cat "/sys/class/net/$dev/mtu") || die "cannot read $dev MTU"
  info=$(ip -d -o link show dev "$dev") || die "cannot inspect $dev"
  markers=$(printf '%s\n' "$info" | awk '
    {for (i=1; i<NF; i++) if ($i=="altname" && $(i+1) ~ /^t2mtu-/) print $(i+1)}')
}

state
case "$op" in
  claim)
    [ "$mtu" = "$before" ] && [ -z "$markers" ] || die "$dev MTU or ownership conflicts"
    ip link property add dev "$dev" altname "$owner" || die "$dev claim outcome uncertain"
    state
    [ "$mtu" = "$before" ] && [ "$markers" = "$owner" ] || die "$dev claim changed"
    ;;
  set)
    [ "$markers" = "$owner" ] || die "$dev is not exclusively owned"
    [ "$mtu" = "$after" ] && exit 0
    [ "$mtu" = "$before" ] || die "$dev MTU changed externally"
    ip link set dev "$dev" mtu "$after" || die "$dev MTU change outcome uncertain"
    state
    [ "$mtu" = "$after" ] && [ "$markers" = "$owner" ] || die "$dev MTU change did not stick"
    ;;
  check)
    [ "$mtu" = "$before" ] && [ "$markers" = "$owner" ] || die "$dev MTU or ownership changed"
    ;;
  release)
    [ "$mtu" = 1500 ] || die "$dev MTU is not restored"
    [ -n "$markers" ] || exit 0
    [ "$markers" = "$owner" ] || die "$dev ownership changed"
    ip link property del dev "$dev" altname "$owner" || die "$dev release outcome uncertain"
    state
    [ "$mtu" = 1500 ] && [ -z "$markers" ] || die "$dev release did not stick"
    ;;
  *) die "expected claim, set, check or release";;
esac

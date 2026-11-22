#!/bin/sh
# Own the short-lived NAT tables used by the vendor jumbo benchmark.
set -eu

die() { echo "ERROR: $*" >&2; exit 1; }
[ "$#" -ge 2 ] || die "expected create or delete and owner token"
op=$1 token=$2
printf '%s\n' "$token" | grep -Eq '^[0-9a-f]{32}$' || die "invalid owner token"
table="vwn$token"
owner="dut2-wan-$token"

valid_ipv4() {
  printf '%s\n' "$1" | awk -F. '
    NR != 1 || NF != 4 { exit 1 }
    {for (i=1; i<=4; i++) if ($i !~ /^[0-9]+$/ || $i+0 > 255 || $i ~ /^0[0-9]/) exit 1}
    END {if (NR != 1) exit 1}'
}
valid_ipv6() {
  printf '%s\n' "$1" | grep -Eq '^[0-9a-fA-F:]{2,39}$' &&
    case "$1" in *:*) true;; *) false;; esac
}

case "$op" in
  create)
    [ "$#" -ge 3 ] || die "create needs empty or nat"
    mode=$3
    case "$mode" in
      empty) [ "$#" -eq 3 ] || die "empty takes no addresses";;
      nat)
        [ "$#" -eq 7 ] || die "nat needs four endpoint addresses"
        valid_ipv4 "$4" && valid_ipv4 "$5" && valid_ipv6 "$6" && valid_ipv6 "$7" ||
          die "invalid NAT endpoint"
        ;;
      *) die "expected empty or nat";;
    esac
    {
      printf 'create table ip %s { comment "%s"; }\n' "$table" "$owner"
      printf 'add chain ip %s post { type nat hook postrouting priority srcnat; }\n' "$table"
      printf 'create table ip6 %s { comment "%s"; }\n' "$table" "$owner"
      printf 'add chain ip6 %s post { type nat hook postrouting priority srcnat; }\n' "$table"
      if [ "$mode" = nat ]; then
        printf 'add rule ip %s post oifname "eth4" ip saddr %s ip daddr %s masquerade comment "%s-nat4up"\n' "$table" "$4" "$5" "$owner"
        printf 'add rule ip %s post oifname "eth3" ip saddr %s ip daddr %s masquerade comment "%s-nat4down"\n' "$table" "$5" "$4" "$owner"
        printf 'add rule ip6 %s post oifname "eth4" ip6 saddr %s ip6 daddr %s masquerade comment "%s-nat6up"\n' "$table" "$6" "$7" "$owner"
        printf 'add rule ip6 %s post oifname "eth3" ip6 saddr %s ip6 daddr %s masquerade comment "%s-nat6down"\n' "$table" "$7" "$6" "$owner"
      fi
    } | nft -f -
    ;;
  delete)
    [ "$#" -eq 2 ] || die "delete takes only the owner token"
    tables=$(nft list tables) || die "cannot inspect nft tables"
    deletes=
    for family in ip ip6; do
      present=$(printf '%s\n' "$tables" | awk -v f="$family" -v t="$table" \
        '$1=="table" && $2==f && $3==t {print 1}')
      [ -n "$present" ] || continue
      listing=$(nft -a list table "$family" "$table") || die "cannot inspect $family $table"
      handle=$(printf '%s\n' "$listing" | awk -v f="$family" -v t="$table" -v owner="$owner" '
        NR==1 && $1=="table" && $2==f && $3==t {
          for (i=1; i<NF; i++) if ($i=="handle" && $(i+1) ~ /^[0-9]+$/) handle=$(i+1)
        }
        $1=="comment" && $2=="\"" owner "\"" && NF==2 {owned=1}
        END {if (handle && owned) print handle; else exit 1}
      ') || die "ownership changed for $family $table"
      deletes="${deletes}delete table $family handle $handle
"
    done
    [ -z "$deletes" ] || printf '%s' "$deletes" | nft -f -
    ;;
  *) die "expected create or delete";;
esac

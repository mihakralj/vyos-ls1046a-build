#!/bin/sh
set -eu

op=${1:-}
token=${2:-}
case "$token" in *[!0-9a-f]*|'') echo "ERROR: invalid WAN NAT token" >&2; exit 2;; esac
[ "${#token}" -eq 32 ] || { echo "ERROR: invalid WAN NAT token" >&2; exit 2; }
table="vwn$token"
owner="ask2-wan-$token"

delete_owned() {
    family=$1
    tables=$(nft list tables)
    present=$(printf '%s\n' "$tables" |
        awk -v f="$family" -v t="$table" '$1=="table" && $2==f && $3==t {print 1}')
    [ -n "$present" ] || return 0
    listing=$(nft -a list table "$family" "$table") ||
        { echo "ERROR: cannot inspect $family WAN NAT table" >&2; return 1; }
    handle=$(printf '%s\n' "$listing" | awk -v owner="$owner" '
        /^table / {
            for (i=1; i<NF; i++) if ($i=="handle") handle=$(i+1)
        }
        $1=="comment" && $2=="\"" owner "\"" {owned=1}
        END {if (handle && owned) print handle; else exit 1}
    ') || { echo "ERROR: WAN NAT table ownership changed" >&2; return 1; }
    nft delete table "$family" handle "$handle"
}

case "$op" in
    add)
        [ "$#" -eq 2 ] || { echo "ERROR: add requires only the token" >&2; exit 2; }
        nft -f - <<EOF
create table ip $table { comment "$owner"; }
add chain ip $table post { type nat hook postrouting priority srcnat; }
add rule ip $table post oifname "pppoe7" ip saddr 10.99.2.0/24 ip daddr 10.99.57.1 masquerade comment "$owner-ppp"
add rule ip $table post oifname "wgw0" ip saddr 10.99.2.0/24 ip daddr 10.99.230.112 masquerade comment "$owner-wg"
add chain ip $table pre { type nat hook prerouting priority dstnat; }
add rule ip $table pre iifname "eth3" ip daddr 10.99.1.185 tcp dport 5201 dnat to 10.99.2.113 comment "$owner-dnat4"
create table ip6 $table { comment "$owner"; }
add chain ip6 $table pre { type nat hook prerouting priority dstnat; }
add rule ip6 $table pre iifname "eth3" ip6 daddr fd99:1::185 tcp dport 5201 dnat to fd99:2::113 comment "$owner-dnat6"
EOF
        ;;
    delete)
        [ "$#" -eq 2 ] || { echo "ERROR: delete requires only the token" >&2; exit 2; }
        delete_owned ip6
        delete_owned ip
        ;;
    *) echo "ERROR: expected add or delete" >&2; exit 2 ;;
esac

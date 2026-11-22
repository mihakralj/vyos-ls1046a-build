#!/bin/sh
set -eu

op=${1:-}
token=${2:-}
case "$token" in *[!0-9a-f]*|'') echo "ERROR: invalid WAN owner token" >&2; exit 2;; esac
[ "${#token}" -eq 32 ] || { echo "ERROR: invalid WAN owner token" >&2; exit 2; }
table="vwn$token"
owner="dut2-wan-$token"

valid_ipv4() {
    printf '%s\n' "$1" | awk -F. '
        NR != 1 || NF != 4 { exit 1 }
        { for (i=1; i<=4; i++) if ($i !~ /^[0-9]+$/ || $i+0 > 255 || $i ~ /^0[0-9]/) exit 1 }
        END { if (NR != 1) exit 1 }
    '
}

case "$op" in
    add)
        [ "$#" -eq 5 ] || { echo "ERROR: add requires source, PPP and WireGuard IPv4 addresses" >&2; exit 2; }
        for address in "$3" "$4" "$5"; do
            valid_ipv4 "$address" || { echo "ERROR: invalid WAN NAT IPv4 address" >&2; exit 2; }
        done
        nft -f - <<EOF
create table ip $table { comment "$owner"; }
add chain ip $table post { type nat hook postrouting priority srcnat; }
add rule ip $table post oifname "pppoe7" ip saddr $3 ip daddr $4 masquerade comment "$owner-ppp"
add rule ip $table post oifname "wgw0" ip saddr $3 ip daddr $5 masquerade comment "$owner-wg"
EOF
        ;;
    delete)
        [ "$#" -eq 2 ] || { echo "ERROR: delete requires only the WAN owner token" >&2; exit 2; }
        tables=$(nft list tables)
        present=$(printf '%s\n' "$tables" | awk -v t="$table" '$1=="table" && $2=="ip" && $3==t {print 1}')
        [ -n "$present" ] || exit 0
        listing=$(nft -a list table ip "$table") || {
            echo "ERROR: cannot inspect WAN NAT table" >&2; exit 1;
        }
        handle=$(printf '%s\n' "$listing" | awk -v t="$table" -v owner="$owner" '
            NR==1 && $1=="table" && $2=="ip" && $3==t {
                for (i=1; i<NF; i++) if ($i=="handle" && $(i+1) ~ /^[0-9]+$/) handle=$(i+1)
            }
            $1=="comment" && $2=="\"" owner "\"" && NF==2 {owned=1}
            END {if (handle && owned) print handle; else exit 1}
        ') || { echo "ERROR: WAN NAT table ownership changed" >&2; exit 1; }
        nft delete table ip handle "$handle"
        ;;
    *) echo "ERROR: expected add or delete" >&2; exit 2 ;;
esac

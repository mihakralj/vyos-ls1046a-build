#!/bin/sh
# Owned resources for bin/testrig-ask2-wan.sh, run as root on the DUT or a generator (sh -s over ssh).
# Every create refuses an existing resource and leaves an ownership marker; every delete removes only a
# resource that still carries the marker, treats an absent one as done, and refuses anything else.
#   link-create <name> <owner> <ip-link-add args...>   create under a temp name, add altname <owner>, rename
#   link-delete <name> <owner>                         delete by altname; <name> without it is refused
#   route-add|route-delete <dest> <gw> <proto> <metric>     main-table route tagged by proto and metric
#   rundir-create|rundir-delete <dir> <owner>          a private directory with an owner file
#   rule6-delete <pref> <table> <proto> <from> <to>    an IPv6 rule and its table's routes, matched by proto
#   service-stop <unit> <boot-id>                       stop an owned transient PPPoE service and verify drain
set -eu

die(){ echo "ERROR: $*" >&2; exit "${rc:-1}"; }
valid(){ printf '%s\n' "$2" | grep -Eq "$1" || rc=2 die "invalid argument '$2'"; }
family(){ case $1 in *:*) echo -6;; *) echo -4;; esac; }
link_exists(){ ip -o link show | awk -v n="$1" '{c=$2; sub(/:$/,"",c); sub(/@.*/,"",c); if (c==n) f=1} END{exit !f}'; }
link_owned(){ ip -o link show | awk -v o="$1" '{for(i=1;i<NF;i++) if($i=="altname" && $(i+1)==o) f=1} END{exit !f}'; }

[ "$#" -ge 1 ] || rc=2 die "usage: $0 <operation> ..."
op=$1; shift
case $op in
  link-create)
    [ "$#" -ge 3 ] || rc=2 die "link-create <name> <owner> <ip-link-add args...>"
    name=$1 owner=$2; shift 2
    valid '^[A-Za-z0-9_.-]{1,15}$' "$name"; valid '^ask2wan-[0-9a-f]{16}-[a-z0-9]+$' "$owner"
    tmp="wn$(printf '%s' "$owner" | cut -c9-18)"
    if link_exists "$name" || link_exists "$tmp" || link_owned "$owner"; then rc=10 die "link $name/$tmp/$owner already exists"; fi
    ip link add "$tmp" "$@" || die "link creation outcome uncertain; lease retained"
    if ! ip link property add dev "$tmp" altname "$owner"; then
      ip link del "$tmp" || die "cannot mark or remove $tmp; lease retained for manual recovery"
      die "cannot mark owned link"
    fi
    ip link set dev "$owner" name "$name" || die "cannot rename owned link to $name; lease retained"
    ;;
  link-delete)
    [ "$#" -eq 2 ] || rc=2 die "link-delete <name> <owner>"
    name=$1 owner=$2
    valid '^[A-Za-z0-9_.-]{1,15}$' "$name"; valid '^ask2wan-[0-9a-f]{16}-[a-z0-9]+$' "$owner"
    tmp="wn$(printf '%s' "$owner" | cut -c9-18)"
    if link_owned "$owner"; then ip link del "$owner"
    elif link_exists "$name"; then die "$name exists without ownership name $owner; refusing"; fi
    if link_exists "$tmp"; then die "$tmp remains without an ownership altname; refusing cleanup"; fi
    ;;
  route-add|route-delete)
    [ "$#" -eq 4 ] || rc=2 die "$op <dest> <gw> <proto> <metric>"
    dest=$1 gw=$2 proto=$3 metric=$4; fam=$(family "$dest")
    valid '^[0-9a-f.:]+/[0-9]+$' "$dest"; valid '^[0-9a-f.:]+$' "$gw"; valid '^[0-9]+$' "$proto"; valid '^[0-9]+$' "$metric"
    if [ "$op" = route-add ]; then
      [ -z "$(ip "$fam" route show exact "$dest")" ] || rc=10 die "a route for $dest already exists"
      ip "$fam" route add "$dest" via "$gw" proto "$proto" metric "$metric" || die "route outcome uncertain; lease retained"
    elif [ -n "$(ip "$fam" route show exact "$dest" proto "$proto" metric "$metric" via "$gw")" ]; then
      ip "$fam" route del "$dest" via "$gw" proto "$proto" metric "$metric"
    fi
    ;;
  rundir-create|rundir-delete)
    [ "$#" -eq 2 ] || rc=2 die "$op <dir> <owner>"
    dir=$1 owner=$2
    valid '^/var/lib/ask2-wan-[0-9a-f]{16}$' "$dir"; valid '^ask2wan-[0-9a-f]{16}-[a-z0-9]+$' "$owner"
    if [ "$op" = rundir-create ]; then
      mkdir -m 700 "$dir" || rc=10 die "cannot create $dir (exists?)"
      printf '%s\n' "$owner" > "$dir/owner"
    elif [ -e "$dir" ]; then
      [ -d "$dir" ] && [ ! -L "$dir" ] && [ "$(cat "$dir/owner" 2>/dev/null)" = "$owner" ] || die "$dir is not owned by $owner; refusing"
      rm -rf -- "$dir"
    fi
    ;;
  rule6-delete)
    [ "$#" -eq 5 ] || rc=2 die "rule6-delete <pref> <table> <proto> <from> <to>"
    pref=$1 table=$2 proto=$3 from=$4 to=$5
    for v in "$pref" "$table" "$proto"; do valid '^[0-9]+$' "$v"; done
    while [ -n "$(ip -6 rule show pref "$pref" from "$from" to "$to" lookup "$table" | head -1)" ]; do
      ip -N -6 rule show pref "$pref" | grep -q " proto $proto\b" || die "IPv6 rule $pref is not tagged proto $proto; refusing"
      ip -6 rule del pref "$pref" from "$from" to "$to" lookup "$table" protocol "$proto"
    done
    ip -6 route flush table "$table" proto "$proto" 2>/dev/null || true
    ;;
  service-stop)
    [ "$#" -eq 2 ] || rc=2 die "service-stop <unit> <boot-id>"
    unit=$1 boot=$2
    valid '^ask2wan-[0-9a-f]{16}\.service$' "$unit"
    valid '^[0-9a-f-]{36}$' "$boot"
    [ "$(cat /proc/sys/kernel/random/boot_id)" = "$boot" ] ||
      die "generator boot changed since service $unit was created"
    state=$(systemctl show --property=LoadState --value "$unit" 2>/dev/null) ||
      state=not-found
    [ "$state" != not-found ] || exit 0
    cgroup=$(systemctl show --property=ControlGroup --value "$unit" 2>/dev/null) ||
      die "cannot identify cgroup for $unit"
    systemctl stop "$unit" || die "cannot stop $unit"
    i=0
    while [ "$i" -lt 100 ]; do
      active=$(systemctl is-active "$unit" 2>/dev/null || true)
      tasks=
      [ -z "$cgroup" ] || [ ! -e "/sys/fs/cgroup$cgroup/cgroup.procs" ] ||
        tasks=$(cat "/sys/fs/cgroup$cgroup/cgroup.procs" 2>/dev/null)
      if [ "$active" != active ] && [ "$active" != activating ] &&
         [ "$active" != deactivating ] && [ -z "$tasks" ]; then
        systemctl reset-failed "$unit" 2>/dev/null || true
        exit 0
      fi
      i=$((i + 1))
      sleep 0.1
    done
    die "$unit or its cgroup did not drain"
    ;;
  *) rc=2 die "unknown operation $op" ;;
esac

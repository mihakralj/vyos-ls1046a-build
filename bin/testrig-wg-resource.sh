# shellcheck shell=bash
# Journal remote mutations before attempting them; uncertain operations retain the lease.

wg_resource_add() {
  local name=$1 marker rc
  shift
  [[ $name =~ ^[a-z0-9-]+$ ]] || return 1
  marker="$WG_LEASE_DIR/$name"
  if [ -e "$marker" ] || [ -L "$marker" ]; then
    echo "ERROR: resource already recorded: $name" >&2
    return 1
  fi
  printf 'pending\n' > "$marker" || return 1
  if "$@"; then
    printf 'created\n' > "$marker"
  else
    rc=$?
    case $rc in 10|11|12) rm -- "$marker" || return 1;; esac
    return 1
  fi
}

wg_resource_remove() {
  local name=$1 marker
  shift
  [[ $name =~ ^[a-z0-9-]+$ ]] || return 1
  marker="$WG_LEASE_DIR/$name"
  [ -e "$marker" ] || [ -L "$marker" ] || return 0
  if [ -L "$marker" ] || [ ! -f "$marker" ] || [ "$(cat "$marker")" != created ]; then
    echo "ERROR: resource $name has uncertain ownership; retain lease $WG_LEASE_DIR" >&2
    return 1
  fi
  "$@" && rm -- "$marker"
}

wg_server_start() {
  local role=$1 port=$2 directory="/tmp/wgrig-srv-$WG_OWNER_TOKEN-$1"
  wg_server_run "$role" "python3 - start $directory $port" < "$WG_SERVER_HELPER"
}

wg_server_stop() {
  local role=$1 port=$2 directory="/tmp/wgrig-srv-$WG_OWNER_TOKEN-$1"
  local identity pid started recorded
  identity=$(wg_server_run "$role" "python3 - identity $directory $port" < "$WG_SERVER_HELPER") || return 1
  if ! [[ $identity =~ ^[0-9]+:[0-9]+:[0-9]+$ ]]; then
    echo "ERROR: invalid server $role identity" >&2
    return 1
  fi
  IFS=: read -r pid started recorded <<< "$identity"
  [ "$recorded" = "$port" ] || return 1
  wg_server_run "$role" "python3 - $pid $started server $port" < "$WG_STOP_HELPER" || return 1
  wg_server_run "$role" "python3 - remove $directory $port" < "$WG_SERVER_HELPER"
}

wg_unbind_expected() {
  local expected=$1 state names name marker="$WG_LEASE_DIR/binding" overlaps=0
  [ "$DUT_KIND" = ask2 ] || return 0
  state=$(dut 'sudo cat /sys/module/wireguard/parameters/wgfp_peer') ||
    { echo "ERROR: cannot inspect WGFP before cleanup" >&2; return 1; }
  if [ "$state" = off ]; then
    if [ -e "$marker" ]; then rm -- "$marker" || return 1; fi
    return 0
  fi
  case $state in
    on*" dev="*) names=${state##* dev=};;
    *) echo "ERROR: unrecognized WGFP state; refusing cleanup" >&2; return 1;;
  esac
  for name in $expected; do
    case " $names " in *" $name "*) overlaps=1;; esac
  done
  if [ "$overlaps" = 0 ]; then
    if [ -e "$marker" ]; then rm -- "$marker" || return 1; fi
    return 0
  fi
  if [ "$WG_LEASE_WGFP" != 1 ] || [ "$names" != "$expected" ] ||
     [ -L "$marker" ] || [ ! -f "$marker" ] || [ "$(cat "$marker")" != created ]; then
    echo "ERROR: WGFP binding is not owned by this lease; refusing cleanup" >&2
    return 1
  fi
  wg_bound_links_owned || return 1
  dut "echo off | sudo tee /sys/module/wireguard/parameters/wgfp_peer >/dev/null" || return 1
  [ "$(dut 'sudo cat /sys/module/wireguard/parameters/wgfp_peer')" = off ] || return 1
  rm -- "$marker"
}

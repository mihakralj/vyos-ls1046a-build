# shellcheck shell=bash
# Journal remote mutations before attempting them; uncertain operations retain the lease.

# A failed reservation write may have succeeded remotely; only skip reconciliation before the write.
WG_BINDING_UNATTEMPTED=0

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
    case "$name:$rc" in
      link-*:10|link-*:12|route-*:10|server-*:10|server-*:11)
        rm -- "$marker" || return 1;;
    esac
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

wg_lease_only_binding() {
  local path
  for path in "$WG_LEASE_DIR"/* "$WG_LEASE_DIR"/.[!.]* "$WG_LEASE_DIR"/..?*; do
    [ -e "$path" ] || [ -L "$path" ] || continue
    case ${path##*/} in lock|token|identity|count|options|binding) ;; *) return 1;; esac
  done
}

wg_binding_status() {
  local state
  state=$(dut 'sudo cat /sys/module/wireguard/parameters/wgfp_peer_lease') ||
    { echo "ERROR: cannot inspect the atomic WGFP reservation" >&2; return 1; }
  case $state in
    unowned) WG_BINDING_OWNER=; WG_BINDING_DEV=off;;
    owned=*" dev="*)
      WG_BINDING_OWNER=${state#owned=}
      WG_BINDING_OWNER=${WG_BINDING_OWNER%% *}
      WG_BINDING_DEV=${state#* dev=}
      [[ $WG_BINDING_OWNER =~ ^[0-9a-f]{32}$ && -n $WG_BINDING_DEV ]] ||
        { echo "ERROR: invalid WGFP reservation state" >&2; return 1; };;
    *) echo "ERROR: unrecognized WGFP reservation state" >&2; return 1;;
  esac
}

wg_reserve_binding() {
  local marker="$WG_LEASE_DIR/binding"
  if [ "$DUT_KIND" != ask2 ]; then
    [ "$WG_LEASE_WGFP" != 1 ] || { echo "ERROR: WGFP requires an ASK2 DUT" >&2; return 1; }
    return 0
  fi
  WG_BINDING_UNATTEMPTED=1
  if [ -e "$marker" ] || [ -L "$marker" ]; then
    echo "ERROR: WGFP reservation is already journaled" >&2
    return 1
  fi
  wg_binding_status || return 1
  if [ -n "$WG_BINDING_OWNER" ]; then
    echo "ERROR: WGFP is already reserved; refusing setup" >&2
    return 1
  fi
  printf 'pending\n' > "$marker" || return 1
  WG_BINDING_UNATTEMPTED=0
  dut "printf '%s\\n' 'reserve $WG_OWNER_TOKEN' | sudo tee /sys/module/wireguard/parameters/wgfp_peer_lease >/dev/null" ||
    { echo "ERROR: WGFP reservation failed; reconciling its ownership" >&2; return 1; }
  wg_binding_status || return 1
  if [ "$WG_BINDING_OWNER" != "$WG_OWNER_TOKEN" ] || [ "$WG_BINDING_DEV" != off ]; then
    echo "ERROR: WGFP reservation did not remain ours and inactive" >&2
    return 1
  fi
  printf 'created\n' > "$marker"
}

wg_activate_binding() {
  local expected=$1 aliases=$2 marker="$WG_LEASE_DIR/binding" alias
  [ "$WG_LEASE_WGFP" = 1 ] || return 0
  if [ -L "$marker" ] || [ ! -f "$marker" ] || [ "$(cat "$marker")" != created ]; then
    echo "ERROR: WGFP reservation is not owned by this lease" >&2
    return 1
  fi
  for alias in $aliases; do
    [[ $alias =~ ^wgo-[0-9a-f]{32}-[a-z0-9-]+$ ]] ||
      { echo "ERROR: invalid WGFP link identity" >&2; return 1; }
  done
  wg_binding_status || return 1
  if [ "$WG_BINDING_OWNER" != "$WG_OWNER_TOKEN" ] || [ "$WG_BINDING_DEV" != off ]; then
    echo "ERROR: WGFP reservation changed before binding" >&2
    return 1
  fi
  wg_bound_links_owned || return 1
  dut "printf '%s\\n' 'bind $WG_OWNER_TOKEN $aliases' | sudo tee /sys/module/wireguard/parameters/wgfp_peer_lease >/dev/null" ||
    { echo "ERROR: WGFP bind failed" >&2; return 1; }
  wg_binding_status || return 1
  if [ "$WG_BINDING_OWNER" != "$WG_OWNER_TOKEN" ] || [ "$WG_BINDING_DEV" != "$expected" ]; then
    echo "ERROR: WGFP is not bound to the owned devices" >&2
    return 1
  fi
  wg_bound_links_owned
}

wg_prepare_binding_cleanup() {
  local marker="$WG_LEASE_DIR/binding" journal
  [ "$DUT_KIND" = ask2 ] || return 0
  if [ -e "$marker" ] || [ -L "$marker" ]; then
    if [ -L "$marker" ] || [ ! -f "$marker" ]; then
      echo "ERROR: WGFP reservation has uncertain ownership" >&2
      return 1
    fi
    journal=$(cat "$marker") || return 1
    case $journal in pending|created|releasing) ;; *)
      echo "ERROR: invalid WGFP reservation journal" >&2; return 1;; esac
    wg_binding_status || return 1
    if [ "$WG_BINDING_OWNER" = "$WG_OWNER_TOKEN" ]; then
      if [ "$WG_LEASE_WGFP" = 0 ]; then
        if [ "$WG_BINDING_DEV" != off ]; then
          echo "ERROR: software rig's WGFP reservation became active; refusing cleanup" >&2
          return 1
        fi
        [ "$journal" = releasing ] || printf 'created\n' > "$marker"
        return $?
      fi
      dut "printf '%s\\n' 'stop $WG_OWNER_TOKEN' | sudo tee /sys/module/wireguard/parameters/wgfp_peer_lease >/dev/null" || return 1
      wg_binding_status || return 1
      if [ "$WG_BINDING_OWNER" != "$WG_OWNER_TOKEN" ] || [ "$WG_BINDING_DEV" != off ]; then
        echo "ERROR: WGFP reservation changed while stopping" >&2
        return 1
      fi
      [ "$journal" = releasing ] || printf 'created\n' > "$marker"
      return $?
    fi
    if { [ "$journal" = pending ] || [ "$journal" = releasing ]; } && wg_lease_only_binding; then
      return 0
    fi
    echo "ERROR: WGFP reservation is not owned by this lease; refusing cleanup" >&2
    return 1
  fi
  if [ "${WG_BINDING_UNATTEMPTED:-0}" = 1 ] && wg_lease_only_binding; then
    return 0
  fi
  wg_binding_status || return 1
  if [ "$WG_BINDING_OWNER" = "$WG_OWNER_TOKEN" ] || ! wg_lease_only_binding; then
    echo "ERROR: WGFP reservation journal is missing; refusing cleanup" >&2
    return 1
  fi
}

wg_release_binding() {
  local marker="$WG_LEASE_DIR/binding" journal
  [ -e "$marker" ] || [ -L "$marker" ] || return 0
  if [ -L "$marker" ] || [ ! -f "$marker" ]; then
    echo "ERROR: WGFP reservation has uncertain ownership" >&2
    return 1
  fi
  journal=$(cat "$marker") || return 1
  wg_lease_only_binding ||
    { echo "ERROR: retain WGFP reservation until owned resources are removed" >&2; return 1; }
  wg_binding_status || return 1
  if [ "$WG_BINDING_OWNER" = "$WG_OWNER_TOKEN" ]; then
    if [ "$WG_BINDING_DEV" != off ]; then
      echo "ERROR: refusing to release an active WGFP reservation" >&2
      return 1
    fi
    printf 'releasing\n' > "$marker" || return 1
    dut "printf '%s\\n' 'release $WG_OWNER_TOKEN' | sudo tee /sys/module/wireguard/parameters/wgfp_peer_lease >/dev/null" || return 1
    wg_binding_status || return 1
    if [ "$WG_BINDING_OWNER" = "$WG_OWNER_TOKEN" ]; then
      echo "ERROR: WGFP reservation was not released" >&2
      return 1
    fi
  elif [ "$journal" != pending ] && [ "$journal" != releasing ]; then
    echo "ERROR: WGFP reservation disappeared before release" >&2
    return 1
  fi
  rm -- "$marker"
}

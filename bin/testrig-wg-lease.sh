# shellcheck shell=bash

wg_lease_identity() {
  printf '%s\0' "$WG_LEASE_KIND" "$DUT_KIND" "$DUT" "${GEN1:-}" "${GEN2:-}" \
    "${RIG_NS:-}" "${WG_PORT:-eth4}" | sha256sum | cut -d' ' -f1
}

wg_lease_lock() {
  local opened current
  if [ -n "${WG_LEASE_LOCK_FD:-}" ]; then
    [ "${WG_LEASE_LOCK_PATH:-}" = "$WG_LEASE_DIR/lock" ] || return 1
    return 0
  fi
  if [ ! -f "$WG_LEASE_DIR/lock" ] || [ -L "$WG_LEASE_DIR/lock" ]; then
    echo "ERROR: no valid lease lock at $WG_LEASE_DIR" >&2
    return 1
  fi
  exec {WG_LEASE_LOCK_FD}< "$WG_LEASE_DIR/lock" || return 1
  if ! flock -xn "$WG_LEASE_LOCK_FD"; then
    echo "ERROR: lease $WG_LEASE_DIR is in use" >&2
    exec {WG_LEASE_LOCK_FD}<&-
    unset WG_LEASE_LOCK_FD
    return 1
  fi
  opened=$(stat -Lc '%d:%i' "/proc/$$/fd/$WG_LEASE_LOCK_FD") || return 1
  current=$(stat -Lc '%d:%i' "$WG_LEASE_DIR/lock") || return 1
  if [ "$opened" != "$current" ]; then
    echo "ERROR: lease lock changed while acquiring it" >&2
    return 1
  fi
  WG_LEASE_LOCK_PATH="$WG_LEASE_DIR/lock"
}

wg_lease_begin() {
  local identity
  if ! [[ ${WG_LEASE_COUNT:-} =~ ^([1-9]|1[0-6])$ ]]; then
    echo "ERROR: invalid lease resource count" >&2
    return 1
  fi
  WG_LEASE_SKIP_SERVERS=${WG_SKIP_SERVERS:-0}
  WG_LEASE_SKIP_FW4=${WG_SKIP_FW4:-0}
  WG_LEASE_MTU=${WGMTU:-1420}
  WG_LEASE_IPERF_BASE=${IPERF_BASE:-${IPERF_P:-5231}}
  WG_LEASE_WGFP=${WGFP:-0}
  if ! [[ $WG_LEASE_SKIP_SERVERS =~ ^[01]$ && $WG_LEASE_SKIP_FW4 =~ ^[01]$ && $WG_LEASE_WGFP =~ ^[01]$ ]] ||
     ! [[ $WG_LEASE_MTU =~ ^[1-9][0-9]{0,4}$ ]] || (( WG_LEASE_MTU > 65535 )) ||
     ! [[ $WG_LEASE_IPERF_BASE =~ ^[1-9][0-9]{0,4}$ ]] ||
     (( WG_LEASE_IPERF_BASE + WG_LEASE_COUNT - 1 > 65535 )); then
    echo "ERROR: invalid lease options" >&2
    return 1
  fi
  WG_OWNER_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(16))') || return 1
  identity=$(wg_lease_identity) || return 1
  umask 077
  if ! mkdir -m 700 -- "$WG_LEASE_DIR"; then
    echo "ERROR: cannot create lease $WG_LEASE_DIR; tear down the existing run first" >&2
    return 1
  fi
  if ! { : > "$WG_LEASE_DIR/lock" &&
         wg_lease_lock &&
         printf '%s\n' "$WG_OWNER_TOKEN" > "$WG_LEASE_DIR/token" &&
         printf '%s\n' "$identity" > "$WG_LEASE_DIR/identity" &&
         printf '%s\n' "$WG_LEASE_COUNT" > "$WG_LEASE_DIR/count" &&
         printf '%s\n%s\n%s\n%s\n%s\n' "$WG_LEASE_SKIP_SERVERS" "$WG_LEASE_SKIP_FW4" "$WG_LEASE_MTU" "$WG_LEASE_IPERF_BASE" "$WG_LEASE_WGFP" > "$WG_LEASE_DIR/options"; }; then
    rm -f -- "$WG_LEASE_DIR/token" "$WG_LEASE_DIR/identity" "$WG_LEASE_DIR/count" "$WG_LEASE_DIR/options" "$WG_LEASE_DIR/lock"
    rmdir -- "$WG_LEASE_DIR" || echo "ERROR: incomplete lease requires inspection: $WG_LEASE_DIR" >&2
    if [ -n "${WG_LEASE_LOCK_FD:-}" ]; then exec {WG_LEASE_LOCK_FD}<&-; fi
    unset WG_LEASE_LOCK_FD WG_LEASE_LOCK_PATH
    return 1
  fi
}

wg_lease_load() {
  local expected identity
  if [ ! -d "$WG_LEASE_DIR" ] || [ -L "$WG_LEASE_DIR" ] ||
     [ ! -O "$WG_LEASE_DIR" ] || [ "$(stat -c %a "$WG_LEASE_DIR")" != 700 ]; then
    echo "ERROR: no valid owned lease at $WG_LEASE_DIR" >&2
    return 1
  fi
  wg_lease_lock || return 1
  if [ ! -d "$WG_LEASE_DIR" ] || [ -L "$WG_LEASE_DIR" ] ||
     [ ! -f "$WG_LEASE_DIR/token" ] || [ -L "$WG_LEASE_DIR/token" ] ||
     [ ! -f "$WG_LEASE_DIR/identity" ] || [ -L "$WG_LEASE_DIR/identity" ] ||
     [ ! -f "$WG_LEASE_DIR/count" ] || [ -L "$WG_LEASE_DIR/count" ] ||
     [ ! -f "$WG_LEASE_DIR/options" ] || [ -L "$WG_LEASE_DIR/options" ]; then
    echo "ERROR: no valid owned lease at $WG_LEASE_DIR" >&2
    return 1
  fi
  WG_OWNER_TOKEN=$(cat "$WG_LEASE_DIR/token") || return 1
  WG_LEASE_COUNT=$(cat "$WG_LEASE_DIR/count") || return 1
  if ! [[ $WG_LEASE_COUNT =~ ^([1-9]|1[0-6])$ ]]; then
    echo "ERROR: invalid lease resource count" >&2
    return 1
  fi
  local -a options
  mapfile -t options < "$WG_LEASE_DIR/options"
  if [ "${#options[@]}" -ne 5 ] || ! [[ ${options[0]} =~ ^[01]$ && ${options[1]} =~ ^[01]$ && ${options[4]} =~ ^[01]$ ]] ||
     ! [[ ${options[2]} =~ ^[1-9][0-9]{0,4}$ && ${options[3]} =~ ^[1-9][0-9]{0,4}$ ]] ||
     (( options[2] > 65535 || options[3] + WG_LEASE_COUNT - 1 > 65535 )); then
    echo "ERROR: invalid lease options" >&2
    return 1
  fi
  WG_LEASE_SKIP_SERVERS=${options[0]}
  WG_LEASE_SKIP_FW4=${options[1]}
  WG_LEASE_MTU=${options[2]}
  WG_LEASE_IPERF_BASE=${options[3]}
  WG_LEASE_WGFP=${options[4]}
  identity=$(cat "$WG_LEASE_DIR/identity") || return 1
  expected=$(wg_lease_identity) || return 1
  if ! [[ $WG_OWNER_TOKEN =~ ^[0-9a-f]{32}$ ]] ||
     ! [[ $WG_LEASE_COUNT =~ ^([1-9]|1[0-6])$ ]] || [ "$identity" != "$expected" ]; then
    echo "ERROR: lease identity does not match the selected rig" >&2
    return 1
  fi
}

wg_lease_end() {
  local path
  wg_lease_load || return 1
  for path in "$WG_LEASE_DIR"/* "$WG_LEASE_DIR"/.[!.]* "$WG_LEASE_DIR"/..?*; do
    [ -e "$path" ] || [ -L "$path" ] || continue
    case ${path##*/} in lock|token|identity|count|options) ;; *)
      echo "ERROR: lease still contains an owned or uncertain resource: $path" >&2
      return 1;;
    esac
  done
  rm -- "$WG_LEASE_DIR/token" "$WG_LEASE_DIR/identity" "$WG_LEASE_DIR/count" "$WG_LEASE_DIR/options" "$WG_LEASE_DIR/lock" || return 1
  rmdir -- "$WG_LEASE_DIR" || return 1
  exec {WG_LEASE_LOCK_FD}<&-
  unset WG_LEASE_LOCK_FD WG_LEASE_LOCK_PATH
}

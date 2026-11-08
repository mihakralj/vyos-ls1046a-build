#!/bin/sh
# Manage one isolated test route and its exact policy rule.
set -eu

if [ "$#" -ne 9 ]; then
  echo "usage: $0 add|delete far|plain <priority> <table> <source> <destination> <device-or-gateway> <mtu> <realm>" >&2
  exit 2
fi
operation=$1
kind=$2
priority=$3
table=$4
source=$5
destination=$6
target=$7
mtu=$8
realm=$9

for value in "$priority" "$table" "$mtu" "$realm"; do
  case $value in ''|*[!0-9]*) echo "ERROR: invalid route identifier" >&2; exit 2;; esac
done
case $kind in far|plain) ;; *) echo "ERROR: invalid route kind" >&2; exit 2;; esac
case $source in *:*) family=-6;; *) family=-4;; esac
proto=$((128 + realm % 120))

table_routes() {
  if result=$(ip "$family" route show table "$table" "$@" 2>&1); then
    printf '%s\n' "$result"
  else
    case $result in
      *"FIB table does not exist"*) printf '\n';;
      *) echo "ERROR: cannot inspect route table $table: $result" >&2; return 1;;
    esac
  fi
}

route_change() {
  operation=$1
  if [ "$kind" = far ]; then
    set -- table "$table" "$destination" dev "$target" proto "$proto" metric "$realm"
  else
    set -- table "$table" "$destination" via "$target" mtu "$mtu" proto "$proto" metric "$realm"
  fi
  if [ "$family" = -4 ]; then set -- "$@" realm "$realm"; fi
  ip "$family" route "$operation" "$@"
}

rule_change() {
  operation=$1
  set -- priority "$priority" from "$source" to "$destination" lookup "$table" protocol "$proto"
  if [ "$family" = -4 ]; then set -- "$@" realms "$realm"; fi
  ip "$family" rule "$operation" "$@"
}

case $operation in
  add)
    rules=$(ip "$family" rule show pref "$priority") || exit 10
    [ -z "$rules" ] || { echo "ERROR: rule priority $priority is in use" >&2; exit 10; }
    routes=$(table_routes) || exit 10
    [ -z "$routes" ] || { echo "ERROR: route table $table is in use" >&2; exit 10; }
    if ! route_change add; then
      echo "ERROR: route creation outcome is uncertain; retain the lease" >&2
      exit 1
    fi
    if ! rule_change add; then
      echo "ERROR: policy rule creation outcome is uncertain; retain the route and lease" >&2
      exit 1
    fi
    ;;
  delete)
    all_rules=$(ip -N "$family" rule show pref "$priority") || exit 1
    rules=$(ip -N "$family" rule show pref "$priority" from "$source" to "$destination" \
      lookup "$table" protocol "$proto") || exit 1
    if [ -n "$all_rules" ] &&
       { [ -z "$rules" ] || [ "$(printf '%s\n' "$all_rules" | wc -l)" -ne 1 ]; }; then
      echo "ERROR: policy rule priority $priority was replaced or shared" >&2
      exit 1
    fi
    if [ -n "$rules" ]; then
      case " $rules " in *" proto $proto "*) ;; *)
        echo "ERROR: policy rule protocol changed" >&2
        exit 1;;
      esac
      if [ "$family" = -4 ]; then
        if [ "$realm" -lt 65536 ]; then
          rule_realm=$realm
        else
          rule_realm="$((realm >> 16))/$((realm & 65535))"
        fi
        case " $rules " in *" realms $rule_realm "*) ;; *)
          echo "ERROR: policy rule realm changed" >&2
          exit 1;;
        esac
      fi
    fi
    all_routes=$(table_routes exact "$destination") || exit 1
    routes=
    if [ -n "$all_routes" ]; then
      if [ "$kind" = far ]; then
        set -- exact "$destination" proto "$proto" metric "$realm" dev "$target"
      else
        set -- exact "$destination" proto "$proto" metric "$realm" via "$target"
      fi
      if [ "$family" = -4 ]; then set -- "$@" realm "$realm"; fi
      routes=$(table_routes "$@") || exit 1
    fi
    if [ -n "$all_routes" ] &&
       { [ -z "$routes" ] || [ "$(printf '%s\n' "$all_routes" | wc -l)" -ne 1 ]; }; then
      echo "ERROR: route in table $table was replaced or shared" >&2
      exit 1
    fi
    if [ -n "$routes" ]; then route_change del; fi
    if [ -n "$rules" ]; then rule_change del; fi
    ;;
  *)
    echo "ERROR: expected add or delete" >&2
    exit 2
    ;;
esac

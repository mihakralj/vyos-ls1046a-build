# shellcheck shell=bash
# testrig-env.sh — rig settings shared by the bin/testrig-* and bin/wg-parity* drivers (sourced, not run).
#
# Settings come from the environment first, then from the file ${TESTRIG_ENV:-~/.config/testrig.env}
# (plain KEY=value lines; copy bin/testrig.env.example). Nothing lab-specific is built in: the
# management targets have no defaults, and a driver that needs one stops with a clear message.
#
#   GEN1, GEN2           SSH targets (user@host) of generator 1 (DUT eth3 side) and 2 (DUT eth4 side)
#   ASK2_DUT             SSH target of the VyOS/ASK2 DUT                       (DUT_KIND=ask2)
#   VENDOR_DUT           SSH target of the OpenWrt or OPNsense NXP ASK DUT    (DUT_KIND=vendor|opnsense)
#   RIG_KEY              SSH private key for all rig hosts (empty: ssh defaults / agent)
#   RIG_OUT              result directory (default ~/testrig/out)
#   GEN_IF, GEN_IF_NS    generator ports facing the first DUT (root namespace) and the second DUT
#   VENDOR_NS            network namespace that holds GEN_IF_NS on both generators (default n106)
#   PPPOE_USER           PPPoE test account in gen1's /etc/ppp/chap-secrets
#   PPPOE_PLUGIN         rp-pppoe plugin path for pppoe-server on gen1
#   RUN_ON=gen2          the driver runs on generator 2 (its commands run locally); `dell2` is an alias

_testrig_load(){
  local line k v
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    case $line in ''|'#'*) continue ;; esac
    k=${line%%=*}; v=${line#*=}
    [[ $k =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "testrig-env: ignoring line: $line" >&2; continue; }
    [ -n "$(printenv "$k")" ] && continue   # the environment wins
    v=${v#\"}; v=${v%\"}; v=${v#\'}; v=${v%\'}
    case $v in '~/'*) v=$HOME/${v#'~/'} ;; esac
    v=${v//'$HOME'/$HOME}
    export "$k=$v"
  done < "$1"
}
TESTRIG_ENV_FILE=${TESTRIG_ENV:-$HOME/.config/testrig.env}
[ -r "$TESTRIG_ENV_FILE" ] && _testrig_load "$TESTRIG_ENV_FILE"

export RIG_KEY=${RIG_KEY:-} RIG_OUT=${RIG_OUT:-$HOME/testrig/out}
export GEN_IF=${GEN_IF:-enp1s0f0np0} GEN_IF_NS=${GEN_IF_NS:-enp1s0f1np1} VENDOR_NS=${VENDOR_NS:-n106}
export PPPOE_USER=${PPPOE_USER:-pppoe-test} PPPOE_PLUGIN=${PPPOE_PLUGIN:-/usr/lib/pppd/2.5.2/rp-pppoe.so}
case ${RUN_ON:-} in gen2) RUN_ON=dell2 ;; esac
RIG_SSHK=""; [ -n "$RIG_KEY" ] && RIG_SSHK="-i $RIG_KEY"

# rig_require VAR ...: stop unless every named setting is non-empty
rig_require(){
  local v miss=""
  for v in "$@"; do [ -n "$(printenv "$v")" ] || miss="$miss $v"; done
  [ -z "$miss" ] && return 0
  echo "ERROR: set$miss in the environment or in $TESTRIG_ENV_FILE (template: bin/testrig.env.example)" >&2
  exit 2
}
# rig_dut KIND: the DUT SSH target for a DUT_KIND
rig_dut(){
  case $1 in
    ask2) rig_require ASK2_DUT; echo "$ASK2_DUT" ;;
    *) rig_require VENDOR_DUT; echo "$VENDOR_DUT" ;;
  esac
}

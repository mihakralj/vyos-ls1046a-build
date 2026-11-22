# shellcheck shell=bash
# Verify requested shared WGFP modes without changing them.

wgfp_validate_modes() {
  case ${WGFP:-0} in 0|1) ;; *) echo "WGFP must be 0 or 1" >&2; return 2 ;; esac
  WGFP_RX=${WGFP_RX:-${WGFP:-0}}
  case $WGFP_RX in 0|1) ;; *) echo "WGFP_RX must be 0 or 1" >&2; return 2 ;; esac
  if [ "$WGFP_RX" = 1 ] && [ "${WGFP:-0}" != 1 ]; then
    echo "WGFP_RX=1 needs WGFP=1" >&2; return 2
  fi
  WGFP_RX_FAST=${WGFP_RX_FAST:-}
  case $WGFP_RX_FAST in ''|0|1|2) ;; *) echo "WGFP_RX_FAST must be 0, 1 or 2" >&2; return 2 ;; esac
  if [ -n "$WGFP_RX_FAST" ] && [ "$WGFP_RX" != 1 ]; then
    echo "WGFP_RX_FAST needs WGFP_RX=1" >&2; return 2
  fi
  WGFP_RX_TAIL=${WGFP_RX_TAIL:-}
  case $WGFP_RX_TAIL in ''|0|1) ;; *) echo "WGFP_RX_TAIL must be 0 or 1" >&2; return 2 ;; esac
  if [ -n "$WGFP_RX_TAIL" ] && [ "${WGFP:-0}" != 1 ]; then
    echo "WGFP_RX_TAIL needs WGFP=1" >&2; return 2
  fi
}

wgfp_check_value() {
  local name=$1 expected=$2 actual
  actual=$(wgfp_mode_read "$name") ||
    { echo "ERROR: cannot read $name" >&2; return 1; }
  case $name in
    wgfp_rx|wgfp_rx_tail)
      case $actual in
        Y|y|1) actual=1 ;;
        N|n|0) actual=0 ;;
        *) echo "ERROR: invalid $name value: $actual" >&2; return 1 ;;
      esac ;;
  esac
  if [ "$actual" != "$expected" ]; then
    echo "ERROR: $name=$actual, requested $expected; matrix will not change shared WGFP settings" >&2
    return 1
  fi
}

wgfp_check_modes() {
  [ "$DUT_KIND" = ask2 ] && [ "${WGFP:-0}" = 1 ] || return 0
  wgfp_check_value wgfp_rx "$WGFP_RX" || return 1
  if [ -n "$WGFP_RX_FAST" ]; then
    wgfp_check_value wgfp_rx_fast "$WGFP_RX_FAST" || return 1
  fi
  if [ -n "$WGFP_RX_TAIL" ]; then
    wgfp_check_value wgfp_rx_tail "$WGFP_RX_TAIL" || return 1
  fi
}

# shellcheck shell=bash
# Source before platform-specific defaults; use RIG_PARITY=1 in matrix adapters.
parity_error(){ echo "ERROR: parity profile: $*" >&2; return 2; }

export DUR=${DUR:-32} STEADY_FROM=${STEADY_FROM:-10}
export STREAMS=${STREAMS:-8} REPS=${REPS:-3} PARITY_CC=${PARITY_CC:-bbr}
for parity_value in "$DUR" "$STEADY_FROM" "$STREAMS" "$REPS"; do
  [[ $parity_value =~ ^(0|[1-9][0-9]{0,5})$ ]] ||
    { parity_error "invalid count or duration"; return 2; }
done
export MEASURE_TO=${MEASURE_TO:-$((DUR-2))}
[[ $MEASURE_TO =~ ^(0|[1-9][0-9]{0,5})$ ]] &&
  (( REPS > 0 && STREAMS > 0 && STEADY_FROM >= 0 &&
     STEADY_FROM < MEASURE_TO && MEASURE_TO < DUR )) ||
  { parity_error "require positive repetitions/streams and 0 <= STEADY_FROM < MEASURE_TO < DUR"; return 2; }
case $PARITY_CC in bbr|cubic) ;; *) parity_error "PARITY_CC must be bbr or cubic"; return 2 ;; esac
[[ ${SAMPLE:-0} = 0 && ${DUT_TRACE:-0} = 0 ]] ||
  { parity_error "SAMPLE and DUT_TRACE must be 0 on both boards"; return 2; }
[[ ${RUN_ON:-agent} = agent ]] ||
  { parity_error "RUN_ON must be agent for identical SSH orchestration"; return 2; }
[[ ${BIDIR_DUR:-$DUR} = "$DUR" &&
   ${BIDIR_STEADY_FROM:-$STEADY_FROM} = "$STEADY_FROM" &&
   ${WG_REPS:-$REPS} = "$REPS" ]] ||
  { parity_error "unidirectional, bidirectional and WireGuard timing/repetitions must match"; return 2; }
[[ ${IPERF_ARGS:--C $PARITY_CC} = "-C $PARITY_CC" ]] ||
  { parity_error "IPERF_ARGS must select only the common congestion control"; return 2; }
export BIDIR_DUR=$DUR BIDIR_STEADY_FROM=$STEADY_FROM WG_REPS=$REPS
export SAMPLE=0 DUT_TRACE=0 RUN_ON=agent IPERF_ARGS="-C $PARITY_CC"
unset parity_value

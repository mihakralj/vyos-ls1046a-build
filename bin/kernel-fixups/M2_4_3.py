# M2_4_3: DISABLED — Keep FM_CTL params page allocated across disengage/re-engage cycles.
# Freeing params page on disarm caused gen_pool_free_owner BUG on re-disengage due to
# double-free/stale offset tracking. Params page (256 B) stays safely cached per port.
import sys
print("### M2_4_3: disabled (params page kept warm across cycles)")
sys.exit(0)

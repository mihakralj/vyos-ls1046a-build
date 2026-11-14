#!/usr/bin/env python3
"""ask2_churn_run.py <mice_rate> <secs> [eleph_pps] : run stl/ask2_churn.py on ports 0+1 and report elephant loss.

Elephants start at t=0; mice start at t=8 s and run <secs>. Elephant pg_id stats are snapshot at t=8 s and at the end,
so the reported elephant loss covers only the mice window. Prints one 'eleph' and one 'total' line.
"""
import sys, time
sys.path.insert(0, '/opt/trex/automation/trex_control_plane/interactive')
from trex.stl.api import STLClient, STLProfile

mice, secs = float(sys.argv[1]), int(sys.argv[2])
eleph_pps = float(sys.argv[3]) if len(sys.argv) > 3 else 200000
c = STLClient(verbose_level='error')
c.connect()
try:
    c.reset(ports=[0, 1])
    c.set_service_mode(ports=[0, 1], enabled=True)
    c.resolve(ports=[0, 1])
    c.set_service_mode(ports=[0, 1], enabled=False)
    for p in (0, 1):
        c.add_streams(STLProfile.load_py('/opt/trex/stl/ask2_churn.py', port_id=p, mice_rate=mice,
                                         eleph_pps=eleph_pps).get_streams(), ports=[p])
    c.clear_stats()
    c.start(ports=[0, 1], mult="1")
    time.sleep(8)

    def eleph():
        fs = c.get_pgid_stats([1, 2])['flow_stats']
        return [(fs[g]['tx_pkts']['total'], fs[g]['rx_pkts']['total']) for g in (1, 2)]

    e0 = eleph(); s0 = c.get_stats()
    time.sleep(secs)
    e1 = eleph(); s1 = c.get_stats()
    c.stop(ports=[0, 1])
    etx = sum(e1[i][0] - e0[i][0] for i in range(2)); erx = sum(e1[i][1] - e0[i][1] for i in range(2))
    tx = sum(s1[p]['opackets'] - s0[p]['opackets'] for p in (0, 1)); rx = sum(s1[p]['ipackets'] - s0[p]['ipackets'] for p in (0, 1))
    print(f"eleph mice_rate={mice:.0f} secs={secs} tx={etx} rx={erx} loss_pct={100.0 * (etx - erx) / max(1, etx):.5f}")
    print(f"total mice_rate={mice:.0f} secs={secs} tx={tx} rx={rx} loss_pct={100.0 * (tx - rx) / max(1, tx):.5f}")
finally:
    c.disconnect()

#!/usr/bin/env python3
"""ask2_fixed.py <size> <flows> <rate> <seconds> — fixed-rate bidirectional run of stl/ask2_udp.py on ports 0+1
after a 3 s 1 kpps priming run in both directions (rate per port, TRex syntax: '10%', '1mpps', '5gbps'). Prints per-port tx/rx packets and the drop rate."""
import sys, time
sys.path.insert(0, '/opt/trex/automation/trex_control_plane/interactive')
from trex.stl.api import STLClient, STLProfile

size, flows, rate, secs = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
c = STLClient(verbose_level='error')
c.connect()
try:
    c.reset(ports=[0, 1])
    c.set_service_mode(ports=[0, 1], enabled=True)
    c.resolve(ports=[0, 1])  # ARP for the DUT gateways (TRex resolves only at startup)
    c.set_service_mode(ports=[0, 1], enabled=False)
    for p in (0, 1):
        c.add_streams(STLProfile.load_py('/opt/trex/stl/ask2_udp.py', port_id=p, size=size, flows=flows).get_streams(), ports=[p])
    # prime: every tuple seen in both directions at a low rate, then give ASK2 time to offload it
    # (offload_delay_ms 500 + ~1 s flowtable refresh); without this the first second runs in software and drops
    c.start(ports=[0, 1], mult="1kpps", duration=3)
    c.wait_on_traffic(ports=[0, 1], timeout=20)
    time.sleep(3)
    c.clear_stats()
    # stop explicitly: at 100 % with a flow VM the DP cores run behind and the duration timer fires late
    # (wait_on_traffic timed out at 60 s for a 30 s trial); tx counts what was actually sent
    c.start(ports=[0, 1], mult=rate)
    time.sleep(secs)
    c.stop(ports=[0, 1])
    time.sleep(1)
    s = c.get_stats()
    tx = [s[p]['opackets'] for p in (0, 1)]; rx = [s[p]['ipackets'] for p in (0, 1)]
    for p in (0, 1):
        print(f"port{p} tx={tx[p]} rx={rx[p]} tx_pps={tx[p]/secs:.0f}")
    lost = (tx[0] - rx[1]) + (tx[1] - rx[0])
    print(f"size={size} flows={flows} rate={rate} total_tx={sum(tx)} total_rx={sum(rx)} lost={lost} drop={100.0*lost/max(1,sum(tx)):.5f}%")
finally:
    c.disconnect()

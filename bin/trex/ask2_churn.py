from trex_stl_lib.api import *
import argparse


class ASK2Churn(object):
    """Mice churn plus elephants through the DUT (plans/ASK2-FLOW-SCALING-PLAN.md §3.0 / S1 churn).

    Port 0 sends 10.99.1.112 -> 10.99.2.113, port 1 the reverse, routed by the DUT.
    - Elephants: --eleph flows (sport 40000.., dport 5000), --eleph_size B frames, --eleph_pps per direction in total,
      from t=0. Per-stream stats pg_id 1 (port 0) and 2 (port 1) so elephant loss is counted apart from the mice.
    - Mice: one --mice_size B query per new flow at --mice_rate flows/s from t = --mice_start s, each answered once by
      port 1 --reply_us later (a DNS-like exchange: conntrack sees the reply, the flow is offload-eligible). Tuples are
      a nested sport 1025..65535 x dport 20000..20063 grid (4.1 M tuples), so no tuple repeats within a run.
    """

    def get_streams(self, port_id, direction, tunables, **kwargs):
        p = argparse.ArgumentParser()
        p.add_argument('--eleph', type=int, default=4)
        p.add_argument('--eleph_pps', type=float, default=200000)
        p.add_argument('--eleph_size', type=int, default=1518)
        p.add_argument('--mice_rate', type=float, default=0)
        p.add_argument('--mice_size', type=int, default=96)
        p.add_argument('--mice_start', type=float, default=8.0)
        p.add_argument('--reply_us', type=float, default=200.0)
        a = p.parse_args(tunables)
        fwd = port_id % 2 == 0
        src, dst = ('10.99.1.112', '10.99.2.113') if fwd else ('10.99.2.113', '10.99.1.112')

        def pkt(sport, dport, size):
            base = Ether() / IP(src=src, dst=dst, ttl=64) / UDP(sport=sport, dport=dport, chksum=0)
            return base / (max(0, size - len(base) - 4) * 'x')

        streams = []
        e_sp, e_dp = (40000, 5000) if fwd else (5000, 40000)
        evm = STLScVmRaw([STLVmFlowVar(name='e', min_value=40000, max_value=40000 + a.eleph - 1, size=2, op='inc'),
                          STLVmWrFlowVar(fv_name='e', pkt_offset='UDP.sport' if fwd else 'UDP.dport')])
        streams.append(STLStream(name='eleph', packet=STLPktBuilder(pkt=pkt(e_sp, e_dp, a.eleph_size), vm=evm),
                                 mode=STLTXCont(pps=a.eleph_pps), flow_stats=STLFlowStats(pg_id=1 if fwd else 2)))
        if a.mice_rate > 0:
            lo, hi = ('UDP.sport', 'UDP.dport') if fwd else ('UDP.dport', 'UDP.sport')
            mvm = STLScVmRaw([STLVmFlowVar(name='hi', min_value=20000, max_value=20063, size=2, op='inc'),
                              STLVmFlowVar(name='lo', min_value=1025, max_value=65535, size=2, op='inc', next_var='hi'),
                              STLVmWrFlowVar(fv_name='lo', pkt_offset=lo),
                              STLVmWrFlowVar(fv_name='hi', pkt_offset=hi)])
            isg = a.mice_start * 1e6 + (0 if fwd else a.reply_us)
            streams.append(STLStream(name='mice', isg=isg, packet=STLPktBuilder(pkt=pkt(1025, 20000, a.mice_size), vm=mvm),
                                     mode=STLTXCont(pps=a.mice_rate)))
        return streams


def register():
    return ASK2Churn()

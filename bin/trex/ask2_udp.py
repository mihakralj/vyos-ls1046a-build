from trex_stl_lib.api import *
import argparse


class ASK2UDP(object):
    """Routed IPv4 UDP through the DUT for plans/TREX-NDR-PERF-PLAN.md.

    Port 0 sends 10.99.1.112 -> 10.99.2.113, port 1 the exact reverse, so every flow is one 5-tuple seen in both
    directions (conntrack sees the replies; ASK2 can offload it). Flows differ by port: source port on port 0,
    destination port on port 1, 1025 .. 1025+flows-1. Above 64511 flows (the 16-bit port space from 1025) flows are
    a nested sport x dport grid, 256 values each from 1025 / 10000 (flows must then be a multiple of 256; the
    dport range must avoid ASK2's software-only control ports, e.g. SIP 5060/5061). --size is the frame size including FCS. UDP checksum 0
    (valid for IPv4) so varying the port needs no checksum fixup.
    tunables: --size <bytes> (default 64), --flows <n> (default 1)
    """

    def get_streams(self, port_id, direction, tunables, **kwargs):
        p = argparse.ArgumentParser()
        p.add_argument('--size', type=int, default=64)
        p.add_argument('--flows', type=int, default=1)
        a = p.parse_args(tunables)
        fwd = port_id % 2 == 0
        src, dst = ('10.99.1.112', '10.99.2.113') if fwd else ('10.99.2.113', '10.99.1.112')
        sport, dport = (1025, 5000) if fwd else (5000, 1025)
        base = Ether() / IP(src=src, dst=dst, ttl=64) / UDP(sport=sport, dport=dport, chksum=0)
        pad = max(0, a.size - len(base) - 4) * 'x'
        vm = []
        if a.flows > 64511:
            assert a.flows % 256 == 0, 'flows > 64511 must be a multiple of 256'
            lo, hi = ('UDP.sport', 'UDP.dport') if fwd else ('UDP.dport', 'UDP.sport')
            vm = STLScVmRaw([STLVmFlowVar(name='hi', min_value=10000, max_value=10000 + a.flows // 256 - 1, size=2, op='inc'),
                             STLVmFlowVar(name='lo', min_value=1025, max_value=1025 + 255, size=2, op='inc', next_var='hi'),
                             STLVmWrFlowVar(fv_name='lo', pkt_offset=lo),
                             STLVmWrFlowVar(fv_name='hi', pkt_offset=hi)])
        elif a.flows > 1:
            vm = STLScVmRaw([STLVmFlowVar(name='p', min_value=1025, max_value=1025 + a.flows - 1, size=2, op='inc'),
                             STLVmWrFlowVar(fv_name='p', pkt_offset='UDP.sport' if fwd else 'UDP.dport')])
        return [STLStream(packet=STLPktBuilder(pkt=base / pad, vm=vm), mode=STLTXCont())]


def register():
    return ASK2UDP()

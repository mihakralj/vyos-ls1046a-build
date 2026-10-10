#!/usr/bin/env python3
"""testrig-latprobe.py: hardware-timestamped two-way delay through the DUT (latency part of the comparison).

  reflect IFACE ADDR                 dell2: answer probes arriving on ADDR (UDP 319) until killed.
  probe IFACE SRC DST COUNT [RATE]   dell1: COUNT probes at RATE/s (default 1000) from SRC to the reflector
                                     at DST. The first 5000 (5 s at 1000/s) are warm-up and are not counted
                                     (ask.ko offloads a flow only once it is offload_delay_ms, 2 s, old).
                                     Prints one JSON line, in microseconds.
  selftest                           loopback with software timestamps, no rig, no root.

PTP-style, so no clock sync is needed. The probe sends a PTPv2 Delay_Req to UDP 319 (t1 = its TX hardware
timestamp); the reflector timestamps the arrival (t2), answers with another event message to the probe's
port 319 (t3), then sends t3 - t2 in a follow-up to UDP 320. rtt = (t4 - t1) - (t3 - t2) covers both DUT
traversals and the four DAC legs, without either host's stack or queueing in either NIC's TX ring (the
timestamps are taken at the MAC). half_rtt_us is the per-direction estimate. The X710 timestamps received
frames only if they are PTP event messages (ptpv2-l4-event), hence the framing. Hardware timestamping is
switched on for IFACE while running and off again at exit.
"""
import ctypes, fcntl, json, select, signal, socket, struct, sys, threading, time

SO_TIMESTAMPING = SCM_TIMESTAMPING = 37
SIOCSHWTSTAMP = 0x89b0
HW = 1 | 4 | 64            # SOF_TIMESTAMPING_TX_HARDWARE | RX_HARDWARE | RAW_HARDWARE -> ts[2]
SW = 2 | 8 | 16            # SOF_TIMESTAMPING_TX_SOFTWARE | RX_SOFTWARE | SOFTWARE     -> ts[0]
OPT_TSONLY = 1 << 11
EVENT, GENERAL, WARM = 319, 320, 5000


def hwtstamp(ifname, on):
    # struct hwtstamp_config {flags, tx_type (1 = ON), rx_filter (6 = PTP_V2_L4_EVENT)} via struct ifreq
    cfg = ctypes.create_string_buffer(struct.pack('iii', 0, int(on), 6 if on else 0))
    ifr = struct.pack('16sP', ifname.encode(), ctypes.addressof(cfg)).ljust(40, b'\0')
    with socket.socket() as s:
        fcntl.ioctl(s.fileno(), SIOCSHWTSTAMP, ifr)


def ptp(seq):   # PTPv2 Delay_Req: 34-byte header + 10-byte originTimestamp
    return struct.pack('!BBHBBHq4s10sHBb10s', 1, 2, 44, 0, 0, 0, 0, b'', b'ask2-probe', seq & 0xffff, 1, 0x7f, b'')


def seq_of(data):
    return struct.unpack('!H', data[30:32])[0] if len(data) >= 32 else None


def stamp(anc, idx):
    for lvl, typ, data in anc:
        if lvl == socket.SOL_SOCKET and typ == SCM_TIMESTAMPING:
            t = struct.unpack('6q', data[:48])
            return t[2 * idx] * 10**9 + t[2 * idx + 1] or None
    return None


def sock(addr, port, flags, ifname):
    s = socket.socket(socket.AF_INET6 if ':' in addr else socket.AF_INET, socket.SOCK_DGRAM)
    if flags:
        s.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPING, flags | OPT_TSONLY)
    if ifname:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, ifname.encode())
    s.bind((addr, port))
    s.setblocking(False)
    return s


def tx_stamp(s, idx, timeout=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            t = stamp(s.recvmsg(256, 256, socket.MSG_ERRQUEUE)[1], idx)
            if t:
                return t
        except BlockingIOError:
            time.sleep(0.00002)
    return None


def mode(ifname, sw):
    if sw:
        return SW, 0, None
    hwtstamp(ifname, True)
    return HW, 2, ifname


def reflect(ifname, addr, sw=False, stop=None):
    flags, idx, dev = mode(ifname, sw)
    ev = sock(addr, EVENT, flags, dev)
    gen = socket.socket(ev.family, socket.SOCK_DGRAM)
    try:
        while not (stop and stop.is_set()):
            if not select.select([ev], [], [], 0.5)[0]:
                continue
            data, anc, _, peer = ev.recvmsg(256, 256)
            q = seq_of(data)
            if q is None:
                continue
            t2 = stamp(anc, idx)
            ev.sendto(ptp(q), peer)
            t3 = tx_stamp(ev, idx)
            gen.sendto(struct.pack('!Hq', q, t3 - t2 if t2 and t3 else -1), (peer[0], GENERAL))
    finally:
        if not sw:
            hwtstamp(ifname, False)


def probe(ifname, src, dst, count, rate=1000, sw=False):
    flags, idx, dev = mode(ifname, sw)
    ev, gen = sock(src, EVENT, flags, dev), sock(src, GENERAL, 0, None)
    rtt, lost = [], 0
    try:
        for n in range(WARM + count):
            t0 = time.monotonic()
            ev.sendto(ptp(n), (dst, EVENT))
            t1, t4, res = tx_stamp(ev, idx), None, None
            while (t4 is None or res is None) and time.monotonic() < t0 + 0.05:
                for s in select.select([ev, gen], [], [], 0.01)[0]:
                    data, anc, _, _ = s.recvmsg(256, 256)
                    if s is ev and seq_of(data) == n & 0xffff:
                        t4 = stamp(anc, idx)
                    elif s is gen and len(data) == 10 and struct.unpack('!Hq', data)[0] == n & 0xffff:
                        res = struct.unpack('!Hq', data)[1]
            if n >= WARM:
                if None in (t1, t4, res) or res < 0:
                    lost += 1
                else:
                    rtt.append((t4 - t1 - res) / 1000)
            time.sleep(max(0, t0 + 1 / rate - time.monotonic()))
    finally:
        if not sw:
            hwtstamp(ifname, False)
    rtt.sort()
    p = lambda q: round(rtt[min(len(rtt) - 1, int(q * len(rtt)))], 2) if rtt else None
    out = dict(count=count, ok=len(rtt), lost=lost,
               rtt_us=dict(min=p(0), p50=p(.5), p90=p(.9), p99=p(.99), max=p(1)),
               half_rtt_us=dict(p50=p(.5) and round(p(.5) / 2, 2), p99=p(.99) and round(p(.99) / 2, 2)))
    print(json.dumps(out))
    return out


def selftest():
    global EVENT, GENERAL
    EVENT, GENERAL = 31900, 32000   # unprivileged ports
    stop = threading.Event()
    t = threading.Thread(target=reflect, args=('lo', '127.0.0.2', True, stop), daemon=True)
    t.start()
    time.sleep(0.3)
    out = probe('lo', '127.0.0.1', '127.0.0.2', 300, 2000, sw=True)
    stop.set()
    assert out['ok'] >= 290 and out['lost'] <= 10, out
    assert 0 < out['rtt_us']['p50'] < 1000, out
    print('selftest OK')


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))   # run the finally blocks: timestamping off
    a = sys.argv[1:]
    if a[:1] == ['reflect'] and len(a) == 3:
        reflect(a[1], a[2])
    elif a[:1] == ['probe'] and len(a) >= 5:
        probe(a[1], a[2], a[3], int(a[4]), float(a[5]) if len(a) > 5 else 1000)
    elif a[:1] == ['selftest']:
        selftest()
    else:
        sys.exit(__doc__)

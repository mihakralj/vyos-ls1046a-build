#!/usr/bin/env python3
"""testrig-offload-delay.py: how long after a flow starts does the DUT forward it in hardware?

Each trial starts a fresh paced flow dell1 -> dell2 (new source port, so a new conntrack entry and a
new offload record) while a sampler on the DUT reads the eth3 kernel-RX packet counter every 50 ms.
Frames forwarded in hardware never reach the kernel, so the counter's growth rate is the software
share of the flow:
  onset  = first sample where the rate exceeds the idle baseline by ONSET_PPS (flow started),
  offload = first sample where the rate stays below 10 % of the flow's software rate for HOLD_S s.
  delay  = offload - onset (50 ms resolution: the first and last sample interval each count).
  kernel_frames = frames the kernel received for the flow before hardware took over (idle rate removed).
  A flow that never shows a software-rate burst (vendor cmm offloads within the first frames) reports
  delay_s 0.0 with its kernel_frames count.

Kernel RX counter: ASK2 (.185) /sys/class/net/eth3/statistics/rx_packets; vendor (.106 OpenWrt)
`ethtool -S eth3` "rx packets [TOTAL]" (the netdev counter also counts fast-path frames there).

--method pcap (ask2 only) counts kernel-received frames with tcpdump on eth3 (-Q in, filtered on the trial's source
port) instead of the netdev counter. Use it whenever the delay may be short: ASK2 folds the silicon counters of
offloaded flows into rx_packets in ~3 s batches, and those jumps look like a software burst. Frames forwarded in
hardware never reach the tap, so delay = last kernel frame - first kernel frame and kernel_frames = frames seen.

Usage: bin/testrig-offload-delay.py [--kind ask2|vendor] [--proto tcp|udp] [--v6] [--trials N] [--rate 200M] [--method counter|pcap]
Defaults match the quick protocol rig: ask2 = vyos@192.168.1.185, vendor = root@192.168.1.106 + netns n106.
"""
import argparse, json, os, random, statistics, subprocess, sys, time

SAMPLER = r'''
import subprocess, sys, time
kind, dur = sys.argv[1], float(sys.argv[2])
def rd():
    if kind == "vendor":
        out = subprocess.run(["ethtool", "-S", "eth3"], stdout=subprocess.PIPE, universal_newlines=True).stdout
        for l in out.splitlines():
            if "rx packets [TOTAL]" in l:
                return int(l.split()[-1])
    return int(open("/sys/class/net/eth3/statistics/rx_packets").read())
end = time.time() + dur
while time.time() < end:
    print("%.3f %d" % (time.time(), rd()), flush=True)
    time.sleep(0.05)
'''

ONSET_PPS, HOLD_S, IDLE_SAMPLES = 3000, 1.0, 28
SSHO = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8']
# RUN_ON=dell2: stored and run on dell2; dell1 and the DUT are reached with dell2's own key
RIG_KEY = ['-i', os.path.expanduser('~/.ssh/vyos_key')] if os.environ.get('RUN_ON') == 'dell2' else []


def analyse(samples):
    # per-sample deltas: d[i] = packets the kernel received in the 50 ms before samples[i]
    d = [(samples[i][0], samples[i][1] - samples[i - 1][1], samples[i][0] - samples[i - 1][0]) for i in range(1, len(samples))]
    pps = [(t, n / dt) for t, n, dt in d]
    base = statistics.median(v for t, v in pps[:20])
    # frames the kernel received after the idle baseline, minus the idle rate: the software share of the flow
    frames = max(0, round(samples[-1][1] - samples[IDLE_SAMPLES][1] - base * (samples[-1][0] - samples[IDLE_SAMPLES][0])))
    on = next((t for t, v in pps if v > base + ONSET_PPS), None)
    if on is None:
        # no 50 ms sample carried a software-rate burst: hardware took over within the first frames of the flow
        return dict(base_pps=round(base), max_pps=round(max(v for t, v in pps)), kernel_frames=frames, delay_s=0.0,
                    error=None, note='offloaded within the first sample (<50 ms)')
    peak = statistics.median(v for t, v in pps if on + 0.3 <= t <= on + 0.6) or 1
    lo = base + 0.1 * (peak - base)
    end = pps[-1][0]
    off = next((t for t, v in pps if t > on + 0.3 and v < lo and t + HOLD_S <= end
                and all(w < lo for u, w in pps if t <= u <= t + HOLD_S)), None)
    if off is not None:
        # ASK2 folds the silicon counters of offloaded flows into eth3's rx_packets in 3 s batches, so
        # only the span from the first software burst to the hand-over is the kernel's own traffic
        frames = max(0, round(sum(n for t, n, dt in d if on - 0.1 <= t <= off + 0.05) - base * (off - on)))
    return dict(base_pps=round(base), sw_pps=round(peak), kernel_frames=frames,
                delay_s=None if off is None else round(off - on, 2),
                error=None if off is not None else 'never offloaded (kernel still receiving)')


def pcap_trial(a, dut, src, dst, n):
    cport = random.randrange(20000, 60000)
    flt = f'{a.proto} and src port {cport}'
    tap = subprocess.Popen(['ssh', '-T'] + SSHO + ['-i', os.path.expanduser('~/.ssh/vyos_key'), dut,
                            f'sudo timeout {a.dur + 6} tcpdump -nn -tt -l -Q in -i eth3 -s 64 "{flt}" 2>/dev/null'],
                           stdout=subprocess.PIPE, universal_newlines=True)
    time.sleep(2.5)
    udp = ' -u -l 1200' if a.proto == 'udp' else ''
    cmd = f'iperf3 -c {dst} -B {src} --cport {cport} -p 5201 -P 1 -t {a.dur} -b {a.rate}{udp} -J > /dev/null'
    subprocess.run(['ssh', '-n'] + SSHO + RIG_KEY + ['admin@192.168.1.112', cmd], check=False)
    ts = [float(l.split()[0]) for l in tap.communicate()[0].splitlines() if l[:1].isdigit()]
    if not ts:
        return dict(kernel_frames=0, delay_s=0.0, error=None, note='no frame reached the kernel tap')
    # a 1 s gap in the tap ends the kernel phase; later frames are stragglers (control segments, FIN)
    end = ts[0]
    for t in ts[1:]:
        if t - end > 1.0:
            break
        end = t
    return dict(kernel_frames=sum(1 for t in ts if t <= end), delay_s=round(end - ts[0], 3), tap_total=len(ts), error=None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kind', default='ask2', choices=['ask2', 'vendor'])
    ap.add_argument('--proto', default='tcp', choices=['tcp', 'udp'])
    ap.add_argument('--v6', action='store_true')
    ap.add_argument('--trials', type=int, default=5)
    ap.add_argument('--rate', default='200M')
    ap.add_argument('--dut')
    ap.add_argument('--dur', type=int, default=9, help='flow seconds')
    ap.add_argument('--method', default='counter', choices=['counter', 'pcap'])
    ap.add_argument('--out')
    ap.add_argument('--dump', help='write the raw samples of each trial to FILE (JSON lines)')
    a = ap.parse_args()
    dut = a.dut or ('root@192.168.1.106' if a.kind == 'vendor' else 'vyos@192.168.1.185')
    ns = 'sudo ip netns exec n106 ' if a.kind == 'vendor' else ''
    src, dst = ('fd99:1::112', 'fd99:2::113') if a.v6 else ('10.99.1.112', '10.99.2.113')
    res = []
    if a.method == 'pcap' and a.kind != 'ask2':
        sys.exit('--method pcap is ask2 only')
    for n in range(a.trials):
        if a.method == 'pcap':
            res.append(pcap_trial(a, dut, src, dst, n))
            print(f'trial {n + 1}: {res[-1]}', flush=True)
            time.sleep(3)
            continue
        # a fresh source port every trial: a repeated 4-tuple would reuse the previous flow's conntrack/offload state
        cport = random.randrange(20000, 60000)
        samp = subprocess.Popen(['ssh', '-T'] + SSHO + ['-i', os.path.expanduser('~/.ssh/vyos_key'), dut, 'python3 - %s %d' % (a.kind, a.dur + 5)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True)
        samp.stdin.write(SAMPLER); samp.stdin.flush(); samp.stdin.close(); samp.stdin = None
        head = [samp.stdout.readline() for _ in range(30)]   # 1.5 s of idle baseline before the flow
        udp = ' -u -l 1200' if a.proto == 'udp' else ''
        cmd = f'{ns}iperf3 -c {dst} -B {src} --cport {cport} -p 5201 -P 1 -t {a.dur} -b {a.rate}{udp} -J > /dev/null'
        subprocess.run(['ssh', '-n'] + SSHO + RIG_KEY + ['admin@192.168.1.112', cmd], check=False)
        out = ''.join(head) + samp.communicate()[0]
        samples = [(float(x), int(y)) for x, y in (l.split() for l in out.splitlines() if len(l.split()) == 2)]
        r = analyse(samples) if len(samples) > 40 else None
        if a.dump:
            with open(a.dump, 'a') as f:
                f.write(json.dumps(samples) + '\n')
        res.append(r)
        print(f'trial {n + 1}: {r}', flush=True)
        time.sleep(3)
    d = [r['delay_s'] for r in res if r and r.get('delay_s') is not None]
    kf = [r['kernel_frames'] for r in res if r and r.get('kernel_frames') is not None]
    summary = dict(kind=a.kind, proto=a.proto, v6=a.v6, rate=a.rate, trials=a.trials, ok=len(d),
                   min=min(d) if d else None, median=statistics.median(d) if d else None, max=max(d) if d else None,
                   kernel_frames_median=statistics.median(kf) if kf else None, kernel_frames_max=max(kf) if kf else None)
    print(json.dumps(summary))
    if a.out:
        with open(a.out, 'a') as f:
            f.write(json.dumps(dict(summary, runs=res)) + '\n')


if __name__ == '__main__':
    sys.exit(main())

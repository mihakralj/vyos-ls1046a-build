#!/usr/bin/env python3
"""Fire DUT-side jobs during a 60 s iperf3 --bidir run and show what each one does to throughput.

Runs on dell2 (RUN_ON=dell2 style: scripts live on the rig, ssh starts from the rig):
dell1 runs the iperf3 client (10.99.1.112 -> 10.99.2.113, the DUT's eth3 -> eth4 path, server on dell2),
the DUT jobs go over ssh with dell2's ~/.ssh/vyos_key.

usage: testrig-trigger-probe.py TAG name=cmd [name=cmd ...]
       env: START=13.4 STEP=10 (trigger k fires at START + STEP*k seconds after the client starts),
            DUT=vyos@192.168.1.185 OUTDIR=~/testrig/out STREAMS=8
The rig has its own periodic dips (about every 5 s, deeper every 10 s, phase 1 mod 5 s in the iperf3
interval index), so START=13.4 keeps the samples after a trigger (index s+1, s+2) off that grid.
Output: OUTDIR/trigger-TAG.json plus one line per trigger: the DUT job's run time, the median of the whole
run, and the 3 samples after the trigger second with their drop from that median."""
import json, os, statistics, subprocess, sys, time

OUTDIR = os.environ.get('OUTDIR', os.path.expanduser('~/testrig/out'))
os.makedirs(OUTDIR, exist_ok=True)
DUT_HOST = os.environ.get('DUT', 'vyos@192.168.1.185')
START = float(os.environ.get('START', '13.4'))
STEP = float(os.environ.get('STEP', '10'))
STREAMS = os.environ.get('STREAMS', '8')
KEY = os.path.expanduser('~/.ssh/vyos_key')
SSH = ['ssh', '-n', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8']
D1 = SSH + ['-i', KEY, 'admin@192.168.1.112']
DUT = SSH + ['-i', KEY, DUT_HOST]

tag = sys.argv[1]
trig = [a.split('=', 1) for a in sys.argv[2:]]
out = f'{OUTDIR}/trigger-{tag}.json'
cl = f'iperf3 -c 10.99.2.113 -B 10.99.1.112 -p 5201 -P {STREAMS} -t 60 -i 1 -Z --bidir -J'
t_start = time.time()
p = subprocess.Popen(D1 + [cl], stdout=open(out, 'w'))
times = []
for k, (name, cmd) in enumerate(trig):
    at = t_start + START + STEP * k
    time.sleep(max(0, at - time.time()))
    t0 = time.time()
    subprocess.run(DUT + [cmd], capture_output=True, text=True)
    times.append((name, t0, time.time()))
p.wait()
j = json.load(open(out))
ts = j['start']['timestamp']['timesecs']
if abs(ts - t_start) > 3:
    print(f'warning: dell1 clock differs from dell2 by {ts - t_start:.1f} s, trigger times are approximate')
b = [(i['sum']['bits_per_second'] + i.get('sum_bidir_reverse', {}).get('bits_per_second', 0)) / 1e9
     for i in j['intervals']]
med = statistics.median(b[12:])
print('gbps', [round(x, 1) for x in b])
print(f'median from t=12: {med:.2f}')
for name, t0, t1 in times:
    s = int(t0 - ts)
    post = b[s + 1:s + 4]
    print(f'{name}: fired t={t0 - ts:.1f}s ran {t1 - t0:.1f}s | after ' +
          ' '.join(f'{x:.2f}({x - med:+.2f})' for x in post))

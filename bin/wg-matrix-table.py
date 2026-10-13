#!/usr/bin/env python3
"""Median (min - max) table of the full WireGuard matrix for two DUTs. Usage: wg-matrix-table.py [outdir] [tag]"""
import csv, statistics as st, sys, collections, os
d = sys.argv[1] if len(sys.argv) > 1 else '/mnt/builds/ask2-review/oracle'
tag = sys.argv[2] if len(sys.argv) > 2 else 'wgfull'
def load(who):
    g = collections.defaultdict(list)
    for t in (1, 2, 4):
        f = os.path.join(d, f'{tag}-{who}-t{t}.csv')
        if not os.path.exists(f): continue
        for r in csv.DictReader(open(f)):
            try: v = float(r['gbps'])
            except Exception: continue
            if v > 0.05: g[(t, r['dir'])].append(v)
    return g
a, b = load('106'), load('185')
def fmt(v):
    if not v: return 'n/m'
    return '%.2f (%.2f - %.2f)' % (st.median(v), min(v), max(v))
rows = []
print('+---------+-------+----------------------+----------------------+-------+')
print('| Tunnels | Dir   | .106 reference       | .185 VyOS            | Gain  |')
print('+---------+-------+----------------------+----------------------+-------+')
for t in (1, 2, 4):
    for dr in ('up', 'down', 'bidir'):
        x, y = a.get((t, dr)), b.get((t, dr))
        gain = '-' if not x or not y else '%+.0f%%' % (100 * (st.median(y) / st.median(x) - 1))
        print('| %-7d | %-5s | %-20s | %-20s | %5s |' % (t, dr, fmt(x), fmt(y), gain))
    print('+---------+-------+----------------------+----------------------+-------+')
print('n per cell:', {k: len(v) for k, v in sorted(a.items())}, '|', {k: len(v) for k, v in sorted(b.items())})

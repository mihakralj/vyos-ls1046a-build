#!/usr/bin/env python3
"""WireGuard parity table, one row per port and direction, "v4 X / v6 Y" cells (inner family), median Gbit/s.
Usage: wg-parity-table.py [outdir] [tag]     reads <tag>-{106,185}-{eth3,eth4}.csv written by bin/wg-parity.sh (106 = vendor, 185 = ask2)
Verdict rule: ASK2 median below the vendor median is a FAIL (it needs investigation); "overlap" marks a FAIL whose
min-max ranges still overlap, so more reps can still change the answer."""
import csv, os, statistics as st, sys, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testrig_env  # noqa: E402

d = sys.argv[1] if len(sys.argv) > 1 else testrig_env.load()['RIG_OUT']
tag = sys.argv[2] if len(sys.argv) > 2 else 'wgpar'
FAM = {'wg-v4': 'v4', 'wg-v6': 'v6'}
DIRS = (('up', 'up (DUT encrypts)'), ('down', 'down (DUT decrypts)'), ('bidir', 'bidir'))


def load(who, port):
    g = collections.defaultdict(list)
    f = os.path.join(d, f'{tag}-{who}-{port}.csv')
    if not os.path.exists(f):
        return g
    suf = '-eth3' if port == 'eth3' else ''
    for r in csv.DictReader(open(f)):
        cell = r['cell'][:-len(suf)] if suf and r['cell'].endswith(suf) else r['cell']
        try:
            v = float(r['gbps'])
        except ValueError:
            continue
        if cell in FAM and v > 0.05:
            g[(r['dir'], FAM[cell])].append(v)
    return g


def cell(a, b, fmt):
    parts = []
    for fam in ('v4', 'v6'):
        v = (a if fmt == 'a' else b).get(fam)
        parts.append(f'{fam} {st.median(v):.2f}' if v else f'{fam} n/m')
    return ' / '.join(parts)


print('| WireGuard tunnel on | Direction | NXP ASK (vendor) | ASK2 | Verdict |')
print('|---|---|---|---|---|')
detail = []
for port in ('eth3', 'eth4'):
    v, a = load('106', port), load('185', port)
    for key, label in DIRS:
        vv = {f: v.get((key, f)) for f in ('v4', 'v6')}
        aa = {f: a.get((key, f)) for f in ('v4', 'v6')}
        verdict = []
        for fam in ('v4', 'v6'):
            x, y = vv[fam], aa[fam]
            if not x or not y:
                verdict.append(f'{fam} n/m')
                continue
            m, n = st.median(x), st.median(y)
            delta = 100 * (n / m - 1)
            if n >= m:
                verdict.append(f'{fam} PASS {delta:+.1f}%')
            else:
                overlap = max(min(x), min(y)) <= min(max(x), max(y))
                verdict.append(f'{fam} FAIL {delta:+.1f}%' + (' (overlap)' if overlap else ''))
            detail.append((port, key, fam, len(x), min(x), m, max(x), len(y), min(y), n, max(y)))
        print(f'| {port} | {label} | {cell(vv, aa, "a")} | {cell(vv, aa, "b")} | {"; ".join(verdict)} |')
print()
print('Spread (Gbit/s min / median / max, n): vendor | ASK2')
for p, k, f, nx, lx, mx, hx, ny, ly, my, hy in detail:
    print(f'  {p} {k:<5} {f}: {lx:.2f} / {mx:.2f} / {hx:.2f} (n={nx}) | {ly:.2f} / {my:.2f} / {hy:.2f} (n={ny})')

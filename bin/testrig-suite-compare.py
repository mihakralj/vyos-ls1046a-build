#!/usr/bin/env python3
"""testrig-suite-compare.py VENDOR_DIR ASK2_DIR: side-by-side table of two bin/testrig-suite-abcd.sh runs.

Rows are (test, metric) from results.csv; the delta is ASK2 relative to the vendor, in percent, for numeric values.
Measurements only, no pass/fail."""
import csv, os, sys


def load(d):
    out = {}
    with open(os.path.join(d, 'results.csv')) as f:
        for r in csv.DictReader(f):
            out[(r['test'], r['metric'])] = r
    return out


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    v, a = load(sys.argv[1]), load(sys.argv[2])
    keys = sorted(set(v) | set(a), key=lambda k: (k[0], list(v).index(k) if k in v else 10**6, k[1]))
    print('| test | metric | OWRT/ask | vyos/ASK2 | delta |')
    print('|---|---|---|---|---|')
    for k in keys:
        rv, ra = v.get(k, {}), a.get(k, {})
        x, y = num(rv.get('value')), num(ra.get('value'))
        d = '' if x is None or y is None or x == 0 else '%+.1f%%' % (100 * (y - x) / abs(x))
        print('| %s | %s | %s | %s | %s |' % (k[0], k[1], rv.get('value', ''), ra.get('value', ''), d))


main()

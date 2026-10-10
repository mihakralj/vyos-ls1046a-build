#!/usr/bin/env python3
"""testrig-compare-report.py RUN_DIR: markdown results table of a bin/testrig-compare.sh run, next to the
published Mono DK figures (VytautasJnk/mono-gw, armbian-ask-master-20261010-results, RESULTS.md)."""
import csv, json, os, sys

# their published numbers: (Mpps, Gbit/s, +-%) per size; latency p50/p99 per direction; kernel path
THEIRS = {
    'cmm': {'64': (3.29, 2.21, 0), '570': (1.09, 5.15, 96), '1518': (1.44, 17.66, 2), 'imix': (0.99, 2.96, 11)},
    'tuned': {'64': (3.18, 2.13, 0), '570': (2.70, 12.73, 1), '1518': (1.37, 16.86, 0), 'imix': (3.04, 9.09, 2)},
}
THEIR_LAT = {'offloaded': {0: '13.7 / 15.2', 10: '13.7 / 40.7', 50: '14.8 / 60.8', 90: '26.3 / 59.8'},
             'kernel': {0: '128 / 245', 10: '115 / 197', 50: '116 / 345', 90: '195 / 695'}}
THEIR_KPATH = {('bidir', '64'): '0.292 Mpps / 0.20 Gbit/s ±0 %', ('bidir', 'imix'): '0.291 Mpps / 0.87 Gbit/s ±6 %',
               ('1flow', '64'): '0.132 Mpps / 0.09 Gbit/s'}


def load(d):
    summ = {}
    p = os.path.join(d, 'summary.csv')
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            summ[(r['label'], r['size'])] = r
    jl = lambda f: [json.loads(l) for l in open(os.path.join(d, f))] if os.path.exists(os.path.join(d, f)) else []
    return summ, jl('lat.jsonl'), jl('cps.jsonl')


def cell(r):
    if not r or int(r['n']) == 0:
        return 'not measured'
    return f"{int(r['mean_pps']) / 1e6:.2f} Mpps / {float(r['gbit_l1']):.2f} Gbit/s ±{float(r['maxdev_pct']):.0f} %"


def main(d):
    summ, lat, cps = load(d)
    out = [f'# ASK2 vs NXP ASK on the Mono DK ({os.path.basename(d)})', '']
    out += ['## Stateful firewall, offloaded (8k sessions, both directions, PDR ≤ 0.5 %, mean of 3)', '',
            '| Size | cmm as shipped | ASK master tuned | ASK2 | ASK2 HW share | ASK2 MAC RX drops eth3 / eth4 |',
            '|---|---|---|---|---|---|']
    for s in ('64', '570', '1518', 'imix'):
        r = summ.get(('firewall', s))
        c, t = THEIRS['cmm'][s], THEIRS['tuned'][s]
        out.append(f"| {s.upper() if s == 'imix' else s + ' B'} | {c[0]:.2f} Mpps / {c[1]:.2f} Gbit/s ±{c[2]} % "
                   f"| {t[0]:.2f} Mpps / {t[1]:.2f} Gbit/s ±{t[2]} % | {cell(r)} "
                   f"| {r['hw_share'] if r else ''} | {(r['rdrp_eth3'] + ' / ' + r['rdrp_eth4']) if r else ''} |")
    for label, title in (('offloaded', 'Latency, firewall offloaded'), ('kernel', 'Latency, routing on the kernel path')):
        rows = [x for x in lat if x.get('path') == label]
        out += ['', f'## {title} (µs, p50 / p99)', '',
                '| Load | ASK master tuned, eth3→eth4 | ASK2 per direction (half RTT) | ASK2 round trip | ASK2 probes ok / lost |',
                '|---|---|---|---|---|']
        for x in rows:
            l = x['load_pct']
            h, rt = x['half_rtt_us'], x['rtt_us']
            out.append(f"| {'idle' if l == 0 else f'~{l} %'} | {THEIR_LAT[label].get(l, '')} | {h['p50']} / {h['p99']} "
                       f"| {rt['p50']} / {rt['p99']} | {x['ok']} / {x['lost']} |")
        if not rows:
            out.append('| | | not measured | | |')
    out += ['', '## Kernel path, not offloaded (PDR ≤ 0.5 %)', '',
            '| Test | ASK master tuned | ASK2, ASK disengaged | ASK2, ASK engaged (miss path) |', '|---|---|---|---|']
    for kind, s, name in (('bidir', '64', 'Routing, 64 B'), ('bidir', 'imix', 'Routing, IMIX'), ('1flow', '64', 'Routing, 1 flow')):
        out.append(f"| {name} | {THEIR_KPATH[(kind, s)]} | {cell(summ.get((f'kpath-off-{kind}', s)))} "
                   f"| {cell(summ.get((f'kpath-engaged-{kind}', s)))} |")
    out += ['', '## Connections (stateful firewall)', '',
            '| Test | ASK master tuned | ASK2 achieved | failed | held alive | client retransmits | DUT conntrack drops / after | Verdict |',
            '|---|---|---|---|---|---|---|---|']
    for x in cps:
        ok = x['failed'] == 0 and x['hold_alive'] == x['hold'] and x['cps'] >= 0.99 * x['rate'] and x['client_retrans_pct'] <= 0.1
        theirs = ('PASS 20k conn/s + 20k hold' if not x['nat'] else 'PASS 20k conn/s') if x['rate'] == 20000 else ''
        out.append(f"| {x['rate'] // 1000}k conn/s{' + NAT' if x['nat'] else ''} + {x['hold'] // 1000}k held | {theirs} "
                   f"| {x['cps']}/s | {x['failed']} {x['errors'] or ''} | {x['hold_alive']} / {x['hold']} "
                   f"| {x['client_retrans_pct']} % | {x['dut_ct_drops']} / {x['dut_conntrack_after']} | {'PASS' if ok else 'FAIL'} |")
    if not cps:
        out.append('| | | not measured | | | | | |')
    out += ['', 'TCP application mix: not measured (no TRex ASTF equivalent on this rig).']
    print('\n'.join(out))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])

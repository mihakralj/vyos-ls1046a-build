#!/usr/bin/env python3
"""trex-report-tables.py ndr <outdir> <title> | ladder <ladder3.csv> <title> <trial_s> <rate>

Presentation layer on top of bin/testrig-trex-ndr.sh's results-*.csv (via the same aggregation as
bin/trex-ndr-table.py) and bin/testrig-flow-ladder.sh's ladder3.csv. Prints the two-table report format
used in perf writeups: plain best-of-all-reps Mpps for the NDR/PDR/TP90/MRR table (no %-of-line
annotation; TP99 is dropped here as redundant with PDR), and flows/offloaded/full-after/load-max/stuck-max/
trial-loss for the flow ladder.
"""
import csv
import sys
from pathlib import Path

def _load_ndr_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("trex_ndr_table", Path(__file__).parent / "trex-ndr-table.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ndr_table(outdir, title):
    m = _load_ndr_module()
    g = m.collect(outdir)
    metrics = tuple(x for x in m.METRICS if x != "TP99")  # TP99 dropped: binary search converges to ~PDR, redundant
    sizes_flows = sorted({(k[1], k[2]) for k in g if k[0] == "ALL"})
    print(f"TRex throughput, {title}. Best of all runs; Mpps delivered, both directions combined.\n")
    hdr = f"{'Frame':>6} {'Flows':>6}  " + "".join(f"{x:>6}" for x in metrics)
    print(hdr)
    for s, f in sizes_flows:
        cells = []
        for metric in metrics:
            rows = g.get(("ALL", s, f, metric))
            cells.append(f"{'-':>6}" if not rows else f"{m.summarize(rows)['best']:>6.3f}")
        print(f"{str(s) + 'B':>6} {f:>6}  " + "".join(cells))


def _flt(val, default=None):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def ladder_table(csv_path, title, trial_s, rate):
    rows = list(csv.DictReader(open(csv_path)))
    best = {}
    for r in rows:
        f = int(r["flows"])
        if f not in best or _flt(r.get("offloaded_max"), -1.0) > _flt(best[f].get("offloaded_max"), -1.0):
            best[f] = r
    packets = f"{round(trial_s * rate / 1e6)} M packets" if rate >= 1e6 else f"{round(trial_s * rate)} packets"
    print(f"64 B flow ladder, {title}. Trial: {trial_s} s at {rate / 1e6:g} Mpps ({packets}).\n")
    print(f"{'flows':>7}   {'offloaded':>14}   {'full after':>10}  {'load max':>8}  {'stuck max':>9}  {'trial loss':>10}")
    for f in sorted(best):
        r = best[f]
        off, exp = r["offloaded_max"], r["expected"]
        loss_v = _flt(r.get("trial_loss_pct"))
        loss_s = f"{loss_v:.1f} %" if loss_v is not None else "n/a"
        if off in ("", "0") and r.get("verdict", "").lower() not in ("pass", ""):
            print(f"{f:>7}   {'n/a (sample failed, board saturated)':<38}  {'n/a':>8}  {'n/a':>9}  {loss_s}")
            continue
        full_v = _flt(r.get("t_full_s"))
        full_s = f"{full_v:.1f} s" if full_v is not None else "n/a"
        load_v = _flt(r.get("load_max"))
        load_s = f"{load_v:>8.2f}" if load_v is not None else f"{'n/a':>8}"
        print(f"{f:>7}   {off:>6} / {exp:<6}   {full_s:>10}  {load_s}  {r['blocked_max']:>9}  {loss_s}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "ndr":
        ndr_table(sys.argv[2], sys.argv[3])
    elif cmd == "ladder":
        ladder_table(sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5]))
    else:
        sys.exit(f"usage: {sys.argv[0]} ndr <outdir> <title> | ladder <ladder3.csv> <title> <trial_s> <rate>")

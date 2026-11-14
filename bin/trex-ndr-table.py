#!/usr/bin/env python3
"""trex-ndr-table.py <outdir>: aggregate results-*.csv (bin/testrig-trex-ndr.sh) into best.csv and print the best-of-all table.

best.csv: one row per (dut|ALL, size, flows, metric): n, best/mean/stdev/min rx Mpps, best Gbit/s L2/L1, % line rate,
and which dut/rep gave the best. The table shows, per size x flows, best-of-all Mpps (% of line rate) per metric."""
import csv, statistics, sys
from collections import defaultdict
from pathlib import Path

# TP99 (<= 1 % loss) is no longer measured or reported: the binary search lands on ~PDR, so it added a cell
# without information. Rows for metrics not listed here (TP99 in older results) are ignored.
METRICS = ("NDR", "PDR", "TP90", "MRR")


def collect(out):
    g = defaultdict(list)
    for p in sorted(Path(out).glob("results-*.csv")):
        for r in csv.DictReader(open(p)):
            if r["metric"] not in METRICS:
                continue
            for dut in (r["dut"], "ALL"):
                g[(dut, int(r["size"]), int(r["flows"]), r["metric"])].append(r)
    return g


def summarize(rows):
    v = [float(r["rx_mpps"]) for r in rows]
    b = max(rows, key=lambda r: float(r["rx_mpps"]))
    return dict(n=len(v), best=max(v), mean=statistics.fmean(v), stdev=statistics.stdev(v) if len(v) > 1 else 0.0,
                min=min(v), gbps_l2=float(b["gbps_l2"]), gbps_l1=float(b["gbps_l1"]), pct_line=float(b["pct_line"]),
                from_=f"{b['dut']}/r{b['rep']}")


def render(g, dut="ALL"):
    out = [f"{'size':>5} {'flows':>6}  " + "".join(f"{m:>16}" for m in METRICS)]
    for s, f in sorted({(k[1], k[2]) for k in g if k[0] == dut}):
        cells = []
        for m in METRICS:
            rows = g.get((dut, s, f, m))
            cells.append(f"{'-':>16}" if not rows else
                         "{:>16}".format("%.3f (%.0f%%)" % (summarize(rows)["best"], summarize(rows)["pct_line"])))
        out.append(f"{s:>5} {f:>6}  " + "".join(cells))
    return "\n".join(out)


def main():
    out = sys.argv[1]
    g = collect(out)
    with open(Path(out) / "best.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["dut", "size", "flows", "metric", "n", "best_mpps", "mean_mpps", "stdev_mpps", "min_mpps",
                    "best_gbps_l2", "best_gbps_l1", "best_pct_line", "best_from"])
        for k in sorted(g, key=lambda k: (k[0], k[1], k[2], METRICS.index(k[3]))):
            s = summarize(g[k])
            w.writerow([*k, s["n"], *(f"{s[x]:.4f}" for x in ("best", "mean", "stdev", "min")),
                        f"{s['gbps_l2']:.3f}", f"{s['gbps_l1']:.3f}", f"{s['pct_line']:.2f}", s["from_"]])
    print("Best of all reps, both DUTs: delivered Mpps, both directions (% of 2 x 10GbE line rate)")
    print(render(g))


def demo(tmp):
    hdr = "ts,dut,image,boot,rep,size,flows,metric,loss_limit_pct,rate_pct,tx_mpps,rx_mpps,drop_pct,gbps_l2,gbps_l1,pct_line,trial_s,iterations,elapsed_s"
    (tmp / "results-dut1.csv").write_text(hdr + "\nt,dut1,i,b,1,64,1,PDR,0.5,16,4.5,4.48,0.1,2.29,3.01,15.06,30,9,300\n"
                                          "t,dut1,i,b,2,64,1,PDR,0.5,16,4.6,4.52,0.1,2.31,3.04,15.19,30,9,300\n")
    (tmp / "results-dut2.csv").write_text(hdr + "\nt,dut2,i,b,1,64,1,PDR,0.5,16,4.4,4.40,0.1,2.25,2.96,14.78,30,9,300\n"
                                          "t,dut2,i,b,1,64,1,TP99,1,17,4.5,4.45,0.9,2.27,2.99,14.95,30,9,300\n")
    g = collect(tmp)
    assert not any(k[3] == "TP99" for k in g) and "TP99" not in render(g)   # older results: ignored
    s = summarize(g[("ALL", 64, 1, "PDR")])
    assert (s["n"], s["best"], s["min"], s["from_"]) == (3, 4.52, 4.40, "dut1/r2") and round(s["stdev"], 4) == 0.0611
    assert summarize(g[("dut2", 64, 1, "PDR")])["stdev"] == 0.0
    assert "4.520 (15%)" in render(g) and render(g).splitlines()[1].split()[2] == "-"
    sys.argv = ["x", str(tmp)]; main()
    assert "ALL,64,1,PDR,3,4.5200,4.4667,0.0611,4.4000,2.310,3.040,15.19,dut1/r2" in (tmp / "best.csv").read_text()


if __name__ == "__main__":
    if sys.argv[1:] == ["--test"]:
        import tempfile; demo(Path(tempfile.mkdtemp())); print("ok")
    else:
        main()

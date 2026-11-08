#!/usr/bin/env python3
"""ASK2-only flow-type matrix (plans/ASK2-PERF-MATRIX-PLAN.md, Layer A): one row per flow type,
Upload / Download / Bidir x v4 / v6, FASTEST rep in Gbit/s. Invalid or unmeasured slots print "-".

Usage: ask2-matrix-table.py <run-dir>
Reads every quick-*.csv (bin/testrig-offload-quick.sh rows: ts,cell,gbps,...,verdict) and wg-*.csv
(bin/testrig-wireguard.sh rows: ts,cell,dir,rep,gbps,...) in <run-dir>. Writes <run-dir>/matrix.csv
(type,fam,dir,gbps=max,n,verdicts) and prints the table. Up = eth3 -> eth4 (PPPoE: encap), Down = the
reverse, Bidir = sum of both directions.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

QUICK = {  # type: (upload cell, download cell, bidir cell), {f} = 4|6
    "unicast": ("unicast-v{f}", "unicast-v{f}-rev", "unicast-v{f}-bidir"),
    "nat": ("nat-v{f}", "nat-v{f}-rev", "nat-v{f}-bidir"),
    "vlan-vlan": ("vlan-v{f}", "vlan-v{f}-rev", "vlan-v{f}-bidir"),
    "vlan-port": ("vlanport-v{f}", "portvlan-v{f}", "vlanport-v{f}-bidir"),
    "pppoe": ("pppoe-up-v{f}", "pppoe-down-v{f}", "pppoe-up-v{f}-bidir"),
}
ROWS = ["unicast", "nat", "vlan-vlan", "vlan-port", "pppoe", "wireguard", "ipsec", "bridge",
        "broadcast", "multicast", "fragments", "policer"]
DIRS = ("up", "down", "bidir")


def collect(run):
    vals, verd = defaultdict(list), defaultdict(list)
    for p in sorted(Path(run).glob("quick-*.csv")):
        for r in csv.reader(open(p)):
            if len(r) < 11 or r[1] == "cell":
                continue
            try:
                g = float(r[2])
            except ValueError:
                continue
            if g > 0.05:
                vals[r[1]].append(g)
                verd[r[1]].append(r[10])
    for p in sorted(Path(run).glob("wg-*.csv")):
        for r in csv.DictReader(open(p)):
            cell = r.get("cell", "").removesuffix("-eth3")
            try:
                g = float(r.get("gbps", ""))
            except ValueError:
                continue
            if cell in ("wg-v4", "wg-v6") and g > 0.05:
                vals[f"wireguard-{cell[3:]}-{r['dir']}"].append(g)
                verd[f"wireguard-{cell[3:]}-{r['dir']}"].append("SW")
    table = {}
    for t in ROWS:
        for f in ("4", "6"):
            for i, d in enumerate(DIRS):
                key = QUICK[t][i].format(f=f) if t in QUICK else f"{t}-v{f}-{d}"
                v = vals.get(key)
                table[(t, f, d)] = (max(v) if v else None, len(v or ()),
                                    "/".join(sorted(set(verd.get(key, ())))))
    return table


def fmt(v):
    return "-" if v is None else (f"{v:.1f}" if v >= 10 else f"{v:.2f}")


def render(table):
    out = ["Flow type".ljust(16) + "Upload".ljust(16) + "Download".ljust(18) + "Bidir",
           " " * 10 + "".join(f"{fam:>8}" for _ in DIRS for fam in ("v4", "v6"))]
    for t in ROWS:
        out.append(t.ljust(10) + "".join(f"{fmt(table[(t, f, d)][0]):>8}"
                                         for d in DIRS for f in ("4", "6")))
    return "\n".join(out)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    table = collect(sys.argv[1])
    with open(Path(sys.argv[1]) / "matrix.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["type", "fam", "dir", "gbps", "n", "verdicts"])
        for (t, f, d), (v, n, vd) in table.items():
            w.writerow([t, f"v{f}", d, "" if v is None else f"{v:.3f}", n, vd])
    print(render(table))


if __name__ == "__main__":
    main()

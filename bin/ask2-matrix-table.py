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
NINEK = ["unicast", "nat", "vlan-vlan", "vlan-port"]   # MTU 9000 rows, from quick9k-*.csv
LABEL = {"vlan-vlan-9k": "vlan-9k"}
ROWS = ["unicast", "nat", "vlan-vlan", "vlan-port", "pppoe", "bridge"] + [f"{t}-9k" for t in NINEK] + ["bridge-9k",
        "wireguard", "wireguard6", "policer"]
# Not measured (decision 2026-11-09): ipsec, multicast and broadcast have no ASK2 hardware offload,
# so a row would only show the Linux software rate; fragment cells are not needed either.
DIRS = ("up", "down", "bidir")


def collect(run):
    vals, verd = defaultdict(list), defaultdict(list)
    for p in sorted(Path(run).glob("quick-*.csv")) + sorted(Path(run).glob("quick9k-*.csv")):
        pre = "9k:" if p.name.startswith("quick9k-") else ""
        for r in csv.reader(open(p)):
            if len(r) < 11 or r[1] == "cell":
                continue
            try:
                g = float(r[2])
            except ValueError:
                continue
            if g > 0.05:
                vals[pre + r[1]].append(g)
                verd[pre + r[1]].append(r[10])
    # bridge-<mtu>.csv (bin/testrig-ask2-bridge.sh): ts,mtu,fam,dir,rep,gbps,dut_busy
    for p in sorted(Path(run).glob("bridge-*.csv")):
        for r in csv.DictReader(open(p)):
            try:
                g = float(r["gbps"])
            except (KeyError, ValueError):
                continue
            row = "bridge" if r["mtu"] == "1500" else "bridge-9k"
            if g > 0.05:
                vals[f"{row}-v{r['fam']}-{r['dir']}"].append(g)
                verd[f"{row}-v{r['fam']}-{r['dir']}"].append("HW" if float(r["dut_busy"]) < 10 else "SW?")
    # policer-*.csv (bin/testrig-ask2-policer.sh): delivered upload rate through the 1G ingress policer
    for p in sorted(Path(run).glob("policer-*.csv")):
        for r in csv.reader(open(p)):
            if len(r) < 11 or r[1] not in ("unicast-v4", "unicast-v6"):
                continue
            try:
                g = float(r[2])
            except ValueError:
                continue
            if g > 0.05:
                vals[f"policer-v{r[1][-1]}-up"].append(g)
                verd[f"policer-v{r[1][-1]}-up"].append("HW")
    # wg-*.csv: IPv4 outer transport; wg6-*.csv: IPv6 outer (OUTER=6). Columns are the inner family.
    for p in sorted(Path(run).glob("wg-*.csv")) + sorted(Path(run).glob("wg6-*.csv")):
        row = "wireguard6" if p.name.startswith("wg6-") else "wireguard"
        for r in csv.DictReader(open(p)):
            cell = r.get("cell", "").removesuffix("-eth3")
            try:
                g = float(r.get("gbps", ""))
            except ValueError:
                continue
            if cell in ("wg-v4", "wg-v6") and g > 0.05:
                vals[f"{row}-{cell[3:]}-{r['dir']}"].append(g)
                verd[f"{row}-{cell[3:]}-{r['dir']}"].append("TX+RX")
    table = {}
    for t in ROWS:
        for f in ("4", "6"):
            for i, d in enumerate(DIRS):
                base = t.removesuffix("-9k")
                pre = "9k:" if t.endswith("-9k") else ""
                key = pre + QUICK[base][i].format(f=f) if base in QUICK else f"{t}-v{f}-{d}"
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
        out.append(LABEL.get(t, t).ljust(12) + "".join(f"{fmt(table[(t, f, d)][0]):>8}"
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

#!/usr/bin/env python3
"""One-line ASK2 WireGuard summary: median Gbit/s for up, down and bidir, each for inner IPv4 and IPv6.

Usage: wg-line.py <csv> [<csv> ...] [--label TEXT] [--header]
Reads rows written by bin/testrig-wireguard.sh (cells wg-v4/wg-v6, an optional -eth3 suffix is ignored).
Zero-traffic or failed reps (<= 0.05 Gbit/s) are excluded; a missing cell prints "-".
"""
import csv
import statistics
import sys

COLS = [(d, f) for d in ("up", "down", "bidir") for f in ("v4", "v6")]
HEADER = "up-v4 up-v6 dn-v4 dn-v6 bi-v4 bi-v6"


def medians(paths):
    vals = {c: [] for c in COLS}
    for p in paths:
        for r in csv.DictReader(open(p)):
            cell = r.get("cell", "").removesuffix("-eth3")
            fam = {"wg-v4": "v4", "wg-v6": "v6"}.get(cell)
            try:
                g = float(r.get("gbps", ""))
            except ValueError:
                continue
            if fam and (r.get("dir"), fam) in vals and g > 0.05:
                vals[(r["dir"], fam)].append(g)
    return {c: (statistics.median(v) if v else None) for c, v in vals.items()}


def line(meds, label=""):
    nums = " ".join(f"{meds[c]:5.2f}" if meds[c] is not None else "    -" for c in COLS)
    return f"{label}  {nums}".strip()


def main(argv):
    label, paths, header = "", [], False
    it = iter(argv)
    for a in it:
        if a == "--label":
            label = next(it)
        elif a == "--header":
            header = True
        else:
            paths.append(a)
    if not paths:
        sys.exit(__doc__)
    if header:
        print(HEADER)
    print(line(medians(paths), label))


if __name__ == "__main__":
    main(sys.argv[1:])

#!/usr/bin/env python3
"""Render perf/catalogue.csv into the tables of plans/OFFLOAD-TEST-CATALOGUE.md.

The CSV is authoritative. Each table sits between
<!-- BEGIN TABLE:<group> --> and <!-- END TABLE:<group> --> markers (a bare
<!-- TABLE:<group> --> placeholder is expanded on first run). Re-run after
editing the CSV:

    bin/perf-catalogue-render.py            # rewrite the doc in place
    bin/perf-catalogue-render.py --check    # exit 1 if the doc is stale
"""
import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "perf" / "catalogue.csv"
DOC = ROOT / "plans" / "OFFLOAD-TEST-CATALOGUE.md"

MARK = re.compile(
    r"<!-- BEGIN TABLE:(?P<g>[\w-]+) -->.*?<!-- END TABLE:(?P=g) -->"
    r"|<!-- TABLE:(?P<p>[\w-]+) -->",
    re.S,
)


def cell(s):
    return (s or "").replace("|", "\\|").strip() or "–"


def code(s):
    return cell(s) if not s or s.startswith("TBD") else f"`{s}`"


def table(rows, group):
    sel = [r for r in rows if r["group"] == group]
    if not sel:
        sys.exit(f"no catalogue rows for group '{group}'")
    last = "Owner gate" if group == "gap" else "Last evidence"
    out = [
        f"| ID | Test | Driver | Metric | Pass rule | ASK2 | OWRT | OPNS | T0/T1/T2 | {last} |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sel:
        tail = r["owner"] if group == "gap" else r["evidence"]
        out.append(
            "| {id} | {name} | {drv} | {unit} | {rule} | {a} | {o} | {p} | {t0}/{t1}/{t2} | {tail} |".format(
                id=r["test_id"], name=cell(r["name"]), drv=code(r["driver"]),
                unit=cell(r["unit"]), rule=cell(r["pass_rule"]),
                a=cell(r["vyos_ask2"]), o=cell(r["owrt_vendor"]), p=cell(r["opns"]),
                t0=r["t0"], t1=r["t1"], t2=r["t2"], tail=cell(tail),
            )
        )
    return f"<!-- BEGIN TABLE:{group} -->\n" + "\n".join(out) + f"\n<!-- END TABLE:{group} -->"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if the doc is out of date")
    a = ap.parse_args()

    rows = list(csv.DictReader(CSV.open(newline="")))
    ids = [r["test_id"] for r in rows]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        sys.exit(f"duplicate test_id(s): {sorted(dup)}")

    doc = DOC.read_text()
    seen = set()

    def sub(m):
        g = m.group("g") or m.group("p")
        seen.add(g)
        return table(rows, g)

    new = MARK.sub(sub, doc)
    missing = {r["group"] for r in rows} - seen
    if missing:
        sys.exit(f"catalogue groups with no table marker in the doc: {sorted(missing)}")

    if a.check:
        if new != doc:
            print(f"{DOC.relative_to(ROOT)} is stale; run {Path(__file__).name}", file=sys.stderr)
            return 1
        return 0
    DOC.write_text(new)
    print(f"rendered {len(rows)} rows into {len(seen)} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Append testrig result CSVs to the perf ledger (perf/runs.csv, perf/campaigns.csv).

Reads the per-cell CSV that bin/testrig-offload-quick.sh writes
(ts,cell,gbps,dut_busy,dut_softirq,gen_cpu,retrans,hw_pkts,data_pkts_est,hw_ratio,verdict)
and maps each cell name to a catalogue test_id through the driver column of
perf/catalogue.csv ("... testrig-offload-quick.sh <cell>").

Usage:
  bin/perf-ingest.py --csv RESULT.csv --dut-os ask2|owrt|opns --dut-host vyos@192.168.1.185 \\
      --image 2026.10.25-2342-rolling --campaign ask2-t0-20261026 --tier T0 \\
      [--slot A|B] [--kernel K] [--boot cold|warm] [--raw-dir DIR] [--ref-os ... --ref-host ... --ref-image ...]
  bin/perf-ingest.py --check          # validate runs.csv / campaigns.csv shape only

A result file is appended only once per campaign; re-ingesting it is refused.
"""
import argparse
import csv
import datetime
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = os.path.join(ROOT, "perf", "catalogue.csv")
RUNS = os.path.join(ROOT, "perf", "runs.csv")
CAMP = os.path.join(ROOT, "perf", "campaigns.csv")


def header(path):
    with open(path, newline="") as f:
        return next(csv.reader(f))


def cell_map():
    m = {}
    with open(CAT, newline="") as f:
        for r in csv.DictReader(f):
            parts = r["driver"].split()
            if len(parts) >= 2 and parts[0].startswith("bin/testrig-") and parts[0].endswith(".sh"):
                m.setdefault((parts[0], parts[1]), []).append(r["test_id"])
    return m


def check():
    ok = True
    for path in (RUNS, CAMP):
        h = header(path)
        with open(path, newline="") as f:
            for i, row in enumerate(csv.reader(f), 1):
                if len(row) != len(h):
                    print(f"{path}:{i}: {len(row)} fields, expected {len(h)}", file=sys.stderr)
                    ok = False
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--csv")
    ap.add_argument("--driver", default="bin/testrig-offload-quick.sh",
                    help="catalogue driver script the result file came from")
    ap.add_argument("--dut-os")
    ap.add_argument("--dut-host")
    ap.add_argument("--image")
    ap.add_argument("--campaign")
    ap.add_argument("--tier", default="T0")
    ap.add_argument("--slot", default="")
    ap.add_argument("--kernel", default="")
    ap.add_argument("--boot", default="warm")
    ap.add_argument("--raw-dir", default="")
    ap.add_argument("--ref-os", default="")
    ap.add_argument("--ref-host", default="")
    ap.add_argument("--ref-image", default="")
    ap.add_argument("--status", default="complete")
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    if a.check:
        return 0 if check() else 1
    for need in ("csv", "dut_os", "dut_host", "image", "campaign"):
        if not getattr(a, need):
            ap.error(f"--{need.replace('_', '-')} is required")

    digest = hashlib.sha256(open(a.csv, "rb").read()).hexdigest()[:12]
    with open(RUNS, newline="") as f:
        existing = list(csv.DictReader(f))
    if any(r["campaign_id"] == a.campaign and r["raw_ref"].endswith("#" + digest) for r in existing):
        sys.exit(f"already ingested: {a.csv} ({digest}) in campaign {a.campaign}")

    cmap = cell_map()
    cols = header(RUNS)
    reps = {}
    out = []
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(a.csv, newline="") as f:
        for r in csv.DictReader(f):
            cell = r["cell"]
            ids = cmap.get((a.driver, cell))
            if not ids:
                print(f"skip: no catalogue row for cell '{cell}'", file=sys.stderr)
                continue
            if r["verdict"] == "SKIP":
                continue
            tid = ids[0]
            reps[tid] = reps.get(tid, 0) + 1
            row = dict.fromkeys(cols, "")
            row.update(
                run_id=f"{a.campaign}-{tid}-{reps[tid]}",
                campaign_id=a.campaign,
                test_id=tid,
                rep=reps[tid],
                dut_os=a.dut_os,
                dut_host=a.dut_host,
                slot=a.slot,
                image=a.image,
                started_utc=r["ts"],
                metric="gbps",
                value=r["gbps"],
                unit="Gbit/s",
                hw_ratio=r["hw_ratio"],
                verdict=r["verdict"],
                dut_busy=r["dut_busy"],
                dut_softirq=r["dut_softirq"],
                gen_cpu=r["gen_cpu"],
                retrans=r["retrans"],
                raw_ref=f"{os.path.basename(a.csv)}#{digest}",
                note=a.note,
                variant_id=cell,
                protocol="tcp",
            )
            out.append(row)
    if not out:
        sys.exit("nothing to ingest")

    with open(RUNS, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        for row in out:
            w.writerow(row)

    with open(CAMP, newline="") as f:
        camps = {r["campaign_id"] for r in csv.DictReader(f)}
    if a.campaign not in camps:
        ccols = header(CAMP)
        crow = dict.fromkeys(ccols, "")
        crow.update(
            campaign_id=a.campaign, tier=a.tier, started_utc=out[0]["started_utc"] or now,
            finished_utc=now, dut_os=a.dut_os, dut_host=a.dut_host, image=a.image,
            kernel=a.kernel, boot=a.boot, ref_os=a.ref_os, ref_host=a.ref_host,
            ref_image=a.ref_image, status=a.status, raw_dir=a.raw_dir,
        )
        with open(CAMP, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=ccols, lineterminator="\n").writerow(crow)
    print(f"appended {len(out)} runs to {os.path.relpath(RUNS, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

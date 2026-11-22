#!/usr/bin/env python3
"""Schedule and validate alternating, matched jumbo benchmark groups."""

import argparse
import copy
import json
import os
from pathlib import Path
import re
import sys

import testrig_parity


ROWS = {
    "unicast-9k": ("unicast-v{}", "unicast-v{}-rev"),
    "nat-9k": ("nat-v{}", "nat-v{}-rev"),
    "vlan-9k": ("vlan-v{}", "vlan-v{}-rev"),
    "vlan-port-9k": ("vlanport-v{}", "portvlan-v{}"),
}
BOOT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def schedule(rows, repetitions):
    rows = list(rows) or list(ROWS)
    if repetitions != 3 or len(rows) != len(set(rows)) or any(row not in ROWS for row in rows):
        raise ValueError("jumbo battery requires distinct supported rows and three repetitions")
    return [(row, rep, board) for row in rows for rep in range(1, 4)
            for board in ("ask2", "vendor")]


def cells(row):
    if row not in ROWS:
        raise ValueError(f"unsupported jumbo row: {row}")
    upload, download = ROWS[row]
    return [(f"{direction}-v{family}",
             (download if direction == "down" else upload).format(family),
             int(direction == "bidir"))
            for direction in ("up", "down", "bidir") for family in (4, 6)]


def compare_snapshot(baseline, data, active, label):
    if active not in ("ask2", "vendor"):
        raise ValueError(f"invalid active generator path: {active}")
    for field in ("kernel", "iperf", "iperf_sha256"):
        if not isinstance(baseline.get(field), str) or not baseline[field]:
            raise ValueError(f"baseline lacks generator {field}")
    if not re.fullmatch(r"[0-9a-f]{64}", baseline["iperf_sha256"]):
        raise ValueError("invalid generator iperf SHA-256")
    testrig_parity.compare_ports(baseline, 1500)
    for field in ("kernel", "iperf", "iperf_sha256"):
        if data.get(field) != baseline.get(field):
            raise ValueError(f"{label}: generator {field} changed")
    inactive = "vendor" if active == "ask2" else "ask2"
    if data["ports"][inactive] != baseline["ports"][inactive]:
        raise ValueError(f"{label}: inactive generator path changed")
    current = copy.deepcopy(data["ports"][active])
    if current.get("mtu") != 9000 or current.get("vlan", {}).get("mtu") != 9000:
        raise ValueError(f"{label}: active physical/VLAN MTUs are not 9000")
    current["mtu"] = 1500
    current["vlan"]["mtu"] = 1500
    if current != baseline["ports"][active]:
        raise ValueError(f"{label}: active generator conditions changed")


def compare_group(baseline, ask_before, ask_after, vendor_before, vendor_after):
    for data, active, label in (
        (ask_before, "ask2", "DUT1 before"), (ask_after, "ask2", "DUT1 after"),
        (vendor_before, "vendor", "DUT2 before"), (vendor_after, "vendor", "DUT2 after"),
    ):
        compare_snapshot(baseline, data, active, label)
    matched = copy.deepcopy(baseline)
    matched["ports"] = {
        "ask2": ask_before["ports"]["ask2"],
        "vendor": vendor_before["ports"]["vendor"],
    }
    testrig_parity.compare_ports(matched, 9000)


def compare_reference(reference):
    profile = testrig_parity.protocol()
    if profile["mtu"] != 9000 or profile["repetitions"] != 3:
        raise ValueError("jumbo profile requires MTU 9000 and three repetitions")
    profile["mtu"] = 1500
    if reference != profile:
        raise ValueError("jumbo protocol differs from the reference beyond MTU")


def read_json(path):
    with path.open() as source:
        return json.load(source)


def verify_snapshots(directory, row, rep):
    baseline = directory / "baseline"
    ask = directory / f"ask2-{row}-r{rep}-snapshot"
    vendor = directory / f"vendor-{row}-r{rep}-snapshot"
    for gen in (1, 2):
        compare_group(read_json(baseline / f"gen{gen}-before.json"),
                      read_json(ask / f"gen{gen}-before.json"),
                      read_json(ask / f"gen{gen}-after.json"),
                      read_json(vendor / f"gen{gen}-before.json"),
                      read_json(vendor / f"gen{gen}-after.json"))


def verify_snapshot(directory, row, rep, board, phase):
    directory = Path(directory)
    if row not in ROWS or rep not in (1, 2, 3) or board not in ("ask2", "vendor"):
        raise ValueError("invalid jumbo group")
    if phase not in ("before", "after"):
        raise ValueError("invalid snapshot phase")
    for gen in (1, 2):
        baseline = directory / "baseline" / f"gen{gen}-before.json"
        snapshot = directory / f"{board}-{row}-r{rep}-snapshot" / f"gen{gen}-{phase}.json"
        compare_snapshot(read_json(baseline), read_json(snapshot), board, f"{board} {phase}")


def record_group(directory, vendor_run, row, rep):
    directory, vendor_run = Path(directory), Path(vendor_run)
    if row not in ROWS or type(rep) is not int or rep not in (1, 2, 3):
        raise ValueError("invalid jumbo row or repetition")
    profile = testrig_parity.protocol()
    if profile["mtu"] != 9000 or profile["repetitions"] != 3:
        raise ValueError("jumbo profile requires MTU 9000 and three repetitions")
    boots = [(directory / f"boot-{board}.txt").read_text().strip()
             for board in ("ask2", "vendor")]
    if not all(BOOT_ID.fullmatch(boot) for boot in boots):
        raise ValueError("invalid cold-boot identifiers")
    if (directory / f"ask2-{row}-r{rep}.complete").read_text().splitlines() != [f"boot={boots[0]}"]:
        raise ValueError("DUT1 setup or cleanup is incomplete")
    if (vendor_run / "COMPLETE").read_text().splitlines() != ["mode=run-routed", f"boot={boots[1]}"]:
        raise ValueError("DUT2 setup or cleanup is incomplete")
    verify_snapshots(directory, row, rep)
    outputs = [directory / f"pair-{row}-{column}-r{rep}.json"
               for column, _, _ in cells(row)]
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise ValueError("paired result already exists")
    results = []
    previous_bidir = os.environ.get("BIDIR")
    try:
        for column, cell, bidir in cells(row):
            os.environ["BIDIR"] = str(bidir)
            tag = cell + ("-bidir" if bidir else "")
            stem = directory / f"ask2-{row}-{column}-r{rep}"
            results.append(testrig_parity.pair_report(
                [Path(f"{stem}.csv"), vendor_run / f"quick9k-{tag}-r1.csv"],
                [Path(f"{stem}-json") / f"{tag}.json",
                 vendor_run / "json" / f"{tag}-r1" / f"{tag}.json"], cell))
    finally:
        if previous_bidir is None:
            os.environ.pop("BIDIR", None)
        else:
            os.environ["BIDIR"] = previous_bidir
    for path, result in zip(outputs, results):
        with path.open("x") as output:
            json.dump(result, output, indent=2)
            output.write("\n")


def initialize(directory, rows):
    groups = schedule(rows, int(os.environ["REPS"]))
    if os.environ.get("PARITY_MTU") != "9000":
        raise ValueError("jumbo battery requires PARITY_MTU=9000")
    selected = list(dict.fromkeys(row for row, _, _ in groups))
    plan = {"platforms": {"ask2": "VyOS", "vendor": "OpenWrt"},
            "order": "row, repetition, DUT1, DUT2, column",
            "repetitions": 3, "rows": [{"name": row, "status": "pending"} for row in selected],
            "trials": []}
    with (directory / "battery.json").open("x") as output:
        json.dump(plan, output, indent=2)
        output.write("\n")
    return groups


def verify_complete(directory):
    directory = Path(directory)
    plan = read_json(directory / "battery.json")
    rows = [row["name"] for row in plan["rows"]]
    schedule(rows, plan["repetitions"])
    if not rows or any(row["status"] != "pending" for row in plan["rows"]):
        raise ValueError("invalid jumbo battery plan")
    results = testrig_parity.battery_report(directory)
    if any(row["status"] != "complete" for row in results):
        raise ValueError("jumbo battery has incomplete paired cells")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    init = commands.add_parser("schedule")
    init.add_argument("directory", type=Path)
    init.add_argument("rows", nargs="*")
    reference = commands.add_parser("reference")
    reference.add_argument("profile", type=Path)
    select = commands.add_parser("cells")
    select.add_argument("row", choices=ROWS)
    snapshot = commands.add_parser("check-snapshot")
    snapshot.add_argument("directory", type=Path)
    snapshot.add_argument("row", choices=ROWS)
    snapshot.add_argument("rep", type=int)
    snapshot.add_argument("board", choices=("ask2", "vendor"))
    snapshot.add_argument("phase", choices=("before", "after"))
    complete = commands.add_parser("complete")
    complete.add_argument("directory", type=Path)
    record = commands.add_parser("record")
    record.add_argument("directory", type=Path)
    record.add_argument("vendor_run", type=Path)
    record.add_argument("row", choices=ROWS)
    record.add_argument("rep", type=int)
    args = parser.parse_args()
    if args.operation == "schedule":
        for row, rep, board in initialize(args.directory, args.rows):
            print(f"{row}\t{rep}\t{board}")
    elif args.operation == "reference":
        compare_reference(read_json(args.profile))
    elif args.operation == "cells":
        for column, cell, bidir in cells(args.row):
            print(f"{column}\t{cell}\t{bidir}")
    elif args.operation == "check-snapshot":
        verify_snapshot(args.directory, args.row, args.rep, args.board, args.phase)
    elif args.operation == "complete":
        verify_complete(args.directory)
    else:
        record_group(args.directory, args.vendor_run, args.row, args.rep)
        testrig_parity.battery_report(args.directory)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)

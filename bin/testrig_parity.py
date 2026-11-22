#!/usr/bin/env python3
"""Read-only generator checks and actual-traffic gates for paired benchmarks."""

import argparse
import csv
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def command(*args):
    return subprocess.run(args, text=True, capture_output=True, check=True).stdout.strip()


def collect_port(interface, namespace="", vlan=None):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", interface):
        raise ValueError("invalid interface")
    if namespace and not re.fullmatch(r"[a-zA-Z0-9_.-]+", namespace):
        raise ValueError("invalid namespace")
    prefix = ["ip", "netns", "exec", namespace] if namespace else []

    def run(*args):
        return command(*prefix, *args)

    def hardware(option):
        text = run("ethtool", option, interface)
        if "Current hardware settings:" in text:
            text = text.split("Current hardware settings:", 1)[1]
        else:
            text = "\n".join(text.splitlines()[1:])
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())

    def link_data(device):
        data = json.loads(run("ip", "-j", "addr", "show", "dev", device))[0]
        return {"mtu": data["mtu"], "addresses": sorted(
            f'{addr["local"]}/{addr["prefixlen"]}'
            for addr in data["addr_info"] if addr["scope"] == "global"
        )}

    rss = hardware("-x")
    if not re.search(r"^\d+:", rss, re.MULTILINE):
        raise ValueError(f"{interface}: missing RSS indirection table")
    qdiscs = json.loads(run("tc", "-j", "qdisc", "show", "dev", interface))
    qdiscs = [{key: value for key, value in item.items()
               if key in ("kind", "parent", "root", "options")} for item in qdiscs]
    port = link_data(interface)
    port.update(
        carrier=int(run("cat", f"/sys/class/net/{interface}/carrier")),
        rings=hardware("-g"), channels=hardware("-l"), coalescing=hardware("-c"),
        features=hardware("-k"), rss=rss,
        rss_fields={family: run("ethtool", "-n", interface, "rx-flow-hash", family).splitlines()[1:]
                    for family in ("tcp4", "tcp6")},
        rps=run("sh", "-c", f"cat /sys/class/net/{interface}/queues/rx-*/rps_cpus").splitlines(),
        qdiscs=sorted(qdiscs, key=lambda item: json.dumps(item, sort_keys=True)),
        link="\n".join(line.strip() for line in run("ethtool", interface).splitlines()
                       if re.match(r"\s*(Speed|Duplex|Link detected):", line)),
    )
    if vlan is not None:
        port["vlan"] = link_data(interface + f".{vlan}")
    return port


def snapshot(interface, namespace_interface, namespace, vlan):
    binary = shutil.which("iperf3")
    if binary is None:
        raise ValueError("iperf3 is missing")
    return {
        "kernel": os.uname().release,
        "iperf": command("iperf3", "--version"),
        "iperf_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        "ports": {"ask2": collect_port(interface, vlan=vlan),
                  "vendor": collect_port(namespace_interface, namespace, vlan=vlan)},
    }


def compare_ports(data, mtu):
    ports = data["ports"]
    one, two = ports["ask2"], ports["vendor"]
    for field in ("mtu", "carrier", "addresses", "rings", "channels", "features",
                  "coalescing", "rss", "rss_fields", "rps", "qdiscs", "link"):
        if field not in one or field not in two or one[field] != two[field]:
            raise ValueError(f"generator paths differ: {field}")
    if one.get("vlan") != two.get("vlan"):
        raise ValueError("generator VLAN conditions differ")
    if one["mtu"] != mtu:
        raise ValueError(f"mtu is {one['mtu']}, expected {mtu} on both paths")
    if "vlan" in one and one["vlan"]["mtu"] != mtu:
        raise ValueError(f"VLAN mtu is {one['vlan']['mtu']}, expected {mtu}")
    if one["carrier"] != 1:
        raise ValueError("carrier is not up on both paths")


def compare_snapshots(before, after, mtu):
    compare_ports(before, mtu)
    compare_ports(after, mtu)
    if before != after:
        raise ValueError("generator conditions changed since campaign preflight")


def battery_plan(rows, repetitions):
    routed = {
        "unicast": ("unicast-v{}", "unicast-v{}-rev"),
        "nat": ("nat-v{}", "nat-v{}-rev"),
        "vlan-vlan": ("vlan-v{}", "vlan-v{}-rev"),
        "vlan-port": ("vlanport-v{}", "portvlan-v{}"),
        "port-forward": ("portfwd-up-v{}", "portfwd-down-v{}"),
    }
    blocked = {
        "pppoe": "PPPoE endpoint/service adapters are not yet matched",
        "bridge": "matched bridge setup/teardown ownership is unresolved",
        **{row: "matched per-row jumbo MTU transition adapter is not yet integrated"
           for row in ("unicast-9k", "nat-9k", "vlan-9k", "vlan-port-9k", "bridge-9k")},
        "wireguard": "WGFP completion identity and post-teardown drain gate are unresolved",
        "wireguard6": "WGFP completion identity and post-teardown drain gate are unresolved",
        "nat+ppp+vlan": "matched PPPoE/VLAN setup and cleanup ownership are unresolved",
        "wg+nat+pp+vl": "WGFP drain and matched composite setup ownership are unresolved",
    }
    rows = list(rows) or [
        "unicast", "nat", "vlan-vlan", "vlan-port", "pppoe", "bridge",
        "unicast-9k", "nat-9k", "vlan-9k", "vlan-port-9k", "bridge-9k",
        "wireguard", "wireguard6", "port-forward", "nat+ppp+vlan", "wg+nat+pp+vl"]
    if len(set(rows)) != len(rows):
        raise ValueError("duplicate battery rows")
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("invalid battery repetitions")
    plan = {"platforms": {"ask2": "VyOS", "vendor": "OpenWrt"},
            "order": "row, repetition, DUT1, DUT2, column",
            "repetitions": repetitions, "rows": [], "trials": []}
    for row in rows:
        if row not in routed:
            if row not in blocked:
                raise ValueError(f"unknown battery row: {row}")
            plan["rows"].append({"name": row, "status": "blocked", "reason": blocked[row]})
            continue
        plan["rows"].append({"name": row, "status": "pending"})
        upload, download = routed[row]
        for rep in range(1, repetitions + 1):
            for board in ("ask2", "vendor"):
                for direction in ("up", "down", "bidir"):
                    for family in (4, 6):
                        down = direction == "down" or (row == "port-forward" and direction == "bidir")
                        cell = (download if down else upload).format(family)
                        plan["trials"].append({
                            "row": row, "rep": rep, "board": board,
                            "column": f"{direction}-v{family}", "cell": cell,
                            "bidir": int(direction == "bidir"),
                        })
    return plan


def battery_report(directory):
    directory = Path(directory)
    plan = json.loads((directory / "battery.json").read_text())
    columns = [f"{direction}-v{family}" for direction in ("up", "down", "bidir")
               for family in (4, 6)]
    tables = {board: [] for board in plan["platforms"]}
    results = []
    for row in plan["rows"]:
        values = {board: {} for board in tables}
        complete = row["status"] != "blocked"
        for column in columns:
            samples = {board: [] for board in tables}
            for rep in range(1, plan["repetitions"] + 1):
                path = directory / f"pair-{row['name']}-{column}-r{rep}.json"
                if path.exists():
                    pair = json.loads(path.read_text())
                    for measurement in pair["measurements"]:
                        samples[measurement["board"]].append(measurement["gbps"])
            for board, rates in samples.items():
                accepted = len(rates) == plan["repetitions"]
                complete &= accepted
                values[board][column] = {"n": len(rates), "rates": rates,
                                         "best": max(rates) if accepted else None}
        status = "blocked" if row["status"] == "blocked" else "complete" if complete else "pending"
        results.append({**row, "status": status, "measurements": values})
        for board in tables:
            cells = [values[board][column]["best"] for column in columns]
            tables[board].append(row["name"].ljust(17) + "".join(
                f"{value:8.2f}" if value is not None else f"{'-':>8}" for value in cells))
    lines = []
    for board, platform in plan["platforms"].items():
        lines += [f"{'DUT1' if board == 'ask2' else 'DUT2'} {platform} - Gbit/s, best of "
                  f"{plan['repetitions']}",
                  "Flow type        Upload          Download        Bidir",
                  "                       v4      v6      v4      v6      v4      v6",
                  *tables[board], ""]
    (directory / "matrix.json").write_text(json.dumps(results, indent=2) + "\n")
    (directory / "matrix.txt").write_text("\n".join(lines))
    return results


def compare_trials(one, two, duration, streams, cc, bidir):
    for label, trial in (("ask2", one), ("vendor", two)):
        if trial.get("error"):
            raise ValueError(f"{label}: {trial['error']}")
        start, end = trial["start"], trial["end"]
        test = start["test_start"]
        if (test.get("protocol") != "TCP" or test.get("duration") != duration or
                test.get("num_streams") != streams or test.get("bidir", 0) != bidir):
            raise ValueError(f"{label}: traffic does not match the common protocol")
        if (end.get("sender_tcp_congestion") != cc or
                end.get("receiver_tcp_congestion") != cc):
            raise ValueError(f"{label}: actual congestion control differs from {cc}")
        mss = start.get("tcp_mss_default")
        if type(mss) is not int or mss <= 0:
            raise ValueError(f"{label}: missing MSS")
        if len(start.get("connected", [])) != streams * (2 if bidir else 1):
            raise ValueError(f"{label}: incomplete connections")
    for field in ("version", "test_start", "tcp_mss_default"):
        if one["start"].get(field) != two["start"].get(field):
            raise ValueError(f"actual trial parameters differ: {field}")
    endpoints = [
        {(item["local_host"], item["remote_host"], item["remote_port"])
         for item in trial["start"]["connected"]}
        for trial in (one, two)
    ]
    if endpoints[0] != endpoints[1]:
        raise ValueError("actual source/destination endpoints differ")


def protocol():
    return {"protocol": "matched-iperf-v1", "duration": int(os.environ["DUR"]),
            "steady_from": int(os.environ["STEADY_FROM"]),
            "measure_to": int(os.environ["MEASURE_TO"]), "streams": int(os.environ["STREAMS"]),
            "repetitions": int(os.environ["REPS"]), "congestion": os.environ["PARITY_CC"],
            "mtu": int(os.environ.get("PARITY_MTU", "1500")),
            "interval": 1, "zerocopy": True, "extra_sampling": False,
            "rate_semantics": "client sender normally, client receiver with -R; bidir adds reverse receiver"}


def pair_report(csv_paths, json_paths, cell):
    from testrig_wg_validation import iperf_rate

    profile = protocol()
    data = [json.loads(Path(path).read_text()) for path in json_paths]
    bidir = int(os.environ.get("BIDIR", "1"))
    compare_trials(*data, profile["duration"], profile["streams"], profile["congestion"], bidir)
    # Most quick reverse cells swap the client host rather than using iperf -R.
    reverse = int(cell in {"wanfull-down-v4", "portfwd-up-v4", "portfwd-up-v6",
                          "wgwan-down-v4", "wgwan-down-v6"})
    family = 6 if "-v6" in cell else 4
    for trial in data:
        if trial["start"]["test_start"].get("reverse", 0) != reverse:
            raise ValueError("actual direction differs from requested cell")
        for connection in trial["start"]["connected"]:
            if any(ipaddress.ip_address(connection[field]).version != family
                   for field in ("local_host", "remote_host")):
                raise ValueError("actual address family differs from requested cell")
    if profile["mtu"] == 9000 and data[0]["start"]["tcp_mss_default"] < 8800:
        raise ValueError("jumbo comparison has no jumbo MSS")
    measurements = []
    for board, csv_path, json_path, trial in zip(("ask2", "vendor"), csv_paths, json_paths, data):
        with Path(csv_path).open(newline="") as source:
            rows = list(csv.DictReader(source))
        if len(rows) != 1:
            raise ValueError(f"{csv_path}: expected exactly one trial")
        row = rows[0]
        if row["verdict"].startswith(("FAIL", "SKIP")):
            raise ValueError(f"{csv_path}: failed or skipped trial")
        if row["cell"] != cell + ("-bidir" if bidir else ""):
            raise ValueError(f"{csv_path}: unexpected cell")
        value = float(row["gbps"])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{csv_path}: invalid rate")
        direction = "bidir" if bidir else (
            "down" if trial["start"]["test_start"].get("reverse") else "up")
        measured = iperf_rate(json_path, direction, profile["duration"], profile["steady_from"],
                              profile["measure_to"], profile["streams"])
        if abs(measured - value) > 0.000501:
            raise ValueError(f"{csv_path}: rate differs from bounded measurement")
        measurements.append({"board": board, "cell": row["cell"], "gbps": measured,
                             "verdict": row["verdict"], "json": str(json_path)})
    if measurements[0]["cell"] != measurements[1]["cell"]:
        raise ValueError("different cells in paired CSVs")
    return {"profile": profile, "measurements": measurements,
            "delta_ask2_minus_vendor": measurements[0]["gbps"] - measurements[1]["gbps"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    collect = commands.add_parser("snapshot")
    collect.add_argument("interface")
    collect.add_argument("namespace_interface")
    collect.add_argument("namespace")
    collect.add_argument("vlan", type=int, choices=(10, 20))
    compare = commands.add_parser("compare")
    compare.add_argument("paths", nargs=2)
    compare.add_argument("--mtu", type=int, default=1500)
    compare.add_argument("--baseline", nargs=2)
    schedule = commands.add_parser("schedule")
    schedule.add_argument("directory")
    schedule.add_argument("rows", nargs="*")
    report = commands.add_parser("battery-report")
    report.add_argument("directory")
    save = commands.add_parser("profile")
    save.add_argument("output")
    pair = commands.add_parser("pair")
    pair.add_argument("ask2_csv")
    pair.add_argument("ask2_json")
    pair.add_argument("vendor_csv")
    pair.add_argument("vendor_json")
    pair.add_argument("output")
    pair.add_argument("--cell", required=True)
    args = parser.parse_args()
    if args.operation == "snapshot":
        print(json.dumps(snapshot(args.interface, args.namespace_interface, args.namespace, args.vlan)))
    elif args.operation == "compare":
        for index, path in enumerate(args.paths):
            current = json.loads(Path(path).read_text())
            compare_ports(current, args.mtu)
            if args.baseline:
                compare_snapshots(json.loads(Path(args.baseline[index]).read_text()), current, args.mtu)
    elif args.operation == "schedule":
        plan = battery_plan(args.rows, int(os.environ["REPS"]))
        directory = Path(args.directory)
        with (directory / "battery.json").open("x") as output:
            json.dump(plan, output, indent=2)
            output.write("\n")
        for trial in plan["trials"]:
            print("\t".join(str(trial[key]) for key in ("row", "rep", "board", "column", "cell", "bidir")))
        for row in plan["rows"]:
            if row["status"] == "blocked":
                print(f"BLOCKED {row['name']}: {row['reason']}", file=sys.stderr)
    elif args.operation == "battery-report":
        battery_report(args.directory)
    else:
        if args.operation == "profile":
            result = protocol()
        else:
            result = pair_report([args.ask2_csv, args.vendor_csv],
                                 [args.ask2_json, args.vendor_json], args.cell)
        with Path(args.output).open("x") as output:
            json.dump(result, output, indent=2, sort_keys=True)
            output.write("\n")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: parity validation: {exc}", file=sys.stderr)
        sys.exit(1)

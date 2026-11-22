#!/usr/bin/env python3
"""Print only validated pairs from an in-progress row-paired battery."""

import json
import math
from pathlib import Path
import re
import sys
import time


COLUMNS = tuple(f"{direction}-v{family}" for direction in ("up", "down", "bidir")
                for family in (4, 6))
BOARDS = ("ask2", "vendor")


def read_pair(path):
    for attempt in range(3):
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            if attempt == 2:
                raise
            time.sleep(0.1)


def status(directory):
    plan = json.loads((directory / "battery.json").read_text())
    profile = json.loads((directory / "protocol.json").read_text())
    repetitions = plan["repetitions"]
    if repetitions != 3 or profile["repetitions"] != repetitions:
        raise ValueError("battery must use the recorded best-of-three profile")
    if plan["platforms"] != {"ask2": "VyOS", "vendor": "OpenWrt"}:
        raise ValueError("unexpected DUT platforms")
    scheduled = {(t["row"], t["column"], t["rep"], t["board"]): t
                 for t in plan["trials"]}
    results = {board: [] for board in BOARDS}
    blocked = []
    for row in plan["rows"]:
        name = row["name"]
        if not re.fullmatch(r"[a-z0-9+_-]+", name):
            raise ValueError("invalid row name")
        is_blocked = row["status"] == "blocked"
        if is_blocked:
            blocked.append(f"{name}: {row['reason']}")
        values = {board: [] for board in BOARDS}
        for column in COLUMNS:
            samples = {board: [] for board in BOARDS}
            verdicts = {board: [] for board in BOARDS}
            for rep in range(1, repetitions + 1):
                path = directory / f"pair-{name}-{column}-r{rep}.json"
                if not path.exists():
                    continue
                pair = read_pair(path)
                if is_blocked or pair["profile"] != profile:
                    raise ValueError(f"{path.name}: blocked row or mismatched profile")
                measurements = pair["measurements"]
                if len(measurements) != 2 or {m["board"] for m in measurements} != set(BOARDS):
                    raise ValueError(f"{path.name}: expected one result per DUT")
                for measurement in measurements:
                    board = measurement["board"]
                    trial = scheduled[(name, column, rep, board)]
                    expected = trial["cell"] + ("-bidir" if trial["bidir"] else "")
                    if measurement["cell"] != expected:
                        raise ValueError(f"{path.name}: unexpected cell")
                    rate = measurement["gbps"]
                    if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
                        raise ValueError(f"{path.name}: invalid rate")
                    verdict = measurement["verdict"]
                    if verdict.startswith(("FAIL", "SKIP")):
                        raise ValueError(f"{path.name}: failed or skipped trial")
                    samples[board].append(rate)
                    verdicts[board].append(verdict)
            for board in BOARDS:
                rates = samples[board]
                if not rates:
                    value = "B" if is_blocked else "-"
                else:
                    marker = "*" if any(v != "HW" for v in verdicts[board]) else ""
                    value = f"{max(rates):.2f}({len(rates)}/{repetitions}){marker}"
                values[board].append(value)
        for board in BOARDS:
            results[board].append(name.ljust(17) + "".join(f"{value:>13}" for value in values[board]))
    lines = ["Gbit/s: max so far (n/3); n < 3 is provisional. B = blocked; * = not all HW-estimated.", ""]
    for board, label in (("ask2", "DUT1 VyOS"), ("vendor", "DUT2 OpenWrt")):
        lines += [label, "Flow type             Upload                    Download                  Bidir",
                  "                         v4           v6           v4           v6           v4           v6",
                  *results[board], ""]
    if blocked:
        lines += ["Blocked:", *blocked]
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        raise ValueError("usage: testrig-paired-status.py RUN_DIR")
    print(status(Path(sys.argv[1])))


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: paired status: {error}", file=sys.stderr)
        sys.exit(1)

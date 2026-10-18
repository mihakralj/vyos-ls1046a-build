#!/usr/bin/env python3
"""Summarise the *.prof files written by `PROF=1 bin/testrig-wireguard.sh` (or wg-parity.sh).

Usage: bin/wg-prof-summary.py FILE.prof [FILE.prof ...]

Per file: busy share per CPU over the steady window, per-CPU softirq deltas (rates per second), and the
threads that used the most CPU (ticks over the window, as a percentage of one CPU, with the CPU each one
last ran on). Thread names are grouped by their trailing number (kworker/1:2 -> kworker/N:N) in the totals.
"""
import re
import sys


def parse(path):
    snaps = []
    cur = None
    mode = None
    for line in open(path, errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("== "):
            cur = {"t": int(line[3:]), "cpu": {}, "soft": {}, "thr": {}}
            snaps.append(cur)
            mode = "cpu"
        elif line == "-- softirqs":
            mode = "soft"
        elif line == "-- threads":
            mode = "thr"
        elif line == "-- end":
            mode = None
        elif cur is None or not line:
            continue
        elif mode == "cpu" and line.startswith("cpu"):
            f = line.split()
            v = list(map(int, f[1:11]))
            cur["cpu"][f[0]] = (sum(v), v[3] + v[4])
        elif mode == "soft":
            f = line.split()
            if f[0].endswith(":"):
                cur["soft"][f[0][:-1]] = list(map(int, f[1:]))
        elif mode == "thr":
            m = re.match(r"(\d+) \((.*)\) (\S) (.*)", line)
            if not m:
                continue
            tid, comm, rest = int(m.group(1)), m.group(2), m.group(4).split()
            # rest[0] is field 4 (ppid); utime is field 14, stime 15, processor 39
            try:
                ticks = int(rest[10]) + int(rest[11])
                cpu = int(rest[35])
            except (IndexError, ValueError):
                continue
            cur["thr"][tid] = (comm, ticks, cpu)
    return snaps


def main():
    for path in sys.argv[1:]:
        s = parse(path)
        if len(s) < 2:
            print(f"{path}: fewer than two snapshots")
            continue
        a, b = s[0], s[-1]
        dt = max(b["t"] - a["t"], 1)
        hz = 100
        print(f"== {path}  window {dt}s")
        cpus = sorted(k for k in b["cpu"] if k != "cpu")
        row = []
        for k in cpus:
            tot = b["cpu"][k][0] - a["cpu"][k][0]
            idle = b["cpu"][k][1] - a["cpu"][k][1]
            row.append(f"{k} {100 * (tot - idle) / tot:5.1f}%" if tot else f"{k} n/a")
        print("  busy:    " + "  ".join(row))
        for name in ("NET_RX", "NET_TX", "TASKLET", "SCHED", "TIMER", "RCU", "HRTIMER", "IRQ_POLL", "BLOCK"):
            if name in b["soft"] and name in a["soft"]:
                d = [y - x for x, y in zip(a["soft"][name], b["soft"][name])]
                if sum(d):
                    print(f"  {name:8} " + "  ".join(f"cpu{i} {v / dt:8.0f}/s" for i, v in enumerate(d)))
        rows = []
        grp = {}
        for tid, (comm, t1, c1) in b["thr"].items():
            t0 = a["thr"].get(tid, (comm, 0, c1))[1]
            d = t1 - t0
            if d <= 0:
                continue
            pct = 100.0 * d / hz / dt
            rows.append((pct, tid, comm, c1))
            g = re.sub(r"\d+", "N", comm)
            grp[g] = grp.get(g, 0.0) + pct
        rows.sort(reverse=True)
        print("  top threads (% of one CPU, last CPU):")
        for pct, tid, comm, c1 in rows[:12]:
            print(f"    {pct:6.1f}%  cpu{c1}  {comm} [{tid}]")
        print("  by thread-name group: " + ", ".join(f"{g} {v:.0f}%" for g, v in sorted(grp.items(), key=lambda x: -x[1])[:8]))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""ask2-ladder-sampler.py: one line per second on the DUT until killed (run as root; copied by the testrig runners).

Columns: epoch phase offloaded_flows cpu_busy_pct running blocked kpath_pps ct_entries ct_inserts_per_s
  phase              contents of /tmp/ladder-phase ("-" when absent)
  offloaded_flows    ASK get-info num-flows / 2 (directions -> bidirectional flows), one persistent YNL session
  cpu_busy_pct       all-CPU busy share of the last second (/proc/stat)
  running            runnable tasks (/proc/loadavg field 4)
  blocked            tasks in D state (/proc/*/stat)
  kpath_pps          packets the kernel network stack processed (/proc/net/softnet_stat), i.e. frames not in silicon
  ct_entries         nf_conntrack_count
  ct_inserts_per_s   conntrack insertions per second (/proc/net/stat/nf_conntrack "insert", summed over CPUs)
Forks nothing per sample: the per-second `ynl` CLI it replaced cost ~15 % of DUT CPU.
"""
import os, sys, time
sys.path.insert(0, "/usr/share/ynl")
from pyynl.lib import YnlFamily

y = YnlFamily("/usr/share/ynl/specs/ask.yaml", "")


def cpu():
    f = [int(x) for x in open("/proc/stat").readline().split()[1:8]]   # user nice system idle iowait irq softirq
    busy = f[0] + f[1] + f[2] + f[5] + f[6]
    return busy, busy + f[3] + f[4]


def kpath():
    return sum(int(l.split()[0], 16) for l in open("/proc/net/softnet_stat"))


def ct_inserts():
    rows = open("/proc/net/stat/nf_conntrack").read().split("\n")
    col = rows[0].split().index("insert")
    return sum(int(r.split()[col], 16) for r in rows[1:] if r.strip())


def blocked():
    n = 0
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                with open(f"/proc/{p}/stat") as fh:
                    n += fh.read().rsplit(")", 1)[1].split()[0] == "D"
            except OSError:
                pass
    return n


pb, pt = cpu(); pk = kpath(); pi = ct_inserts()
while True:
    time.sleep(1)
    b, t = cpu(); k = kpath(); i = ct_inserts()
    try:
        flows = y.do("get-info", {})["info"]["num-flows"] // 2
    except Exception:
        flows = 0
    try:
        phase = open("/tmp/ladder-phase").read().strip() or "-"
    except OSError:
        phase = "-"
    running = open("/proc/loadavg").read().split()[3].split("/")[0]
    ct = open("/proc/sys/net/netfilter/nf_conntrack_count").read().strip()
    print(int(time.time()), phase, flows, 100 * (b - pb) // (t - pt + 1), running, blocked(), k - pk, ct, i - pi, flush=True)
    pb, pt, pk, pi = b, t, k, i

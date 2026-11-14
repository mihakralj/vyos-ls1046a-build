#!/usr/bin/env python3
"""fman-wedgecap.py [outdir] — FMan-global wedge watchdog for experiments (run as root on the DUT).

Install next to bin/fmstall.py on the DUT, e.g. /config/wedgecap/ (survives a power cycle), and run for the
duration of an experiment: sudo systemd-run --unit=wedgecap /usr/bin/python3 /config/wedgecap/fman-wedgecap.py


Detector, read-only: once a second read the FPM per-TNUM task status table fmfp_ts[0..127]
(FMan1 CCSR 0x1A00000 + FPM 0xC3000 + 0x400, big-endian). On a healthy FMan a task is idle
(0 / 3) or finishes in microseconds; the parked FM_CTL task (ts >> 24 == 0x81) and the normal
in-flight/waiting state 0x0088xxxx are excluded.
A wedge is >= MIN_STUCK tasks whose non-idle ts word is unchanged for CONFIRM consecutive samples.

On a wedge, once per boot: fmstall.py (3 x 1 s: task table + DRD words, FPM/QMI/DMA/BMI regs),
dmesg, FMan debugfs (fe_*), fsl_dpaa_fman params, dpaa_wgfp stats, links, interrupts, into
outdir/<UTC stamp>/, then sync + EXT4_IOC_CHECKPOINT so the data survives the power cycle, and
a kmsg line. Only fmstall.py writes FMFP_DRA (the debug data-RAM pointer, as the vendor SDK's
fm_dump_tnum_dbg() does); the detector itself never writes.
"""
import fcntl, mmap, os, struct, subprocess, sys, time

OUT = sys.argv[1] if len(sys.argv) > 1 else "/config/wedgecap"
HERE = os.path.dirname(os.path.abspath(__file__))
TS_OFF, NT, MIN_STUCK, CONFIRM = 0xC3000 + 0x400, 128, 6, 3

fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
mm = mmap.mmap(fd, 0x100000, mmap.MAP_SHARED, mmap.PROT_READ, offset=0x1A00000)
def ts():
    return struct.unpack(">128I", mm[TS_OFF:TS_OFF + 4 * NT])

def sh(cmd, path):
    with open(path, "w") as f:
        subprocess.run(["sh", "-c", cmd], stdout=f, stderr=subprocess.STDOUT, timeout=60)

def capture(stuck, first):
    d = os.path.join(OUT, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "detector.txt"), "w") as f:
        f.write(f"onset_detected_monotonic={first:.1f} uptime={open('/proc/uptime').read().split()[0]}\n")
        f.write("stuck " + " ".join(f"{t}:0x{v:08x}" for t, v in stuck) + "\n")
    sh(f"python3 {HERE}/fmstall.py 3 1", os.path.join(d, "fmstall.txt"))
    sh("dmesg", os.path.join(d, "dmesg.txt"))
    sh("for f in fe_extc fe_flow fe_pool fe_buffer fe_port fe_ehash_stats muram_budget; do "
       "echo \"== $f\"; cat /sys/kernel/debug/fman_pcd/0/$f 2>&1 | head -200; done", os.path.join(d, "fman-debugfs.txt"))
    sh("grep -H . /sys/module/fsl_dpaa_fman/parameters/* 2>/dev/null", os.path.join(d, "fman-params.txt"))
    sh("cat /sys/kernel/debug/dpaa_wgfp/stats 2>&1; grep -H . /sys/module/wireguard/parameters/* 2>&1",
       os.path.join(d, "wgfp.txt"))
    sh("ip -s -s link; cat /proc/interrupts; cat /proc/softirqs", os.path.join(d, "links-irqs.txt"))
    os.sync()
    # EXT4_IOC_CHECKPOINT is per filesystem, and /config is an overlay (ENOTTY there): issue it on the
    # ext4 persistence root that backs /config and /boot.
    try:
        cfd = os.open("/usr/lib/live/mount/persistence", os.O_RDONLY)
        fcntl.ioctl(cfd, 0x4004662B, struct.pack("I", 0))
        os.close(cfd)
    except OSError as e:
        print("wedgecap: checkpoint failed:", e, flush=True)
    with open("/dev/kmsg", "w") as k:
        k.write(f"wedgecap: FMan wedge, {len(stuck)} tasks frozen, capture in {d}\n")
    return d

QMI_ETFC = 0x80400 + 0x28   # QMI enqueue total frame counter: frozen in the 2026-11-06 wedge
def etfc():
    return struct.unpack(">I", mm[QMI_ETFC:QMI_ETFC + 4])[0]

prev, pe, run, first = None, None, 0, None
print(f"wedgecap: watching QMI etfc + fmfp_ts (>= {MIN_STUCK} frozen busy tasks, {CONFIRM} s), output {OUT}", flush=True)
while True:
    cur, ce = ts(), etfc()
    if prev is not None:
        # Busy-state values recur under healthy load (0x00d8/0x0098 seen unchanged between 1 s samples
        # at 8 Gbit/s), so a frozen task word alone is not a wedge: also require the QMI enqueue
        # counter to stand still (it advances even at idle from eth0 management traffic).
        # 0x0088xxxx = normal in-flight/waiting; 0x81xxxxxx = parked FM_CTL task.
        stuck = [(t, v) for t, v in enumerate(cur)
                 if v not in (0, 3) and v >> 24 != 0x81 and v >> 16 != 0x0088 and v == prev[t]]
        if ce == pe and len(stuck) >= MIN_STUCK:
            run += 1
            first = first or time.monotonic()
            if run >= CONFIRM:
                print("wedgecap: captured", capture(stuck, first), flush=True)
                break
        else:
            run, first = 0, None
    prev, pe = cur, ce
    time.sleep(1)

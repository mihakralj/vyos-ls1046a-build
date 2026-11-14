#!/usr/bin/env python3
# fmstall.py [samples] [interval_s] — FMan1 stall snapshot, one name=value per line.
# What a wedged task looks like: FPM per-port status (STL), the per-TNUM task
# table (fmfp_ts[0..127] + its data-RAM words DRD0-3; DRD0[31:24] = hwport of
# the task's last owner), FMFP_EXTC, FPM/QMI/BMI
# events, DMA status + reject counters, BMI RX/TX port status/counters.
# Read-only except FMFP_DRA, the debug data-RAM pointer the vendor SDK's
# fm_dump_tnum_dbg() (lnxwrp_sysfs_fm.c) writes the same way (tnum << 24).
# Run >= 2 samples: a stuck task is one whose ts/DRD words do not change.
# Healthy baseline (.185, 2026-10-07): ts 0x0/0x3 idle, 0x0088000N in flight,
# task 4 always 0x810000xx (parked FM_CTL task, low bits move under load; iter-33).
import mmap, os, sys, time
BASE, LEN = 0x1A00000, 0x100000
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
mm = mmap.mmap(fd, LEN, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BASE)
u32 = memoryview(mm).cast("I")
bs = lambda v: int.from_bytes(v.to_bytes(4, "little"), "big")
r = lambda o: bs(u32[o // 4])
def w(o, v): u32[o // 4] = bs(v)

FPM, QMI, BMI, DMA = 0xC3000, 0x80400, 0x80000, 0xC2000
FPM_REGS = dict(tnc=0x00, prc=0x04, mxd=0x0c, dist1=0x10, dist2=0x14, epi=0x18, rie=0x1c,
                fcev0=0x20, fcev1=0x24, fcev2=0x28, fcev3=0x2c, cee0=0x40, cee1=0x44,
                cee2=0x48, cee3=0x4c, extc=0x74, ext1=0x78, ext2=0x7c, cld=0xd0, npi=0xd4,
                exte=0xd8, ee=0xdc, cev0=0xe0, cev1=0xe4, cev2=0xe8, cev3=0xec, decceh=0x22c)
QMI_REGS = dict(gc=0x00, eie=0x08, ie=0x14, gs=0x20, ts=0x24, etfc=0x28, dtfc=0x2c,
                dfdc=0x40, dfcc=0x44, dffc=0x48, dcc=0x4c, difdcc=0x74, dtc=0x80, efddd=0x84)
DMA_REGS = dict(sr=0x00, tah=0x14, tal=0x18, tcid=0x1c, wcr=0x28, ccqdr=0x30, ccqvr1=0x34,
                ccqvr2=0x38, cqvr3=0x3c, cqvr4=0x40, cqvr5=0x44, sefrc=0x48, sqfrc=0x4c,
                ssrc=0x50, dcr=0x54)
BMI_REGS = dict(init=0x00, ievr=0x20, ier=0x24)
RX = {"eth3": 0x10, "eth4": 0x11}; TX = {"eth3": 0x30, "eth4": 0x31}
RXP = dict(rcfg=0x000, rst=0x004, rfne=0x020, rfpne=0x028, rccb=0x034, rfqid=0x060,
           rstc=0x200, rfrc=0x204, rfbc=0x208, rlfc=0x20c, rffc=0x210, rfdc=0x214,
           rfldec=0x218, rodc=0x21c, rbdc=0x220, rpec=0x224)
TXP = dict(tcfg=0x000, tst=0x004, tfrc=0x204, tfdc=0x208, tfledc=0x20c, tfufdc=0x210, tbdc=0x214)

seen = {}
def sample(k):
    t = time.monotonic()
    for n, o in FPM_REGS.items(): print(f"s{k}.fpm.{n}=0x{r(FPM + o):08x}")
    for p in range(50):
        v = r(FPM + 0x100 + 4 * p)
        if v: print(f"s{k}.fpm.ps[0x{p:02x}]=0x{v:08x}{' STL' if v & 0x00800000 else ''}")
    busy = 0
    for tn in range(128):
        ts = r(FPM + 0x400 + 4 * tn)
        w(FPM + 0xc0, tn << 24)        # FMFP_DRA, as fm_dump_tnum_dbg()
        drd = [r(FPM + 0x80 + 4 * i) for i in range(4)]
        if ts or any(drd):
            busy += 1
            print(f"s{k}.task[{tn:3d}] ts=0x{ts:08x} drd=" + " ".join(f"0x{d:08x}" for d in drd)
                  + f" port=0x{drd[0] >> 24:02x}")
        seen.setdefault(tn, []).append((ts, *drd))
    print(f"s{k}.tasks_nonzero={busy}")
    for n, o in QMI_REGS.items(): print(f"s{k}.qmi.{n}=0x{r(QMI + o):08x}")
    for n, o in DMA_REGS.items(): print(f"s{k}.dma.{n}=0x{r(DMA + o):08x}")
    for n, o in BMI_REGS.items(): print(f"s{k}.bmi.{n}=0x{r(BMI + o):08x}")
    for e, p in RX.items():
        for n, o in RXP.items(): print(f"s{k}.{e}.rx.{n}=0x{r(BMI + p * 0x1000 + o):08x}")
    for e, p in TX.items():
        for n, o in TXP.items(): print(f"s{k}.{e}.tx.{n}=0x{r(BMI + p * 0x1000 + o):08x}")
    print(f"s{k}.read_ms={(time.monotonic() - t) * 1000:.1f}")

n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
gap = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
print(f"time={time.strftime('%Y-%m-%d %H:%M:%S')}")
for k in range(n):
    if k: time.sleep(gap)
    sample(k)
if n > 1:  # busy (not idle 0/3) and byte-identical in every sample
    stuck = [tn for tn, v in seen.items() if len(set(v)) == 1 and v[0][0] not in (0, 3)]
    for tn in stuck:
        ts, *drd = seen[tn][0]
        print(f"STUCK task[{tn}] ts=0x{ts:08x} port=0x{drd[0] >> 24:02x} drd=" + " ".join(f"0x{d:08x}" for d in drd))
    print(f"stuck_count={len(stuck)} (task 4 0x810000xx = normal parked FM_CTL task)")

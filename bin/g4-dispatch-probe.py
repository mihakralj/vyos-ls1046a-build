#!/usr/bin/env python3
"""G4 multi-table dispatch probe (plans/ASK2-MASTER-PLAN.md §3.1 G4).

Runs on the DUT as root. Reads and writes FMan1 KeyGen schemes, KeyGen port
scheme partitions, the per-port parser soft-sequence attachments and the
parser code RAM through /dev/mem, so a second KeyGen scheme can be steered to
by a soft-parser routine (`$nia = KG | DIRECT | scheme`) on one port while the
port's production ASK2 scheme keeps running. Lab tool: never run it on eth0,
never while ask.ko is engaging or disengaging a port (the KeyGen indirect
window is shared with the kernel), and cold boot after an experiment.

Every write is read back. Writes that replace state save the previous value
in a state file (default /tmp/g4-probe.json) so `restore` can put it back.

Commands
  dump [--ports 0x10,0x11]       KG globals, enabled schemes, port partitions,
                                 BMI RFPNE/RCCB, HXS ssa/lcv, RPIMAC, used code RAM
  scheme ID                      one scheme, all words
  scheme-set ID f=v [f=v ...]    read-modify-write scheme fields (mode, ekfc, ekdv,
                                 bmch, bmcl, fqb, hc, ppc, dv0, dv1, ccbs, mv, om,
                                 vsp, gec0..gec7); spc is cleared
  scheme-clear ID                write an all-zero (disabled) scheme
  bind HWPORT ID | unbind HWPORT ID   set/clear the scheme's bit in the port partition
  prs-load BYTEOFF W0 [W1 ...]   write code words with the RM PEN 1->0->1 rule
  ssa-set HWPORT SLOT VALUE      write pmda[SLOT].ssa of a port
  muram OFF [N]                  read N words of MURAM at OFF
  muram-set OFF W0 [W1 ...]      write MURAM words; only inside an allocation the
                                 experiment owns (rows 1..15 of a port's 256-byte
                                 FE scaffold at FMBM_RCCB are unused by ASK2)
  restore                        undo every saved change, newest first

Result 2026-11-09 (.106, image 2026.11.08-2238, cold boot): a soft-parser
routine that writes `$nia = 0x480300 | scheme` sends a protocol class to its
own KeyGen scheme with no LCV/match-vector change; with schemes at CCOBASE 1
and 2 and nodes at RCCB+16/+32, TCP (scheme 3, row 0), UDP (row 1, HIT) and
ESP (row 2, 38-byte table, MISS to the PCD FQ) ran concurrently on eth3, and
`restore` left `pcd-snapshot diff` clean. See plans/ASK2-MASTER-PLAN.md G4.
"""
import json
import mmap
import os
import struct
import sys
import time

FMAN = 0x01A00000
KG = FMAN + 0xC1000
PRS = FMAN + 0xC7000
RPIMAC = 0x844
PEN, PSTAT = 0x1, 0x100
AR = 0x1FC
GO, READ, ERR = 0x80000000, 0x40000000, 0x20000000
SEL_PORT, WSEL_SP, WSEL_CNT = 0x02000000, 0x00008000, 0x00008000
STATE = os.environ.get("G4_STATE", "/tmp/g4-probe.json")

# shadow window word index -> name (struct fman_kg_scheme_regs at KG+0x100)
FIELDS = ["mode", "ekfc", "ekdv", "bmch", "bmcl", "fqb", "hc", "ppc"] + \
         [f"gec{i}" for i in range(8)] + ["spc", "dv0", "dv1", "ccbs", "mv", "om", "vsp"]


class Mem:
    def __init__(self, base, size):
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        page = base & ~0xFFF
        self.off = base - page
        self.m = mmap.mmap(self.fd, size + self.off, mmap.MAP_SHARED,
                           mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
        self.mv = memoryview(self.m).cast("I")

    def r(self, o):
        # native 32-bit load (sliced copies bus-fault on some MMIO offsets)
        return struct.unpack(">I", struct.pack("<I", self.mv[(self.off + o) // 4]))[0]

    def w(self, o, v):
        self.mv[(self.off + o) // 4] = struct.unpack("<I", struct.pack(">I", v & 0xFFFFFFFF))[0]


kg = Mem(KG, 0x1000)
prs = Mem(PRS, 0x1000)


def bmi(hwport):
    return Mem(FMAN + 0x80000 + hwport * 0x1000, 0x1000)


def ar(val):
    kg.w(AR, val)
    for _ in range(100000):
        v = kg.r(AR)
        if not v & GO:
            break
    else:
        sys.exit("KeyGen AR timed out")
    if v & ERR:
        sys.exit(f"KeyGen AR error 0x{v:08x}")


def scheme_read(sid):
    ar(GO | READ | (sid << 16))
    return {f: kg.r(0x100 + 4 * i) for i, f in enumerate(FIELDS)}


def scheme_write(sid, s):
    for i, f in enumerate(FIELDS):
        kg.w(0x100 + 4 * i, s[f])
    ar(GO | (sid << 16) | WSEL_CNT)
    back = scheme_read(sid)
    bad = [f for f in FIELDS if f != "spc" and back[f] != s[f]]
    if bad:
        sys.exit(f"scheme {sid} readback mismatch: {bad}")
    return back


def sp_read(hwport):
    ar(GO | READ | SEL_PORT | hwport | WSEL_SP)
    return kg.r(0x100)


def sp_write(hwport, val):
    ar(GO | READ | SEL_PORT | hwport | WSEL_SP)
    kg.w(0x100, val)
    ar(GO | SEL_PORT | hwport | WSEL_SP)
    if sp_read(hwport) != val:
        sys.exit(f"port 0x{hwport:x} partition readback mismatch")


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def push_state(entry):
    st = load_state()
    st.append(entry)
    with open(STATE, "w") as f:
        json.dump(st, f)


def prs_write(off, words):
    saved = prs.r(RPIMAC) & ~PSTAT
    if not saved & PEN:
        sys.exit(f"parser already disabled (RPIMAC 0x{saved:08x}); refusing")
    prev = []
    prs.w(RPIMAC, saved & ~PEN)
    try:
        for _ in range(100000):
            if not prs.r(RPIMAC) & PSTAT:
                break
        else:
            raise RuntimeError("parser did not go idle")
        prev = [prs.r(off + 4 * i) for i in range(len(words))]
        for i, wd in enumerate(words):
            prs.w(off + 4 * i, wd)
        if [prs.r(off + 4 * i) for i in range(len(words))] != words:
            for i, wd in enumerate(prev):
                prs.w(off + 4 * i, wd)
            raise RuntimeError("code readback mismatch, previous words restored")
    finally:
        prs.w(RPIMAC, saved)
    if prs.r(RPIMAC) & PEN != saved & PEN:
        sys.exit("RPIMAC PEN not restored")
    return prev


def fmt(s):
    return " ".join(f"{f}={s[f]:08x}" for f in FIELDS)


def dump(ports):
    print(f"KG gcr={kg.r(0):08x} eer={kg.r(0xC):08x} seer={kg.r(0x14):08x} gsr={kg.r(0x20):08x} tpc={kg.r(0x28):08x}")
    for sid in range(32):
        s = scheme_read(sid)
        if s["mode"] or s["spc"]:
            print(f"scheme {sid:2d}: mode={s['mode']:08x} ekfc={s['ekfc']:08x} mv={s['mv']:08x} "
                  f"fqb={s['fqb']:08x} hc={s['hc']:08x} ccbs={s['ccbs']:08x} spc={s['spc']} "
                  f"gec={','.join(f'{s[f]:x}' for f in FIELDS[8:16])}")
    for p in ports:
        b = bmi(p)
        sp = sp_read(p)
        ids = [31 - i for i in range(32) if sp >> i & 1]
        print(f"port 0x{p:02x}: SP={sp:08x} schemes={sorted(ids)} rfne={b.r(0x20):08x} "
              f"rfpne={b.r(0x28):08x} rccb={b.r(0x34):08x} rfqid={b.r(0x60):08x} rfene={b.r(0x70):08x}")
        print("  ssa/lcv: " + " ".join(f"{i}:{b.r(0x800 + 8 * i):x}/{b.r(0x804 + 8 * i):x}" for i in range(16)))
    print(f"RPIMAC={prs.r(RPIMAC):08x}")
    used = [(o, prs.r(o)) for o in range(0, 0x800, 4) if prs.r(o)]
    print("code RAM non-zero words: " + " ".join(f"{o:03x}:{v:08x}" for o, v in used))


def num(x):
    return int(x, 0)


def main(a):
    if not a:
        sys.exit(__doc__)
    cmd = a[0]
    if cmd == "dump":
        ports = [0x10, 0x11]
        if len(a) > 2 and a[1] == "--ports":
            ports = [num(x) for x in a[2].split(",")]
        dump(ports)
    elif cmd == "scheme":
        print(fmt(scheme_read(num(a[1]))))
    elif cmd == "scheme-set":
        sid = num(a[1])
        old = scheme_read(sid)
        new = dict(old, spc=0)
        for kv in a[2:]:
            k, v = kv.split("=")
            if k not in FIELDS or k == "spc":
                sys.exit(f"unknown field {k}")
            new[k] = num(v)
        push_state({"t": "scheme", "id": sid, "old": old})
        print(fmt(scheme_write(sid, new)))
    elif cmd == "scheme-clear":
        sid = num(a[1])
        old = scheme_read(sid)
        push_state({"t": "scheme", "id": sid, "old": old})
        print(fmt(scheme_write(sid, {f: 0 for f in FIELDS})))
    elif cmd in ("bind", "unbind"):
        p, sid = num(a[1]), num(a[2])
        old = sp_read(p)
        bit = 1 << (31 - sid)
        new = old | bit if cmd == "bind" else old & ~bit
        push_state({"t": "sp", "port": p, "old": old})
        sp_write(p, new)
        print(f"port 0x{p:02x} SP {old:08x} -> {new:08x}")
    elif cmd == "prs-load":
        off = num(a[1])
        words = [num(x) for x in a[2:]]
        if off < 0 or off + 4 * len(words) > 0x800 or off % 4:
            sys.exit("offset outside the 2 KiB code RAM")
        prev = prs_write(off, words)
        push_state({"t": "prs", "off": off, "old": prev})
        print(f"wrote {len(words)} word(s) at 0x{off:03x}; previous {' '.join(f'{x:08x}' for x in prev)}")
    elif cmd == "ssa-set":
        p, slot, val = num(a[1]), num(a[2]), num(a[3])
        b = bmi(p)
        o = 0x800 + 8 * slot
        old = b.r(o)
        push_state({"t": "ssa", "port": p, "slot": slot, "old": old})
        b.w(o, val)
        if b.r(o) != val:
            b.w(o, old)
            sys.exit("ssa readback mismatch, restored")
        print(f"port 0x{p:02x} pmda[{slot}].ssa {old:08x} -> {val:08x}")
    elif cmd == "muram":
        off, n = num(a[1]), num(a[2]) if len(a) > 2 else 4
        mm = Mem(FMAN + off, 4 * n)
        print(f"{off:05x}: " + " ".join(f"{mm.r(4 * i):08x}" for i in range(n)))
    elif cmd == "muram-set":
        # Only for offsets inside an allocation the experiment owns (e.g. a
        # port's 256-byte FE scaffold at RCCB, rows 1..15 unused by ASK2).
        off = num(a[1])
        words = [num(x) for x in a[2:]]
        if off % 4 or off + 4 * len(words) > 0x60000:
            sys.exit("offset outside MURAM")
        mm = Mem(FMAN + off, 4 * len(words))
        old = [mm.r(4 * i) for i in range(len(words))]
        push_state({"t": "muram", "off": off, "old": old})
        for i, wd in enumerate(words):
            mm.w(4 * i, wd)
        if [mm.r(4 * i) for i in range(len(words))] != words:
            for i, wd in enumerate(old):
                mm.w(4 * i, wd)
            sys.exit("MURAM readback mismatch, restored")
        print(f"{off:05x}: {' '.join(f'{x:08x}' for x in old)} -> {' '.join(f'{x:08x}' for x in words)}")
    elif cmd == "restore":
        st = load_state()
        for e in reversed(st):
            if e["t"] == "scheme":
                old = dict(e["old"], spc=0)
                scheme_write(e["id"], old)
                print(f"scheme {e['id']} restored")
            elif e["t"] == "sp":
                sp_write(e["port"], e["old"])
                print(f"port 0x{e['port']:02x} SP restored {e['old']:08x}")
            elif e["t"] == "prs":
                if e["old"]:
                    prs_write(e["off"], e["old"])
                print(f"code at 0x{e['off']:03x} restored")
            elif e["t"] == "ssa":
                b = bmi(e["port"])
                b.w(0x800 + 8 * e["slot"], e["old"])
                print(f"port 0x{e['port']:02x} pmda[{e['slot']}].ssa restored {e['old']:08x}")
            elif e["t"] == "muram":
                mm = Mem(FMAN + e["off"], 4 * len(e["old"]))
                for i, wd in enumerate(e["old"]):
                    mm.w(4 * i, wd)
                print(f"MURAM {e['off']:05x} restored")
        os.rename(STATE, STATE + ".done")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

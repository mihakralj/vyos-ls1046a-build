# -*- coding: utf-8 -*-
# FmanPrefixCensus.py - count ALL 16-bit prefixes in the image grouped by
# high byte, to identify every branch-family prefix (0xaX/0xbX) missing from
# SLEIGH, and dump the distinct low-byte patterns for each branch family.
from ghidra.app.cmd.disassemble import DisassembleCommand
import ghidra

fm = currentProgram
mem = fm.getMemory()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12852

def A(b):
    return space.getAddress(b)

# prefix16 = top 16 bits of the word
prefixes = {}
for i in range(NWORDS):
    raw = mem.getInt(A(i * 4)) & 0xffffffff
    p = (raw >> 16) & 0xffff
    prefixes.setdefault(p, []).append(i)

print("=== distinct prefixes: count ===")
for p in sorted(prefixes):
    print("  0x%04x: %4d" % (p, len(prefixes[p])))

print("\n=== all 0xaX and 0xbX prefixes (branch families) with sample sites ===")
for p in sorted(prefixes):
    if (p & 0xf000) == 0xa000 or (p & 0xf000) == 0xb000:
        print("  0x%04x: %4d sites, e.g. w%s" % (p, len(prefixes[p]), ",".join(str(x) for x in prefixes[p][:6])))

print("\n=== low-byte patterns within each 0xbCXX / 0xb8XX / 0xb0XX / 0xb4XX / 0xb6XX / 0xb2XX family ===")
for hi in (0xbc00, 0xb800, 0xb000, 0xb400, 0xb600, 0xb200):
    lows = {}
    for p in prefixes:
        if p & 0xff00 == hi:
            lows[p & 0xff] = len(prefixes[p])
    if lows:
        print("  %04x: %s" % (hi, " ".join("%02x:%d" % (k, v) for k, v in sorted(lows.items()))))

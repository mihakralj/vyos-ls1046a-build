# -*- coding: utf-8 -*-
# FmanPoolBase.py - trace how the pool-management routine (w12667..w12850)
# computes the base register for its [0x54]/[0x58] accesses: does it read
# FMBM_RGPR (port param-page pointer) or derive the base some other way?
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.util.task import ConsoleTaskMonitor

fm = currentProgram
listing = fm.getListing()
mem = fm.getMemory()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12851
def A(b): return space.getAddress(b)

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

# Dump the full disassembly of w12660..w12850 (the pool routine + a bit before)
print("=" * 78)
print("DISASSEMBLY w12660..w12850 (pool-management routine)")
print("=" * 78)
for i in range(12660, 12851):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is None:
        # raw word fallback
        b = mem.getByte(a); b2 = mem.getByte(a.add(1)); b3 = mem.getByte(a.add(2)); b4 = mem.getByte(a.add(3))
        w = (b << 24) | (b2 << 16) | (b3 << 8) | b4
        print("w%04d %s  (raw 0x%08x)" % (i, a, w))
        continue
    print("w%04d %s  %s" % (i, a, ins))

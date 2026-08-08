# -*- coding: utf-8 -*-
# FmanRegTrace.py - precisely decode regfld (bits[20:16], the same
# convention validated for every modeled opcode this session) for EVERY
# instruction in a window, including unmodeled "unk" ones, to trace which
# registers get touched. Looking specifically for where r1 (set to 0x0 on
# one path into w12133) or r8 (set to a 0x1a-derived value on the other
# path) get referenced after the jump - that's the "which outcome" carrier.
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

def regfld_of(raw):
    byte1 = (raw >> 16) & 0xff
    return byte1 & 0x1f

START, END = 12133, 12260
print("=" * 78)
print("REGISTER TRACE w%d-w%d (decoding regfld for every instruction)" % (START, END - 1))
print("=" * 78)
r1_hits, r8_hits = [], []
for i in range(START, END):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is None:
        continue
    raw = mem.getInt(a) & 0xffffffff
    rf = regfld_of(raw)
    marker = ""
    if rf == 1:
        marker = "  <<<< touches r1"
        r1_hits.append(i)
    elif rf == 8:
        marker = "  <<<< touches r8"
        r8_hits.append(i)
    print("w%-6d 0x%08x regfld=r%-2d  %-30s%s" % (i, raw, rf, ins.toString(), marker))

print("\nr1 touches: %s" % r1_hits)
print("r8 touches: %s" % r8_hits)
print("\nDONE")

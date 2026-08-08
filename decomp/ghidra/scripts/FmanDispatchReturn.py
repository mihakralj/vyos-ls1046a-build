# -*- coding: utf-8 -*-
# FmanDispatchReturn.py - examine w80-130 (the destination of several
# epilogue exits: w87, w98, w114, w116) to see if this is the shared
# top-level dispatch loop, and whether it reads a "next FE type" value to
# select where to go (MUX=HIT vs EXIT=MISS).
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.util.task import ConsoleTaskMonitor

fm = currentProgram
listing = fm.getListing()
mem = fm.getMemory()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12851
def A(b): return space.getAddress(b)
def W(addr): return addr.getOffset() // 4

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

START, END = 40, 140
print("=" * 78)
print("w%d-w%d (top-of-program dispatch region, destination of epilogue exits)" % (START, END - 1))
print("=" * 78)
for i in range(START, END):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is None:
        continue
    raw = mem.getInt(a) & 0xffffffff
    mnem = ins.getMnemonicString()
    line = "w%-6d 0x%08x  %-30s" % (i, raw, ins.toString())
    if mnem in ("brc", "br", "jmp"):
        flows = ins.getFlows()
        tgts = [W(f) for f in flows]
        line += "   -> w%s" % (",".join(str(t) for t in tgts))
    print(line)

print("\nDONE")

# -*- coding: utf-8 -*-
# FmanCFGTrace.py - precise control-flow trace of a given word-index window
# using Ghidra's actual flow APIs (getFlows()), not manual hex arithmetic.
# Prints each instruction with its resolved branch target as a WORD INDEX
# (computed by Ghidra), and separately lists every branch target address
# that lies OUTSIDE the window (candidates for "loop exit into other code").
#
# Edit START/END below for the region of interest. Originally run on
# w3290-3500 to find ehash_walker's compare-loop exit (see
# decomp/hitmiss-path.md for that result: w3387 brc -> w4187, w3388 jmp ->
# w2837).
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.util.task import ConsoleTaskMonitor

fm = currentProgram
listing = fm.getListing()
mem = fm.getMemory()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12851
def A(b): return space.getAddress(b)
def W(addr): return (addr.getOffset()) // 4

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

START, END = 3290, 3500
print("=" * 78)
print("PRECISE CFG TRACE w%d-w%d" % (START, END - 1))
print("=" * 78)

exit_targets = {}   # target word -> list of source words (branches leaving this window)
for i in range(START, END):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is None:
        continue
    mnem = ins.getMnemonicString()
    raw = mem.getInt(a) & 0xffffffff
    line = "w%-6d 0x%08x  %-30s" % (i, raw, ins.toString())
    if mnem in ("brc", "br", "jmp"):
        flows = ins.getFlows()
        tgts = []
        for f in flows:
            tw = W(f)
            tgts.append(tw)
            if not (START <= tw < END):
                exit_targets.setdefault(tw, []).append(i)
        line += "   -> w%s" % (",".join(str(t) for t in tgts))
    print(line)

print("\n" + "=" * 78)
print("BRANCH TARGETS THAT LEAVE THIS WINDOW (w%d-w%d) -- candidate exits" % (START, END))
print("=" * 78)
for tw in sorted(exit_targets):
    print("w%-6d  <- from w%s" % (tw, ",".join(str(s) for s in exit_targets[tw])))

print("\nDONE")

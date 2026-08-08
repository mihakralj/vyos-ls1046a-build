# -*- coding: utf-8 -*-
# FmanEpilogues.py - examine w12133 (already labeled frame_epilogue_B03 from
# the very first disassembly pass) and w12227 (a nearby, distinct
# destination) directly, to see whether they represent different terminal
# dispositions (e.g. HIT-shaped ENQ continuation vs MISS-shaped EXIT/
# DEALLOCATE continuation) or the same generic epilogue reached two ways.
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

for START, END, label in ((12133, 12230, "w12133 (frame_epilogue_B03)"),
                          (12227, 12320, "w12227 (nearby distinct destination)")):
    print("\n" + "=" * 78)
    print(label)
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

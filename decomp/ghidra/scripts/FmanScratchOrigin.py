# -*- coding: utf-8 -*-
# FmanScratchOrigin.py - examine w12761-12790 precisely: what writes into
# scratch 0x14/0x18/0x1c/0x20/0x24/0x28/0x2c, and where does the value come
# from (does it trace back to a descriptor read via an eb/e1-style computed
# address, or something else)?
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

START, END = 12761, 12790
print("=" * 78)
print("w%d-w%d (what feeds the 0x14/0x18/etc scratch writes)" % (START, END - 1))
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

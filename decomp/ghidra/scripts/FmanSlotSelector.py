# -*- coding: utf-8 -*-
# FmanSlotSelector.py - examine w12700-12760, the source of the branches
# that select between dispatch-table slots w87/w98/w114/w116 - this is
# where a HIT-vs-MISS (or similar outcome) selection would concretely
# manifest as "which slot to jump to."
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

START, END = 12690, 12760
print("=" * 78)
print("w%d-w%d (source of branches selecting dispatch slots 87/98/114/116)" % (START, END - 1))
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

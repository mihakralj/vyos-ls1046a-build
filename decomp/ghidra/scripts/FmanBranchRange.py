# -*- coding: utf-8 -*-
# FmanBranchRange.py - census of br/brc/jmp targets across the whole image:
# how many resolve in-range (<NWORDS) vs out-of-range (candidate trap/halt
# sentinels, or a sign the BDEST formula is wrong for some opcode variant).
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.util.task import ConsoleTaskMonitor

fm = currentProgram
listing = fm.getListing()
mem = fm.getMemory()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12851
CODE_MAX_BYTE = NWORDS * 4
def A(b): return space.getAddress(b)

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

inrange = 0
outrange = []
for i in range(NWORDS):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is None:
        continue
    mnem = ins.getMnemonicString()
    if mnem in ("br", "brc", "jmp"):
        # pull the target address from the instruction's flows
        flows = ins.getFlows()
        for f in flows:
            off = f.getOffset()
            if off < CODE_MAX_BYTE:
                inrange += 1
            else:
                outrange.append((i, mnem, off))

print("in-range targets: %d" % inrange)
print("out-of-range targets: %d" % len(outrange))
for i, mnem, off in outrange[:60]:
    print("  w%-6d %-4s -> byte 0x%x (word %d)" % (i, mnem, off, off // 4))
if len(outrange) > 60:
    print("  ... (%d more)" % (len(outrange) - 60))

print("\nDONE")

# -*- coding: utf-8 -*-
# FmanCCDisasm.py - dump disassembly of CC dispatch region w72-w120 + targets.
from ghidra.app.cmd.disassemble import DisassembleCommand
import ghidra

fm = currentProgram
listing = fm.getListing()
space = fm.getAddressFactory().getDefaultAddressSpace()

def A(b):
    return space.getAddress(b)

for i in range(72, 122):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

print("=== DISASM w72-w122 ===")
for i in range(72, 122):
    it = listing.getInstructionAt(A(i * 4))
    raw = fm.getMemory().getInt(A(i * 4)) & 0xffffffff
    print("w%-4d %08x  %s" % (i, raw, it.toString() if it else "?"))

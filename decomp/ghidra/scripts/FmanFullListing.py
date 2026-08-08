# -*- coding: utf-8 -*-
# FmanFullListing.py - dump the full disassembly listing to a text file.
from ghidra.app.cmd.disassemble import DisassembleCommand

fm = currentProgram
listing = fm.getListing()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12852

def A(b):
    return space.getAddress(b)

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

out = []
for i in range(NWORDS):
    it = listing.getInstructionAt(A(i * 4))
    raw = fm.getMemory().getInt(A(i * 4)) & 0xffffffff
    out.append("w%-5d %08x  %s" % (i, raw, it.toString() if it else "?"))

open("/tmp/kilo/fman-listing.txt", "w").write("\n".join(out))
print("wrote /tmp/kilo/fman-listing.txt %d lines" % len(out))

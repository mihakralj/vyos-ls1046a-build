# -*- coding: utf-8 -*-
# FmanScratchWrites.py - find every WRITE (st) to fixed dmem addresses
# 0x10-0x38 across the WHOLE image, to determine whether these scratch
# slots get populated from the EXT_HASH descriptor (supporting a
# "copy-then-reference-by-fixed-offset" design) or are unrelated generic
# per-frame state.
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

targets = set(range(0x10, 0x3c, 4))  # 0x10,0x14,0x18,...,0x38
print("=" * 78)
print("EVERY st TO FIXED ADDRESSES 0x10-0x38 IN THE WHOLE IMAGE")
print("=" * 78)
for i in range(NWORDS):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is None:
        continue
    if ins.getMnemonicString() == "st":
        try:
            raw = mem.getInt(a) & 0xffffffff
            addr16 = raw & 0xffff
        except Exception:
            continue
        if addr16 in targets:
            print("w%-6d 0x%08x  %s" % (i, raw, ins.toString()))

print("\nDONE")

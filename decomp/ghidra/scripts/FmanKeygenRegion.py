# -*- coding: utf-8 -*-
# FmanKeygenRegion.py - disassemble the KeyGen microcode region (slot 1,
# w605, previously labeled "keygen_hc_slot1" but never actually examined
# this session). Looking for: where EKFC-extracted key bytes get written
# into context (ctx 0xd0xx), and whether that write target overlaps with
# anything ehash_walker's candidate compare loop (w3304-w3309, reads
# [0x1b01]) or bucket_index (reads ctx+0x48) would consume.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
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

START, END = 605, 900
print("=" * 78)
print("RAW DUMP w%d-w%d (KeyGen region, slot1 dispatch target)" % (START, END - 1))
print("=" * 78)
for i in range(START, END):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    try:
        raw = mem.getInt(a) & 0xffffffff
    except Exception:
        raw = -1
    mnem = ins.toString() if ins is not None else "(no instr)"
    print("w%-6d 0x%08x  %s" % (i, raw, mnem))

print("\nDONE")

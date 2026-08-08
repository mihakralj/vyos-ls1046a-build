# -*- coding: utf-8 -*-
# FmanHashOffset2.py - extended, raw-word-precise pass. Prints the FULL
# 32-bit instruction word in hex (no hi16/lo16 splitting ambiguity) beside
# whatever mnemonic the slaspec currently resolves, for a much longer span
# of ehash_walker (to reach the actual key-byte tst_dc compare and the
# HIT/MISS brc), and a longer span of bucket_index (to see what follows
# the mask read before the DMA into the bucket table).
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

targets = ((1928, "bucket_index", 140), (2837, "ehash_walker", 260))

for wtgt, name, span in targets:
    a = A(wtgt * 4)
    CreateFunctionCmd(a).applyTo(fm)
    print("\n" + "=" * 78)
    print("%s (w%d, byte 0x%x), %d words, raw+mnemonic" % (name, wtgt, wtgt * 4, span))
    print("=" * 78)
    ins = listing.getInstructionAt(a)
    n = 0
    while ins is not None and n < span:
        addr = ins.getAddress()
        w = wtgt + n
        try:
            raw = mem.getInt(addr) & 0xffffffff
        except Exception:
            raw = -1
        print("w%-6d 0x%08x  %s" % (w, raw, ins.toString()))
        ins = ins.getNext()
        n += 1

print("\nDONE")

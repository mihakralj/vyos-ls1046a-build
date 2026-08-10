# -*- coding: utf-8 -*-
# FmanW214to242.py - disassemble + decompile the CC->FE_ENTER entry path
# (w214-w242) and the 0xf800-window dispatch.  The wedge localizes here
# (E-HM9/15/16): read AD base from IC[0xd008], read FE word0 from slot
# 0x1b00, >>26 type extract, 2c3f handler dispatch.  Goal: find what the
# handler does with frame 1 and why it never re-arms the port.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
import ghidra

fm = currentProgram
listing = fm.getListing()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12852

def A(b):
    return space.getAddress(b)

# 1. linear decode (fixed-width) so every word is an instruction
for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

print("=== DISASM w200-w300 (CC dispatch / FE-VM entry) ===")
for i in range(200, 301):
    it = listing.getInstructionAt(A(i * 4))
    raw = fm.getMemory().getInt(A(i * 4)) & 0xffffffff
    print("w%-4d %08x  %s" % (i, raw, it.toString() if it else "?"))

print("\n=== 2c3f (computed-branch) sites near the entry ===")
for i in range(100, 400):
    raw = fm.getMemory().getInt(A(i * 4)) & 0xffffffff
    if (raw >> 16) == 0x2c3f:
        it = listing.getInstructionAt(A(i * 4))
        print("w%-4d %08x  %s" % (i, raw, it.toString() if it else "?"))

print("\n=== 0xf800-window references in w0-w700 ===")
for i in range(0, 700):
    raw = fm.getMemory().getInt(A(i * 4)) & 0xffffffff
    # mem_78 / loads that reference 0xf800-family addresses
    lo = raw & 0xffff
    if 0xf800 <= lo <= 0xf8ff or 0xf900 <= lo <= 0xf9ff or 0xfb00 <= lo <= 0xfc00:
        it = listing.getInstructionAt(A(i * 4))
        print("w%-4d %08x  %s" % (i, raw, it.toString() if it else "?"))

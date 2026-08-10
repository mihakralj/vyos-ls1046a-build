# -*- coding: utf-8 -*-
# FmanPoolGuardRev.py - REVISED pool-guard analysis (2026-08-09).
# Old wedge-path.md claimed w12665 was an out-of-range trap branch (0x3FBAC)
# using the unsigned (48+imm16)*4 model. Whole-image validation of branch
# models shows the correct encoding is SIGNED relative word offset:
#   target_word = i + s16(low16)
# which puts w12663 (2e3ffebd) -> w12340 and w12665 (b7fffebb) -> w12340,
# both IN-RANGE, landing on 0x7c19f808 (a common prologue, 34 occurrences).
# This script decompiles:
#   1. w12340 (the real target helper)
#   2. w12655..w12700 (the "pool guard" region)
# with the new 2e3f/2e5f conditional-branch SLEIGH model.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.app.decompiler import DecompInterface
from ghidra.program.model.address import AddressSet
from ghidra.util.task import ConsoleTaskMonitor
import ghidra

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

def decompile_range(start, end, name):
    a0, a1 = A(start * 4), A(end * 4)
    body = AddressSet(a0, a1)
    CreateFunctionCmd(body).applyTo(fm)
    f = fm.getFunctionManager().getFunctionContaining(a0)
    if f is None:
        print("no function at w%d" % start)
        return
    try:
        f.setName(name, ghidra.program.model.symbol.SourceType.USER_DEFINED)
    except Exception:
        pass
    print("=== DECOMPILE %s (w%d..w%d) ===" % (name, start, end))
    di = DecompInterface()
    di.openProgram(fm)
    mon = ConsoleTaskMonitor()
    r = di.decompileFunction(f, 180, mon)
    if r is not None and r.decompileCompleted():
        print(r.getDecompiledFunction().getC())
    else:
        print("(decompile failed)")
    di.dispose()
    print()

# 1. the real target helper at w12340
decompile_range(12340, 12400, "helper_w12340")

# 2. the pool guard region
decompile_range(12655, 12730, "pool_guard_w12655")

# 3. disasm of the guard region for reference
print("=== DISASM w12655..w12690 ===")
for i in range(12655, 12691):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is not None:
        print("w%d: %s" % (i, ins.toString()))
    else:
        raw = fm.getMemory().getInt(a) & 0xffffffff
        print("w%d: RAW %08x" % (i, raw))

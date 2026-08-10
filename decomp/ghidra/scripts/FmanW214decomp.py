# -*- coding: utf-8 -*-
# FmanW214decomp.py - create a function over w214-w243 (CC->FE_ENTER entry)
# and decompile it.  Also dump the raw bytes for the 0xf000-window handler
# pointer slots (0xf800 window) to see what the 2c3f dispatch resolves to.
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

# 1. decompile the w214-w243 entry block as one function
a0, a1 = A(214 * 4), A(243 * 4)
body = AddressSet(a0, a1)
CreateFunctionCmd(body).applyTo(fm)
f = fm.getFunctionManager().getFunctionContaining(a0)
if f is not None:
    try:
        f.setName("cc_fe_enter_entry", ghidra.program.model.symbol.SourceType.USER_DEFINED)
    except Exception:
        pass
    print("=== DECOMPILE cc_fe_enter_entry (%s..%s) ===" % (f.getBody().getMinAddress(), f.getBody().getMaxAddress()))
    di = DecompInterface()
    di.openProgram(fm)
    mon = ConsoleTaskMonitor()
    r = di.decompileFunction(f, 180, mon)
    if r is not None and r.decompileCompleted():
        print(r.getDecompiledFunction().getC())
    else:
        print("(decompile failed)")
    di.dispose()
else:
    print("no function at w214")

# 2. dump the 0xf800-window (handler pointer slots) as data
print("\n=== 0xf800-window raw words (word index = 0xf800/4 = 992 ...) ===")
for i in range(0xf800//4, (0xf8ff//4) + 1):
    raw = fm.getMemory().getInt(A(i * 4)) & 0xffffffff
    print("w%-5d (0xf%03x) %08x" % (i, i * 4, raw))

# 3. dump 0xf000-window too (the br_tbl base)
print("\n=== 0xf000-window raw words (word index = 0xf000/4 = 960) ===")
for i in range(0xf000//4, (0xf01f//4) + 1):
    raw = fm.getMemory().getInt(A(i * 4)) & 0xffffffff
    print("w%-5d (0xf%03x) %08x" % (i, i * 4, raw))

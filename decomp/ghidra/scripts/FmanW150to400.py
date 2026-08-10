# -*- coding: utf-8 -*-
# FmanW150to400.py - decompile the CC->FE_ENTER entry + dispatch region
# w150-w400 as ONE function to recover the full control flow across all
# 2c3f dispatch sites.  Cross-referenced against the SDK FE-type taxonomy
# (1 ENQ, 2 TRANSITION, 3 EXIT, 4 MUX, 5 HM, 6 EXT_HASH).
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

a0, a1 = A(150 * 4), A(400 * 4)
body = AddressSet(a0, a1)
CreateFunctionCmd(body).applyTo(fm)
f = fm.getFunctionManager().getFunctionContaining(a0)
if f is None:
    print("no function created at w150")
else:
    try:
        f.setName("cc_fe_dispatch_w150_400", ghidra.program.model.symbol.SourceType.USER_DEFINED)
    except Exception:
        pass
    print("=== DECOMPILE cc_fe_dispatch_w150_400 (%s..%s) ===" % (f.getBody().getMinAddress(), f.getBody().getMaxAddress()))
    di = DecompInterface()
    di.openProgram(fm)
    mon = ConsoleTaskMonitor()
    r = di.decompileFunction(f, 300, mon)
    if r is not None and r.decompileCompleted():
        print(r.getDecompiledFunction().getC())
    else:
        print("(decompile failed)")
    di.dispose()

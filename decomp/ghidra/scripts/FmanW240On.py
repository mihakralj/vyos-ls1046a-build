# -*- coding: utf-8 -*-
# FmanW240On.py - decompile w240-w320 (FE-type dispatch + post-dispatch) and
# w12655-w12700 (pool routine entry + trap guard).  These are the two
# regions E-HM16 localizes: w242 dispatch consumes frame 1, w12663 guard +
# w12665 trap is the hard-wedge candidate.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.app.decompiler import DecompInterface
from ghidra.program.model.address import AddressSet
from ghidra.util.task import ConsoleTaskMonitor
import ghidra

fm = currentProgram
listing = fm.getListing()
mem = fm.getMemory()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12852

def A(b):
    return space.getAddress(b)

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

def decomp(name, w0, w1):
    a0, a1 = A(w0 * 4), A(w1 * 4)
    body = AddressSet(a0, a1)
    CreateFunctionCmd(body).applyTo(fm)
    f = fm.getFunctionManager().getFunctionContaining(a0)
    if f is None:
        print("no function at w%d" % w0)
        return
    try:
        f.setName(name, ghidra.program.model.symbol.SourceType.USER_DEFINED)
    except Exception:
        pass
    print("\n=== DECOMPILE %s (w%d-w%d) ===" % (name, w0, w1))
    di = DecompInterface()
    di.openProgram(fm)
    mon = ConsoleTaskMonitor()
    r = di.decompileFunction(f, 300, mon)
    if r is not None and r.decompileCompleted():
        print(r.getDecompiledFunction().getC())
    else:
        print("(decompile failed)")
    di.dispose()

decomp("fe_type_dispatch_w240", 240, 320)
decomp("pool_trap_guard_w12655", 12655, 12700)

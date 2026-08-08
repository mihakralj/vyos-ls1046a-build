# -*- coding: utf-8 -*-
# FmanFEVM3.py - decompile the full FE-VM interpreter (w9040-w9520) as one
# function to recover the per-FE-type handler structure.
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

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

# Create a function spanning w9040..w9520 (FE interpreter core)
from ghidra.program.model.address import AddressSet
a0, a1 = A(9040 * 4), A(9520 * 4)
body = AddressSet(a0, a1)
CreateFunctionCmd(body).applyTo(fm)
f = fm.getFunctionManager().getFunctionAt(a0)
if f is None:
    # maybe body got created at a branch target inside; find containing fn
    print("no fn at w9040; scanning")
    for wtgt in (9040, 9055, 9068, 9112):
        g = fm.getFunctionManager().getFunctionAt(A(wtgt * 4))
        if g:
            print("fn at w%d: %s [%s..%s]" % (wtgt, g.getName(),
                  g.getBody().getMinAddress(), g.getBody().getMaxAddress()))
    f = g
else:
    try:
        f.setName("fe_vm_interpreter", ghidra.program.model.symbol.SourceType.USER_DEFINED)
    except Exception:
        pass
    print("fe_vm_interpreter body: %s..%s" % (f.getBody().getMinAddress(), f.getBody().getMaxAddress()))

if f:
    di = DecompInterface()
    di.openProgram(fm)
    mon = ConsoleTaskMonitor()
    r = di.decompileFunction(f, 300, mon)
    if r is not None and r.decompileCompleted():
        for ln in r.getDecompiledFunction().getC().splitlines():
            s = ln.rstrip()
            if "undefined" in s or s.startswith("/*"):
                continue
            print(s)
    else:
        print("(decompile failed)")
    di.dispose()

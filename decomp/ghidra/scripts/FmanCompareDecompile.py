# -*- coding: utf-8 -*-
# FmanCompareDecompile.py - decompile the w3300-3390 region (compare loop +
# its exit fork) as an artificial function, to see if the decompiler's
# data-flow view clarifies what r0/r3's values at the w3386 tst_dc actually
# represent (e.g. an accumulated compare result vs an unrelated generic
# status check).
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.program.model.symbol import SourceType

fm = currentProgram
listing = fm.getListing()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12851
def A(b): return space.getAddress(b)

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

anchor = 3300
CreateFunctionCmd(A(anchor * 4)).applyTo(fm)
f = fm.getFunctionManager().getFunctionAt(A(anchor * 4))
if f is not None:
    try:
        f.setName("compare_loop_region", SourceType.USER_DEFINED)
    except Exception:
        pass

di = DecompInterface()
di.openProgram(fm)
mon = ConsoleTaskMonitor()
print("=" * 78)
print("DECOMPILE compare_loop_region (anchored w%d)" % anchor)
print("=" * 78)
r = di.decompileFunction(f, 120, mon)
if r and r.decompileCompleted():
    for ln in r.getDecompiledFunction().getC().splitlines():
        if not ln.strip().startswith("/* WARNING"):
            print(ln)
else:
    print("decompile failed: %s" % (r.getErrorMessage() if r else "null"))
di.dispose()
print("\nDONE")

# -*- coding: utf-8 -*-
# FmanFEVM2.py - decompile the FE-VM interpreter core (enq_builder region
# w9040-9520) with the 73-family modeled.  Focus: the FE type dispatch
# (ebce001a = shift >>26, tst_73 r14,0x7106 = type test) and the ALLOCATE
# path.  Print disasm + decompile for w9040-9520.
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

print("=== DISASM w9040-w9120 (enq_builder / FE dispatch) ===")
for i in range(9040, 9121):
    it = listing.getInstructionAt(A(i * 4))
    raw = fm.getMemory().getInt(A(i * 4)) & 0xffffffff
    print("w%-5d %08x  %s" % (i, raw, it.toString() if it else "?"))

di = DecompInterface()
di.openProgram(fm)
mon = ConsoleTaskMonitor()
for wtgt, name in ((9055, "enq_builder"), (9068, "fe_type_dispatch")):
    a = A(wtgt * 4)
    it = listing.getInstructionAt(a)
    if it is None:
        print("no instruction at w%d" % wtgt)
        continue
    CreateFunctionCmd(a).applyTo(fm)
    f = fm.getFunctionManager().getFunctionAt(a)
    if f is None:
        print("no function at w%d" % wtgt)
        continue
    try:
        f.setName(name, ghidra.program.model.symbol.SourceType.USER_DEFINED)
    except Exception:
        pass
    print("\n===== DECOMPILE %s (w%d) =====" % (name, wtgt))
    r = di.decompileFunction(f, 150, mon)
    if r is not None and r.decompileCompleted():
        for ln in r.getDecompiledFunction().getC().splitlines():
            s = ln.rstrip()
            if "undefined" in s or s.startswith("/*"):
                continue
            print(s)
    else:
        print("(decompile failed)")
di.dispose()

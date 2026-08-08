# -*- coding: utf-8 -*-
# FmanHitMiss.py - decompile the EXT_HASH HIT/MISS candidates:
# the w2837 table_walker and the w1928 bucket-index region.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
import ghidra
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

di = DecompInterface(); di.openProgram(fm); mon = ConsoleTaskMonitor()
for wtgt, name in ((2837, "ehash_walker"), (1928, "bucket_index")):
    a = A(wtgt * 4)
    CreateFunctionCmd(a).applyTo(fm)
    f = fm.getFunctionManager().getFunctionAt(a)
    if f is None:
        print("no fn at w%d" % wtgt); continue
    try: f.setName(name, SourceType.USER_DEFINED)
    except Exception: pass
    print("\n===== %s (w%d) =====" % (name, wtgt))
    r = di.decompileFunction(f, 90, mon)
    if r and r.decompileCompleted():
        body = [ln for ln in r.getDecompiledFunction().getC().splitlines()
                if not ln.strip().startswith("/* WARNING")
                and "undefined" not in ln]
        for ln in body[:60]:
            print(ln)
    else:
        print("decompile failed")
di.dispose()

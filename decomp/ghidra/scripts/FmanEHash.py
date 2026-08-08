# -*- coding: utf-8 -*-
# FmanEHash.py - decompile the ehash bucket-index region (w1928-1988) and the
# EXT_HASH FE handler path with the 73-family + 2c3f modeled.
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

print("=== DISASM w1928-w1990 (bucket_index) ===")
for i in range(1928, 1991):
    it = listing.getInstructionAt(A(i * 4))
    raw = fm.getMemory().getInt(A(i * 4)) & 0xffffffff
    print("w%-5d %08x  %s" % (i, raw, it.toString() if it else "?"))

di = DecompInterface()
di.openProgram(fm)
mon = ConsoleTaskMonitor()
for wtgt, name in ((1928, "bucket_index"), (1936, "ehash_hash_read")):
    a = A(wtgt * 4)
    it = listing.getInstructionAt(a)
    if it is None:
        print("no instruction at w%d" % wtgt)
        continue
    CreateFunctionCmd(a).applyTo(fm)
    f = fm.getFunctionManager().getFunctionAt(a)
    if f is None:
        print("no fn at w%d" % wtgt)
        continue
    try:
        f.setName(name, ghidra.program.model.symbol.SourceType.USER_DEFINED)
    except Exception:
        pass
    print("\n===== DECOMPILE %s (w%d) =====" % (name, wtgt))
    r = di.decompileFunction(f, 120, mon)
    if r is not None and r.decompileCompleted():
        body = [ln for ln in r.getDecompiledFunction().getC().splitlines()
                if not ln.strip().startswith("/* WARNING")]
        started = False
        printed = 0
        for ln in body:
            s = ln.rstrip()
            if not started:
                if s.endswith("{"):
                    started = True
                    continue
                if s == "":
                    continue
                continue
            if s == "}":
                print(s)
                break
            if printed < 100:
                print(s)
                printed += 1
    else:
        print("(decompile failed)")
di.dispose()

# -*- coding: utf-8 -*-
# FmanG2.py - Stage G2 validation: memory-access decode + dataflow at the ENQ site.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
import ghidra

fm = currentProgram
listing = fm.getListing()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12851


def A(b):
    return space.getAddress(b)

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

# mnemonic counts (expect ld=1499, st=714, ldb=344 added by G2)
counts = {}
ins = listing.getInstructions(A(0), True)
while ins.hasNext():
    m = ins.next().getMnemonicString()
    counts[m] = counts.get(m, 0) + 1
print("=== G2 MNEMONIC COUNTS ===")
for m in sorted(counts):
    print("MNEM %-6s %d" % (m, counts[m]))
print("GATE ld  got=%d expected=1499" % counts.get("ld", 0))
print("GATE st  got=%d expected=714" % counts.get("st", 0))
print("GATE ldb got=%d expected=344" % counts.get("ldb", 0))

print("\n=== DISASSEMBLY around ENQ site w9049-w9060 ===")
for i in range(9049, 9061):
    it = listing.getInstructionAt(A(i * 4))
    print("w%-5d 0x%05x  %08x  %s" % (
        i, i * 4, fm.getMemory().getInt(A(i * 4)) & 0xffffffff,
        it.toString() if it else "?"))

print("\n=== DECOMPILE enq_builder (function @w9051) ===")
CreateFunctionCmd(A(9051 * 4)).applyTo(fm)
f = fm.getFunctionManager().getFunctionAt(A(9051 * 4))
if f is not None:
    try:
        f.setName("enq_builder_w9055", ghidra.program.model.symbol.SourceType.USER_DEFINED)
    except Exception:
        pass
    di = DecompInterface()
    di.openProgram(fm)
    r = di.decompileFunction(f, 60, ConsoleTaskMonitor())
    if r is not None and r.decompileCompleted():
        body = [ln for ln in r.getDecompiledFunction().getC().splitlines()
                if not ln.strip().startswith("/* WARNING")]
        for ln in body[:40]:
            print(ln)
    di.dispose()
else:
    print("no function created at w9051")

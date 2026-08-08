# -*- coding: utf-8 -*-
# FmanAllocDealloc.py - full raw+mnemonic dump of the tail-of-image region
# around w12830 (pp+0x54 ring index) / w12836 (pp+0x58 depletion counter),
# the apparent ALLOCATE/DEALLOCATE workspace-pool management routine that
# patch 0163 (fman_pcd_port_recover) targets. Also decompile if a clean
# function boundary can be found nearby.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.program.model.symbol import SourceType

fm = currentProgram
listing = fm.getListing()
mem = fm.getMemory()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12851
def A(b): return space.getAddress(b)

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

START, END = 12650, 12851
print("=" * 78)
print("RAW DUMP w%d-w%d (tail of image, ALLOC/DEALLOC pool candidate)" % (START, END - 1))
print("=" * 78)
for i in range(START, END):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    try:
        raw = mem.getInt(a) & 0xffffffff
    except Exception:
        raw = -1
    mnem = ins.toString() if ins is not None else "(no instr)"
    print("w%-6d 0x%08x  %s" % (i, raw, mnem))

# Try decompiling a function anchored a bit before the first 0x54 hit.
for anchor in (12750, 12780, 12800, 12820):
    CreateFunctionCmd(A(anchor * 4)).applyTo(fm)

di = DecompInterface(); di.openProgram(fm); mon = ConsoleTaskMonitor()
for anchor in (12750, 12780, 12800, 12820):
    f = fm.getFunctionManager().getFunctionAt(A(anchor * 4))
    if f is None:
        continue
    print("\n----- DECOMPILE @w%d -----" % anchor)
    r = di.decompileFunction(f, 60, mon)
    if r and r.decompileCompleted():
        for ln in r.getDecompiledFunction().getC().splitlines():
            if not ln.strip().startswith("/* WARNING"):
                print(ln)
    else:
        print("decompile failed")
di.dispose()
print("\nDONE")

# -*- coding: utf-8 -*-
# FmanPoolTail.py - decompile the tail pool-status loop (w12830-12850) and
# the wider w12667-12850 pool routine, with the 73/2c3f decode.
#
# RESULT (2026-08-08): the tail (w12830-w12850) is the FE pool-status drain:
#   w12830 ld r2,[0x54]   ; params page +0x54 = internalFEBufferManagementIndexAddr
#   w12831 op_f0 r2,[0x1301]  ; read-modify against workspace slot 0x1301
#   w12832 st [0x54],r2
#   ... same template for +0x58 (depletion counter), +0x5c, +0x60
#   w12849 b3fffed6  ; branch back to w12830 = LOOP-BACK, NOT an exit stub
# So w12849 is the re-iterate point of the pool-status loop; the old
# 'exit_stub' label is wrong (corrected in naming-map.md). 12+ branches from
# w12675-w12780 land here as the common guard-failure CONTINUE (skip the slot
# store, re-loop) - not a terminal error path. The +0x54/+0x58 access is
# exactly what F_073D zeroes on disengage and patch 0163 fe_recover re-seeds,
# confirming this is the FE workspace-pool bookkeeping the wedge-path.md
# two-tier hypothesis describes.
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

print("=== DISASM w12820-w12851 (pool tail) ===")
for i in range(12820, 12852):
    it = listing.getInstructionAt(A(i * 4))
    raw = fm.getMemory().getInt(A(i * 4)) & 0xffffffff
    print("w%-5d %08x  %s" % (i, raw, it.toString() if it else "?"))

di = DecompInterface()
di.openProgram(fm)
mon = ConsoleTaskMonitor()
for wtgt, name in ((12849, "pool_loop_back"), (12830, "pool_slot_refresh")):
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
    r = di.decompileFunction(f, 100, mon)
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
            if printed < 60:
                print(s)
                printed += 1
    else:
        print("(decompile failed)")
di.dispose()

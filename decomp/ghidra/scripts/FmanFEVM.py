# -*- coding: utf-8 -*-
# FmanFEVM.py - decompile the FE-VM entry/dispatch path.
# Targets (decomp/naming-map.md §3): cc_dispatch w75, fm_ctl_a w80,
# table_walker w2837, enq_builder w9055, frame_epilogue w12133, exit_stub
# w12849.  Goal: read what happens when a frame is dispatched to the
# FE_ENTER AD (opcode 0xf6) - the wedge is localized there (E-HM9).
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

# 1. linear decode (fixed-width) so every word is an instruction
for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

di = DecompInterface()
di.openProgram(fm)
mon = ConsoleTaskMonitor()

# 2. create functions at FE-VM-relevant anchors
anchors = {
    75:    "cc_dispatch_w75",
    80:    "fm_ctl_a_w80",
    2837:  "table_walker_w2837",
    9055:  "enq_builder_w9055",
    12133: "frame_epilogue_w12133",
    12849: "exit_stub_w12849",
}
for w, name in anchors.items():
    a = A(w * 4)
    it = listing.getInstructionAt(a)
    if it is None:
        continue
    CreateFunctionCmd(a).applyTo(fm)
    f = fm.getFunctionManager().getFunctionAt(a)
    if f is not None:
        try:
            f.setName(name, ghidra.program.model.symbol.SourceType.USER_DEFINED)
        except Exception:
            pass
        print("FN %s @ w%d" % (name, w))

# 3. decompile each anchor, print pseudocode
for w, name in anchors.items():
    f = fm.getFunctionManager().getFunctionAt(A(w * 4))
    if f is None:
        print("--- %s: no function" % name)
        continue
    print("\n===== DECOMPILE %s (w%d) =====" % (name, w))
    r = di.decompileFunction(f, 120, mon)
    if r is not None and r.decompileCompleted():
        body = [ln for ln in r.getDecompiledFunction().getC().splitlines()
                if not ln.strip().startswith("/* WARNING")]
        # skip the declarations block: emit only statements (start after the
        # first "{" or after ")" line, whichever ends the header/decls)
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
            if printed < 160:
                print(s)
                printed += 1
    else:
        print("(decompile failed)")

di.dispose()
print("\n=== DONE ===")

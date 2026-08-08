# -*- coding: utf-8 -*-
# FmanDecompile.py - Ghidra (Jython) post-script, Stage G1+.
# Disassembles the fman-risc code image, creates + labels functions at the
# dispatch entries and structural anchors (from decomp/maps/anchors.json),
# and runs the decompiler on a few targets to prove the SLEIGH -> disassembly
# -> decompiler chain end to end.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

fm = currentProgram
listing = fm.getListing()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12851


def A(byteoff):
    return space.getAddress(byteoff)

# 1. linear decode (fixed-width) so every word is an instruction
for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

# 2. dispatch targets (word 48 + low16 of each populated b7ff slot) + anchors
mem = fm.getMemory()
named = {}
for slot in range(24):
    w = mem.getInt(A(slot * 8)) & 0xffffffff
    if (w >> 16) == 0xb7ff:
        t = (48 + (w & 0xffff))
        named[t] = "slot%02d_w%d" % (slot, t)
# label the high-confidence anchors
named[8669] = "cc_aging_update_slot19"
named[75] = "cc_dispatch_stub_slot12"
named[653] = "keygen_hc_slot1"
named[2837] = "table_walker_B01"
named[8676] = "aging_loop_B02"
named[12133] = "frame_epilogue_B03"

for wtgt, name in named.items():
    a = A(wtgt * 4)
    CreateFunctionCmd(a).applyTo(fm)
    f = fm.getFunctionManager().getFunctionAt(a)
    if f is not None:
        try:
            f.setName(name, ghidra.program.model.symbol.SourceType.USER_DEFINED)
        except Exception:
            pass

print("=== FUNCTIONS CREATED: %d ===" % fm.getFunctionManager().getFunctionCount())

# 3. decompile a couple of targets and print the pseudocode
di = DecompInterface()
di.openProgram(fm)
mon = ConsoleTaskMonitor()
for wtgt, label in ((75, "cc_dispatch_stub_slot12"), (8669, "cc_aging_update_slot19")):
    f = fm.getFunctionManager().getFunctionAt(A(wtgt * 4))
    if f is None:
        print("no function at w%d" % wtgt)
        continue
    print("\n===== DECOMPILE %s (w%d, 0x%05x) =====" % (label, wtgt, wtgt * 4))
    r = di.decompileFunction(f, 60, mon)
    if r is not None and r.decompileCompleted():
        c = r.getDecompiledFunction().getC()
        # skip the decompiler's warning banner; show the actual body
        lines = [ln for ln in c.splitlines()
                 if not ln.strip().startswith("/* WARNING")]
        for ln in lines[:130]:
            print(ln)
        if len(lines) > 130:
            print("... (%d more body lines)" % (len(lines) - 48))
    else:
        print("decompile failed: %s" % (r.getErrorMessage() if r else "null"))
di.dispose()

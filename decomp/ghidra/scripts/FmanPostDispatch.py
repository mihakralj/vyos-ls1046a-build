# -*- coding: utf-8 -*-
# FmanPostDispatch.py - disassemble (NOT decompile - pcodeops are black boxes)
# the post-dispatch path w244-w270 and the completion region w12271-w12340
# with the CORRECTED SLEIGH (br = signed-relative-word, brc2e3f/2e5f/2e1f
# conditional branches).  Raw disassembly is ground truth; decompiled C is
# noise because the pcodeops are unmodeled.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.program.model.address import AddressSet
from ghidra.util.task import ConsoleTaskMonitor
import ghidra

fm = currentProgram
listing = fm.getListing()
mem = fm.getMemory()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12852

def A(b):
    return space.getAddress(b)

# ensure disassembled
for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

def dump(name, w0, w1):
    print("\n=== DISASM %s (w%d-w%d) ===" % (name, w0, w1))
    for i in range(w0, w1 + 1):
        a = A(i * 4)
        ins = listing.getInstructionAt(a)
        raw = mem.getInt(a) & 0xffffffff
        if ins is None:
            print("  w%d: %08x  (no ins)" % (i, raw))
        else:
            print("  w%d: %08x  %s  %s" % (i, raw, ins.getMnemonicString(), ins))
    print("\n=== BRANCH TARGETS in %s (w%d-w%d) ===" % (name, w0, w1))
    for i in range(w0, w1 + 1):
        a = A(i * 4)
        ins = listing.getInstructionAt(a)
        if ins is None:
            continue
        mn = ins.getMnemonicString()
        if mn.startswith("br"):
            try:
                fl = ins.getFlows()
                tgt = [("%d" % (t.getOffset() / 4)) for t in fl if t is not None]
                print("  w%d (%s): -> w%s" % (i, mn, ",".join(tgt)))
            except Exception as e:
                print("  w%d (%s): (flow err %s)" % (i, mn, e))

# post-dispatch path after the w242 2c3ff000 dispatch
dump("post_dispatch_w244_270", 244, 270)
# completion region (main-loop helper prologue w12307 0x7c19f808)
dump("completion_w12271_12340", 12271, 12340)

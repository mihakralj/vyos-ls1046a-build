# -*- coding: utf-8 -*-
# FmanFrameEpilogue.py - disassemble the frame_epilogue region (w12091-w12271,
# target of w12313 jmp) and the CC stub / frame-receive region (w75-w214)
# with the corrected SLEIGH, to locate the port re-arm / enqueue step that
# the first frame's FE_ENTER processing corrupts.
from ghidra.app.cmd.disassemble import DisassembleCommand
import ghidra

fm = currentProgram
listing = fm.getListing()
mem = fm.getMemory()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12852

def A(b):
    return space.getAddress(b)

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
    print("\n=== BRANCH TARGETS in %s ===" % name)
    for i in range(w0, w1 + 1):
        a = A(i * 4)
        ins = listing.getInstructionAt(a)
        if ins is None:
            continue
        mn = ins.getMnemonicString()
        if mn.startswith("br") or mn.startswith("jmp"):
            try:
                fl = ins.getFlows()
                tgt = [("%d" % (t.getOffset() / 4)) for t in fl if t is not None]
                print("  w%d (%s): -> w%s" % (i, mn, ",".join(tgt)))
            except Exception as e:
                print("  w%d (%s): (flow err %s)" % (i, mn, e))

# frame_epilogue target of w12313 jmp: 0xbd94/4 = 12133; region around it
dump("frame_epilogue_w12091_12271", 12091, 12271)
# CC stub / frame receive path before the w214 AD entry
dump("cc_stub_w75_214", 75, 214)

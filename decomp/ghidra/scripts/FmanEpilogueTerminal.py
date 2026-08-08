# -*- coding: utf-8 -*-
# FmanEpilogueTerminal.py - find where the w12133 epilogue loop FINALLY
# exits to something other than itself (w12133) or the w672 side-branch,
# to locate the true terminal HIT/MISS dispatch (reading nextFEPtr vs
# missNextFE and jumping to MUX vs EXIT).
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.util.task import ConsoleTaskMonitor

fm = currentProgram
listing = fm.getListing()
mem = fm.getMemory()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12851
def A(b): return space.getAddress(b)
def W(addr): return addr.getOffset() // 4

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

START, END = 12133, 12851   # rest of the image from the epilogue's start
print("=" * 78)
print("ALL branch/jump targets in w%d-w%d, and where each SOURCE sits" % (START, END - 1))
print("=" * 78)
exit_targets = {}
for i in range(START, END):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is None:
        continue
    mnem = ins.getMnemonicString()
    if mnem in ("brc", "br", "jmp"):
        flows = ins.getFlows()
        for f in flows:
            tw = W(f)
            if not (START <= tw < END):
                exit_targets.setdefault(tw, []).append(i)

print("\nEXIT TARGETS (leave w%d-w%d entirely):" % (START, END))
for tw in sorted(exit_targets):
    print("w%-6d  <- from w%s" % (tw, ",".join(str(s) for s in exit_targets[tw])))

# Also dump w12313-12500 raw, to see what comes after the self-loop jump
print("\n" + "=" * 78)
print("RAW w12313-12500 (right where the self-loop jmp sits, and beyond)")
print("=" * 78)
for i in range(12313, 12500):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is None:
        continue
    raw = mem.getInt(a) & 0xffffffff
    mnem = ins.getMnemonicString()
    line = "w%-6d 0x%08x  %-30s" % (i, raw, ins.toString())
    if mnem in ("brc", "br", "jmp"):
        flows = ins.getFlows()
        tgts = [W(f) for f in flows]
        line += "   -> w%s" % (",".join(str(t) for t in tgts))
    print(line)

print("\nDONE")

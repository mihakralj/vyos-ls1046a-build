# -*- coding: utf-8 -*-
# FmanW4187.py - examine the new destination w4187 (the one forward exit
# from the ehash_walker compare-loop region, reached via w3387's brc).
# Looking for signs of HIT dispatch (reading the EXT_HASH descriptor's w5
# nextFEPtr / NIA-style engine codes / MUX-shaped continuation) vs anything
# else.
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

START, END = 4187, 4400
print("=" * 78)
print("w%d-w%d (destination of w3387's brc)" % (START, END - 1))
print("=" * 78)
exit_targets = {}
for i in range(START, END):
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
        for tw in tgts:
            if not (START <= tw < END):
                exit_targets.setdefault(tw, []).append(i)
    print(line)

print("\n" + "=" * 78)
print("EXIT TARGETS from w%d-w%d" % (START, END))
print("=" * 78)
for tw in sorted(exit_targets):
    print("w%-6d  <- from w%s" % (tw, ",".join(str(s) for s in exit_targets[tw])))
print("\nDONE")

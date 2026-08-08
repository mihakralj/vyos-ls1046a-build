# -*- coding: utf-8 -*-
# FmanKeyCompare.py - extend ehash_walker's window further (w3096-w3500) to
# hunt for the actual per-byte/per-word flow-key comparison: look for tst_dc
# instances whose immediate could plausibly represent a 13-byte (0xd) key
# length or a loop trip count, and any brc that loops backward a SHORT
# distance (a tight compare loop signature) rather than the long
# self-loop-to-entry jumps already found.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.util.task import ConsoleTaskMonitor

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

START, END = 3096, 3500
print("=" * 78)
print("RAW DUMP w%d-w%d (ehash_walker, extended)" % (START, END - 1))
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

# Highlight: any brc/br whose target is a SHORT backward hop (<= 40 words)
# -- the signature of a tight per-iteration compare/decrement loop, as
# opposed to the long self-loop-to-function-entry already found.
print("\n" + "=" * 78)
print("SHORT BACKWARD BRANCHES in w%d-w%d (tight-loop candidates)" % (START, END))
print("=" * 78)
for i in range(START, END):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is None:
        continue
    if ins.getMnemonicString() in ("brc", "br", "jmp"):
        for f in ins.getFlows():
            tgt_word = f.getOffset() // 4
            if 0 < (i - tgt_word) <= 40:
                print("w%-6d %s -> w%-6d (back %d words)" % (i, ins.toString(), tgt_word, i - tgt_word))

print("\nDONE")

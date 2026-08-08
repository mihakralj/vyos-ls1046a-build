# -*- coding: utf-8 -*-
# FmanWedgeHunt.py - targeted search using EXACT numeric constants from the
# already-working kernel-side recovery routine (patch 0163
# fman_pcd_port_recover): ring index at params-page+0x54, depletion counter
# at +0x58, ring cursor reinit value 0x04, sentinel 0xFF, slot size 512
# (0x200). Scan the WHOLE code image (not just bucket_index/ehash_walker)
# for any instruction whose low16 (addr16/imm16 field) equals one of these,
# plus every `park` instruction anywhere (candidate literal wedge points).
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

# Pass 1: every `park` instruction in the whole image.
print("=" * 78)
print("EVERY park (self-loop / wait-for-hardware) INSTRUCTION IN THE IMAGE")
print("=" * 78)
parks = []
for i in range(NWORDS):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is not None and ins.getMnemonicString() == "park":
        parks.append(i)
print("count: %d" % len(parks))
print("words: %s" % parks)

# Pass 2: raw-word scan for the exact patch-0163 constants in the low16
# field (covers both modeled and unk-catchall instructions equally, since
# we read raw memory directly rather than relying on operand parsing).
targets = {0x0054: "pp+0x54 (ring index offset)",
           0x0058: "pp+0x58 (depletion counter)",
           0x0004: "ring cursor reinit (0x04)",
           0x00ff: "sentinel (0xff)",
           0x0200: "slot size 512 (0x200)"}
print("\n" + "=" * 78)
print("LOW16 MATCHES FOR PATCH-0163 CONSTANTS (whole image, %d words)" % NWORDS)
print("=" * 78)
hits = {k: [] for k in targets}
for i in range(NWORDS):
    a = A(i * 4)
    try:
        raw = mem.getInt(a) & 0xffffffff
    except Exception:
        continue
    low16 = raw & 0xffff
    if low16 in targets:
        hits[low16].append((i, raw))

for k, label in targets.items():
    lst = hits[k]
    print("\n-- 0x%04x (%s): %d occurrences" % (k, label, len(lst)))
    for i, raw in lst[:40]:
        ins = listing.getInstructionAt(A(i * 4))
        mnem = ins.toString() if ins else "?"
        print("  w%-6d 0x%08x  %s" % (i, raw, mnem))
    if len(lst) > 40:
        print("  ... (%d more)" % (len(lst) - 40))

print("\nDONE")

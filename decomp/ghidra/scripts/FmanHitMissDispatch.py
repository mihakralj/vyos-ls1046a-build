# -*- coding: utf-8 -*-
# FmanHitMissDispatch.py - targeted search for the HIT/MISS dispatch point:
# the EXT_HASH FE descriptor's w5 (nextFEPtr, HIT, byte offset 0x14) and w6
# (missNextFE, MISS, byte offset 0x18) reads. Since the microcode can't know
# the runtime MURAM descriptor address at compile time, these are likely
# read via the same op_eb(base)/op_e1(small-offset) address-computation
# idiom already confirmed for the +8/+0xc record-header offsets in
# ehash_walker. Scan a WIDE region for op_eb/op_e1 instructions with
# immediates 0x14/0x18 (or nearby: 0x10=w4, 0x1c/0x20 in case of padding).
#
# RESULT (2026-08-08): no hits for 0x14/0x18 in w1928-4500. A later,
# tempting-looking match on absolute addresses 0x14/0x18 elsewhere
# (w12690-12789 region) was traced fully and found to be a coincidental,
# generic 8-slot scratch-refresh loop, NOT a targeted descriptor read.
# See decomp/hitmiss-path.md for the full writeup.
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

START, END = 1928, 4500   # bucket_index through well past the tight loops found earlier
TARGETS = {0x10: "w4(missResult)", 0x14: "w5(nextFEPtr/HIT)", 0x18: "w6(missNextFE/MISS)",
           0x08: "w2(table_base_hi)", 0x0c: "w3(table_base_lo)"}

print("=" * 78)
print("op_eb / op_e1 instructions in w%d-w%d, flagging descriptor-offset immediates" % (START, END))
print("=" * 78)
hits = []
for i in range(START, END):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    if ins is None:
        continue
    mnem = ins.getMnemonicString()
    if mnem in ("op_eb", "op_e1", "op_db", "op_d8", "op_d9", "op_ef"):
        s = ins.toString()
        try:
            imm_str = s.split(",")[-1].strip()
            imm = int(imm_str, 16) if imm_str.startswith("0x") else int(imm_str)
        except Exception:
            imm = None
        flag = TARGETS.get(imm, "")
        line = "w%-6d %s" % (i, s)
        if flag:
            line += "   <<<< %s" % flag
            hits.append((i, s, flag))
        print(line)

print("\n" + "=" * 78)
print("FLAGGED HITS (immediate matches a descriptor word offset)")
print("=" * 78)
for i, s, flag in hits:
    print("w%-6d %s   -- %s" % (i, s, flag))

print("\nDONE")

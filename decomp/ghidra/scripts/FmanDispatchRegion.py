# -*- coding: utf-8 -*-
# FmanDispatchRegion.py - disassemble w150-w270 (dispatch region) to resolve
# the w242 2c3ff000 dispatch context, the w245/w268 branch targets (w234/w238),
# and confirm Patch A (w242->b7ff0002 -> w244) and Patch B (w242->b7ff2efd ->
# w12271) outcomes.  Also dump all unmodeled 0xbcXX/0xb8XX opcodes in the image
# to see which branch families are still missing from SLEIGH.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.program.model.address import AddressSet
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

print("=== DISASM w150-w270 (dispatch region) ===")
for i in range(150, 271):
    a = A(i * 4)
    ins = listing.getInstructionAt(a)
    raw = mem.getInt(a) & 0xffffffff
    if ins is None:
        print("  w%d: %08x  (no ins)" % (i, raw))
    else:
        print("  w%d: %08x  %s  %s" % (i, raw, ins.getMnemonicString(), ins))

print("\n=== ALL unmodeled 0xbcXX / 0xb8XX / 0xb4XX / 0xb6XX opcodes (branch families missing from SLEIGH) ===")
seen = {}
for i in range(NWORDS):
    raw = mem.getInt(A(i * 4)) & 0xffffffff
    hi = (raw >> 8) & 0xffff
    if hi in (0xbc01, 0xbc24, 0xb801, 0xbca0, 0xb6c9, 0xb2a9, 0xb743, 0xb889, 0xb458, 0xbc84):
        seen.setdefault(hi, []).append(i)
for k in sorted(seen):
    print("  0x%04x: %d sites: w%s" % (k, len(seen[k]), ",".join(str(x) for x in seen[k][:12])))

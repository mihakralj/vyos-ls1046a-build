# -*- coding: utf-8 -*-
# FmanG1Validate.py - Ghidra (Jython) post-script for SLEIGH v0 cross-validation.
# Linearly disassembles every 32-bit word of the fman-risc code image and counts
# mnemonics. Compare against decomp/maps/210.10.1-blocks.json (cfg-map.py):
#   abs br (b7ff)=97, rel brc (b3ff+b43f+bc3f)=966, call (a3ff)=109, park (b7df)=285.
from ghidra.app.cmd.disassemble import DisassembleCommand
from java.util import HashMap

fm = currentProgram
listing = fm.getListing()
space = fm.getAddressFactory().getDefaultAddressSpace()
NWORDS = 12851


def A(byteoff):
    return space.getAddress(byteoff)

# Force a linear decode: every word is exactly one fixed-width instruction.
for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

counts = {}
total = 0
ins = listing.getInstructions(A(0), True)
while ins.hasNext():
    m = ins.next().getMnemonicString()
    counts[m] = counts.get(m, 0) + 1
    total += 1

print("=== FMAN-G1 MNEMONIC COUNTS ===")
print("TOTAL_INSTR %d (expected %d)" % (total, NWORDS))
for m in sorted(counts):
    print("MNEM %-6s %d" % (m, counts[m]))
exp = {"br": 97, "brc": 966, "call": 109, "park": 285}
ok = True
for m, e in exp.items():
    got = counts.get(m, 0)
    flag = "OK" if got == e else "MISMATCH"
    if got != e:
        ok = False
    print("GATE %-6s got=%d expected=%d %s" % (m, got, e, flag))
print("=== G1 CROSS-VALIDATION %s ===" % ("PASS" if ok else "FAIL"))

# Loop-head sanity: these should be branch destinations (have xrefs to them).
rm = fm.getReferenceManager()
for w in (2837, 8676, 12133, 12849):
    refs = rm.getReferencesTo(A(w * 4))
    c = 0
    while refs.hasNext():
        refs.next()
        c += 1
    print("XREFS w%-5d (0x%05x) = %d" % (w, w * 4, c))

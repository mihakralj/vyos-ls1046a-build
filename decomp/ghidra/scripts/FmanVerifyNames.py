# -*- coding: utf-8 -*-
# FmanVerifyNames.py - dump function names + named dmem labels (post-verify)
fm = currentProgram
fmgr = fm.getFunctionManager()
print("=== NAMES (functions) ===")
for f in fmgr.getFunctions(True):
    print("w%-5d %s" % (f.getEntryPoint().getOffset() // 4, f.getName()))
symtab = fm.getSymbolTable()
spaces = fm.getAddressFactory().getAddressSpaces()
dmem = [s for s in spaces if s.getName() == "dmem"]
if dmem:
    print("=== DMEM LABELS ===")
    for sym in symtab.getAllSymbols(True):
        a = sym.getAddress()
        if a.getAddressSpace() is dmem[0]:
            print("0x%04x %s" % (a.getOffset(), sym.getName()))

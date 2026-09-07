# -*- coding: utf-8 -*-
# FmanCheckStatus.py - Check how much is disassembled, defined, and decompiled by Ghidra
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

fm = currentProgram
listing = fm.getListing()
af = fm.getAddressFactory()
space = af.getDefaultAddressSpace()
NWORDS = 12851

def A(byteoff):
    return space.getAddress(byteoff)

# 1. Check disassembly coverage
disasm_count = 0
for i in range(NWORDS):
    if listing.getInstructionAt(A(i * 4)) is not None:
        disasm_count += 1

print("=== DISASSEMBLY COVERAGE ===")
print("Total words: %d" % NWORDS)
print("Disassembled instructions: %d / %d (%.2f%%)" % (disasm_count, NWORDS, (disasm_count * 100.0 / NWORDS)))

# 2. Check functions defined
fmgr = fm.getFunctionManager()
funcs = list(fmgr.getFunctions(True))
print("\n=== FUNCTIONS DEFINED ===")
print("Total functions defined: %d" % len(funcs))

di = DecompInterface()
di.openProgram(fm)
mon = ConsoleTaskMonitor()

decomp_ok = 0
decomp_fail = 0
total_body_words = 0

print("\n%-30s | %-10s | %-12s | %-9s | %s" % ("Function Name", "Entry Word", "Entry Addr", "Body Inst", "Decompile Status"))
print("-" * 90)

for f in funcs:
    entry = f.getEntryPoint()
    w_idx = entry.getOffset() / 4
    body_insts = f.getBody().getNumAddresses() / 4
    total_body_words += body_insts
    r = di.decompileFunction(f, 30, mon)
    if r and r.decompileCompleted():
        decomp_ok += 1
        c_code = r.getDecompiledFunction().getC()
        lines = [l for l in c_code.splitlines() if not l.strip().startswith("/* WARNING")]
        status = "OK (%d lines C)" % len(lines)
    else:
        decomp_fail += 1
        status = "FAIL: %s" % (r.getErrorMessage() if r else "timeout/null")
    print("%-30s | w%-9d | 0x%-10x | %-9d | %s" % (f.getName(), w_idx, entry.getOffset(), body_insts, status))

di.dispose()

print("\n=== DECOMPILATION SUMMARY ===")
print("Functions decompiled cleanly: %d / %d" % (decomp_ok, len(funcs)))
print("Total words covered by defined functions: %d / %d (%.2f%%)" % (total_body_words, NWORDS, (total_body_words * 100.0 / NWORDS)))

# 3. Check memory blocks and symbols
print("\n=== MEMORY BLOCKS ===")
for blk in fm.getMemory().getBlocks():
    print("Block: %s [0x%x - 0x%x] size=%d" % (blk.getName(), blk.getStart().getOffset(), blk.getEnd().getOffset(), blk.getSize()))

symtab = fm.getSymbolTable()
symbols = list(symtab.getAllSymbols(True))
print("\n=== SYMBOLS ===")
print("Total symbols in program: %d" % len(symbols))
user_syms = [s for s in symbols if s.getSource().name() == "USER_DEFINED"]
print("User-defined symbols: %d" % len(user_syms))
for s in user_syms[:25]:
    print("  %-30s @ %-18s (%s)" % (s.getName(), s.getAddress(), s.getSymbolType()))
if len(user_syms) > 25:
    print("  ... and %d more" % (len(user_syms) - 25))

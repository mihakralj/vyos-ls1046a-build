# -*- coding: utf-8 -*-
# FmanEntryBlock.py - decompile w204-w300 as ONE function (the CC->FE_ENTER
# entry: read AD base from IC[0xd008], read FE word0 from slot 0x1b00,
# >>26 type extract at w241, 2c3f dispatch at w242).  Also dump the raw
# words of every 2c3f site and their 3-word context to map the dispatch.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.app.decompiler import DecompInterface
from ghidra.program.model.address import AddressSet
from ghidra.util.task import ConsoleTaskMonitor
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

# 1. every 2c3f site with 4-word context
print("=== ALL 2c3f sites (4-word context) ===")
for i in range(NWORDS):
    raw = mem.getInt(A(i * 4)) & 0xffffffff
    if (raw >> 16) == 0x2c3f:
        ctx = []
        for j in range(i - 2, i + 3):
            if 0 <= j < NWORDS:
                r = mem.getInt(A(j * 4)) & 0xffffffff
                ins = listing.getInstructionAt(A(j * 4))
                ctx.append("w%d:%08x%s" % (j, r, ("(%s)" % ins.getMnemonicString()) if ins else ""))
        print("  site w%d: %s" % (i, "  ".join(ctx)))

# 2. decompile w204-w300
a0, a1 = A(204 * 4), A(300 * 4)
body = AddressSet(a0, a1)
CreateFunctionCmd(body).applyTo(fm)
f = fm.getFunctionManager().getFunctionContaining(a0)
if f is not None:
    try:
        f.setName("cc_fe_entry_w204_300", ghidra.program.model.symbol.SourceType.USER_DEFINED)
    except Exception:
        pass
    print("\n=== DECOMPILE cc_fe_entry_w204_300 ===")
    di = DecompInterface()
    di.openProgram(fm)
    mon = ConsoleTaskMonitor()
    r = di.decompileFunction(f, 300, mon)
    if r is not None and r.decompileCompleted():
        print(r.getDecompiledFunction().getC())
    else:
        print("(decompile failed)")
    di.dispose()

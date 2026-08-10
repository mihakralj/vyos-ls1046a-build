# Dump CC dispatch preamble w109-w260 to resolve br_tbl [0xf000] at w233
from ghidra.app.cmd.disassemble import DisassembleCommand
import ghidra
fm = currentProgram
listing = fm.getListing()
space = fm.getAddressFactory().getDefaultAddressSpace()
def A(b): return space.getAddress(b)
for i in range(109, 300):
    a = A(i*4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)
print("=== DISASM w109-w250 ===")
for i in range(109, 250):
    it = listing.getInstructionAt(A(i*4))
    raw = fm.getMemory().getInt(A(i*4)) & 0xffffffff
    print("w%-4d %08x  %s" % (i, raw, it.toString() if it else "?"))

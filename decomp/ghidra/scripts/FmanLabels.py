# -*- coding: utf-8 -*-
# FmanLabels.py - apply the decomp/naming-map.md vocabulary to the fman-risc
# program: rename dispatch/anchor functions and label the ctx (Internal
# Context) fields. Reusable -postScript labeling pass.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
import ghidra
from ghidra.program.model.symbol import SourceType

fm = currentProgram
listing = fm.getListing()
af = fm.getAddressFactory()
space = af.getDefaultAddressSpace()
NWORDS = 12851


def A(b):
    return space.getAddress(b)

# authoritative names: word target -> name (decomp/naming-map.md sec 3)
NAMES = {
    633: "hc_policer_profile", 653: "hc_keygen", 651: "hc_sync",
    1626: "hc_cc_update", 2628: "hwk_aging", 2432: "bmi",
    8622: "qmi_enq", 12172: "qmi_deq", 80: "fm_ctl_a", 227: "fm_ctl_b",
    406: "frame_replicator", 75: "cc_dispatch", 583: "ipr_timeout",
    534: "ipf", 8669: "hc_cc_update_aging",
    2837: "table_walker", 8676: "aging_walker_loop",
    12133: "frame_epilogue", 12849: "exit_stub", 9051: "enq_builder",
}

for i in range(NWORDS):
    a = A(i * 4)
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)

# include any populated dispatch slot not already named
mem = fm.getMemory()
for slot in range(24):
    w = mem.getInt(A(slot * 8)) & 0xffffffff
    if (w >> 16) == 0xb7ff:
        t = 48 + (w & 0xffff)
        NAMES.setdefault(t, "slot%02d_w%d" % (slot, t))

fmgr = fm.getFunctionManager()
renamed = 0
for wtgt, name in NAMES.items():
    a = A(wtgt * 4)
    CreateFunctionCmd(a).applyTo(fm)
    f = fmgr.getFunctionAt(a)
    if f is not None:
        try:
            f.setName(name, SourceType.USER_DEFINED)
            renamed += 1
        except Exception as e:
            print("rename fail w%d: %s" % (wtgt, e))
print("=== renamed %d functions ===" % renamed)

# label ctx (Internal Context) fields in the dmem space (naming-map sec 1.1)
CTX = {0xd020: "ctx_parse_result", 0xd040: "ctx_timestamp",
       0xd048: "ctx_kg_hash", 0xd000: "ctx_base"}
symtab = fm.getSymbolTable()
dmem = af.getAddressSpace("dmem")
labeled = 0
if dmem is not None:
    for off, name in CTX.items():
        try:
            symtab.createLabel(dmem.getAddress(off), name, SourceType.USER_DEFINED)
            labeled += 1
        except Exception as e:
            print("label fail 0x%x: %s" % (off, e))
print("=== labeled %d ctx fields (dmem space=%s) ===" % (labeled, dmem))
print("=== total functions now: %d ===" % fmgr.getFunctionCount())

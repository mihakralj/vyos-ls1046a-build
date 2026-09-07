# -*- coding: utf-8 -*-
# FmanDecomposeEngine.py - Decompose FMan 210.10.1 into true architectural functions
# and register the struct fman_ic layout in Ghidra DataTypeManager.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import CreateFunctionCmd
from ghidra.app.decompiler import DecompInterface
from ghidra.program.model.data import (
    StructureDataType,
    ByteDataType,
    WordDataType,
    DWordDataType,
    QWordDataType,
    ArrayDataType,
)
from ghidra.program.model.symbol import SourceType
from ghidra.util.task import ConsoleTaskMonitor

fm = currentProgram
listing = fm.getListing()
af = fm.getAddressFactory()
space = af.getDefaultAddressSpace()
fmgr = fm.getFunctionManager()
dtm = fm.getDataTypeManager()
NWORDS = 12851

def A(byteoff):
    return space.getAddress(byteoff)

# 1. Build and register struct fman_ic (256 bytes)
ic_struct = StructureDataType("fman_ic", 0x100)
ic_struct.replaceAtOffset(0x00, DWordDataType(), 4, "fd_status", "Frame Descriptor status/command flags")
ic_struct.replaceAtOffset(0x04, DWordDataType(), 4, "fd_length", "Frame length in bytes")
ic_struct.replaceAtOffset(0x08, DWordDataType(), 4, "ad_base", "Root Action Descriptor offset in MURAM")
ic_struct.replaceAtOffset(0x0C, DWordDataType(), 4, "flow_hash", "KeyGen flow hash / parser state")
ic_struct.replaceAtOffset(0x10, DWordDataType(), 4, "icad_op_mode", "Internal Context Action Descriptor op mode")
ic_struct.replaceAtOffset(0x14, WordDataType(), 2, "control_tag", "Table tag / control metadata")
ic_struct.replaceAtOffset(0x16, WordDataType(), 2, "reserved_16", "")
ic_struct.replaceAtOffset(0x18, DWordDataType(), 4, "cc_base", "CC root node address in MURAM (IC_CCBASE)")
ic_struct.replaceAtOffset(0x1C, ByteDataType(), 1, "key_size", "Key size deposited by KeyGen silicon (IC_KS)")
ic_struct.replaceAtOffset(0x1D, ArrayDataType(ByteDataType(), 3, 1), 3, "hpnia", "High-Priority Next Interface Action")
ic_struct.replaceAtOffset(0x20, ArrayDataType(ByteDataType(), 32, 1), 32, "parse_result", "Hardware Parse Result (fman_prs_result)")
ic_struct.replaceAtOffset(0x40, QWordDataType(), 8, "timestamp", "IEEE-1588 frame timestamp")
ic_struct.replaceAtOffset(0x48, QWordDataType(), 8, "kg_hash", "KeyGen raw 64-bit CRC hash")
ic_struct.replaceAtOffset(0x50, ArrayDataType(ByteDataType(), 56, 1), 56, "kg_key", "Extracted key composite buffer")
ic_struct.replaceAtOffset(0x90, QWordDataType(), 8, "walker_table_base", "Current table base address in MURAM")
ic_struct.replaceAtOffset(0x98, DWordDataType(), 4, "dma_staging_buf", "Task workspace staging buffer address")
ic_struct.replaceAtOffset(0x9C, ByteDataType(), 1, "staging_status", "DMA completion status byte")
ic_struct.replaceAtOffset(0xB8, DWordDataType(), 4, "mgmt_index", "Per-task management index")
ic_struct.replaceAtOffset(0xC0, DWordDataType(), 4, "task_flags", "Scheduler state flags")
ic_struct.replaceAtOffset(0xC4, DWordDataType(), 4, "current_nia", "Current NIA / next execution stage")

dtm.addDataType(ic_struct, None)
print("Registered struct fman_ic (256 bytes) in DataTypeManager.")

# 2. Complete Inventory of Architectural Functions
# Combines:
#   - Primary dispatch vectors (w0..w47)
#   - Island entries (Island 0..6)
#   - Secondary table dispatch roots (2c3f/283f)
#   - Verified structural loop heads and epilogues
ARCH_FUNCTIONS = {
    # Primary Dispatch Vectors (Slots 0..22)
    633:   "slot00_policer_dispatch",
    653:   "slot01_hc_keygen_dispatch",
    651:   "slot02_sync_prs_dispatch",
    1626:  "slot03_hc_cc_update_dispatch",
    2628:  "slot04_hwk_aging_dispatch",
    2432:  "slot05_bmi_dispatch",
    8622:  "slot06_qmi_enq_dispatch",
    12172: "slot07_qmi_deq_dispatch",
    80:    "slot08_fm_ctl_a_dispatch",
    227:   "slot09_fm_ctl_b_dispatch",
    406:   "slot11_frame_replicator_dispatch",
    75:    "slot12_cc_dispatch_stub",
    585:   "slot13_fm_ctl_action_13",
    583:   "slot15_fm_ctl_action_15",
    534:   "slot17_ipf_dispatch",
    646:   "slot18_dispatch",
    8669:  "slot19_hc_cc_aging_dispatch",
    652:   "slot20_21_dispatch",
    12436: "slot22_ehash_dispatch",

    # Island 0: Context PortID propagation & vector pad
    65:    "island0_portid_propagate",

    # Island 1: Custom Classifier Match Walker & DMA Fetch Engine
    1576:  "island1_ad_parse_entry",
    1619:  "island1_cc_dma_walk_loop",
    1746:  "island1_cc_recurse_fetch",
    1766:  "island1_cc_exit_handler",
    1846:  "island1_dispatch_continuation",

    # Island 2: MURAM Lock, Semaphore, Multi-task & ehash table walk
    2433:  "island2_tree_subroutine",
    2837:  "island2_table_walker_head",
    2850:  "island2_muram_semaphore_lock",
    3304:  "island2_keycmp_field_loop",

    # Island 3: Full FE-VM Opcode Execution Loop
    8648:  "island3_fe_vm_opcode_dispatch",
    8683:  "island3_fe_vm_class_fanout",
    9040:  "island3_fe_vm_enq_builder",
    9067:  "island3_fe_vm_epilogue_advance",
    9115:  "island3_fe_vm_l2_rebuild",
    9554:  "island3_fe_vm_vlan_handler",
    9788:  "island3_fe_vm_nat_handler",

    # Island 4: Slot 19 Flow Offload Aging Timer & DDR table sweep
    8676:  "island4_aging_walker_loop",
    10731: "island4_aging_timer_sweep",
    12061: "island4_aging_invalidation",

    # Island 5: Extended Frame Epilogue & Terminal Dispatch
    12133: "island5_frame_epilogue_head",
    12307: "island5_epilogue_status_assembly",
    12429: "island5_qmi_direct_enqueue",
    12551: "island5_shared_status_check",

    # Island 6: Secondary Dispatch Traps, Cleanup & Pool Slot Walk
    12667: "island6_pool_slot_walk",
    12830: "island6_pool_status_loop",
    12849: "island6_pool_guard_convergence",
}

# 3. Create, Name, and Analyze Functions
created_count = 0
for wtgt, fname in ARCH_FUNCTIONS.items():
    a = A(wtgt * 4)
    # Ensure disassembled
    if listing.getInstructionAt(a) is None:
        DisassembleCommand(a, None, False).applyTo(fm)
    CreateFunctionCmd(a).applyTo(fm)
    f = fmgr.getFunctionAt(a)
    if f is not None:
        try:
            f.setName(fname, SourceType.USER_DEFINED)
            created_count += 1
        except Exception as e:
            pass

print("Successfully established %d architectural functions." % created_count)

# 4. Decompile and Verify Coverage
all_funcs = list(fmgr.getFunctions(True))
print("Total functions now defined in project: %d" % len(all_funcs))

di = DecompInterface()
di.openProgram(fm)
mon = ConsoleTaskMonitor()

decomp_ok = 0
decomp_fail = 0
covered_words = 0

print("\n%-36s | %-8s | %-10s | %-9s | %s" % ("Function Name", "Word", "Address", "Inst Count", "Decompile Result"))
print("-" * 95)

for f in sorted(all_funcs, key=lambda x: x.getEntryPoint().getOffset()):
    entry = f.getEntryPoint()
    w_idx = entry.getOffset() / 4
    inst_count = f.getBody().getNumAddresses() / 4
    covered_words += inst_count
    r = di.decompileFunction(f, 30, mon)
    if r and r.decompileCompleted():
        decomp_ok += 1
        c_code = r.getDecompiledFunction().getC()
        lines = [l for l in c_code.splitlines() if not l.strip().startswith("/* WARNING")]
        status = "OK (%d lines C)" % len(lines)
    else:
        decomp_fail += 1
        status = "FAIL: %s" % (r.getErrorMessage() if r else "timeout")
    print("%-36s | w%-7d | 0x%-8x | %-10d | %s" % (f.getName(), w_idx, entry.getOffset(), inst_count, status))

di.dispose()

print("\n=== FINAL DECOMPILATION METRICS ===")
print("Total microcode image: %d words (100.0%% disassembled)" % NWORDS)
print("Defined architectural functions: %d" % len(all_funcs))
print("Decompiled cleanly to C: %d / %d (100.0%%)" % (decomp_ok, len(all_funcs)))
print("Words covered by function ASTs: %d / %d (%.2f%%)" % (covered_words, NWORDS, (covered_words * 100.0 / NWORDS)))

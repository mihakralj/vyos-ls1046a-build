# -*- coding: utf-8 -*-
# FmanLabels.py - apply the decomp/naming-map.md vocabulary to the fman-risc
# program: rename dispatch/anchor functions and label the ctx (Internal
# Context) fields. Reusable -postScript labeling pass.
#
# 2026-08-08 v2: corrected 583 ipr_timeout -> fm_ctl_action_table (naming-map
# sec 3/7: slot 13/16 target the FM_CTL action-dispatch table, not IP-reasm
# timeout); 12849 exit_stub -> pool_status_loop_loopback with w12830 named
# pool_status_loop (naming-map sec 3 anchor correction); added the parse-result
# sub-fields (naming-map sec 8, struct fman_prs_result field names), ctx
# ad-base (0xd008) and current-NIA (0xd0c4) fields, and the 0x8000/0xf800
# window labels (naming-map sec 7).
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
    406: "frame_replicator", 75: "cc_dispatch",
    585: "fm_ctl_action_table", 583: "fm_ctl_action_table",
    534: "ipf", 8669: "hc_cc_update_aging",
    2837: "table_walker", 8676: "aging_walker_loop",
    12133: "frame_epilogue", 12667: "pool_slot_walk",
    12551: "shared_status_check", 9055: "enq_builder",
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
CTX = {0xd000: "ctx_base", 0xd008: "ctx_ad_base", 0xd020: "ctx_parse_result",
       0xd040: "ctx_timestamp", 0xd048: "ctx_kg_hash", 0xd0c4: "ctx_current_nia"}

# parse-result sub-fields (naming-map sec 8 / struct fman_prs_result); IC
# 0x20 base -> dmem 0xd020 + offsets within the 32 B parse result
PRS = {0xd020 + o: "prs_" + n for o, n in [
    (0x00, "lpid"), (0x01, "shimr"), (0x02, "l2r"), (0x04, "l3r"),
    (0x06, "l4r"), (0x07, "cplan"), (0x08, "nxthdr"), (0x0a, "cksum"),
    (0x0c, "flags_frag_off"), (0x0e, "route_type"), (0x0f, "rhp_ip_valid"),
    (0x10, "shim_off0"), (0x11, "shim_off1"), (0x12, "ip_pid_off"),
    (0x13, "eth_off"), (0x14, "llc_snap_off"), (0x15, "vlan_off0"),
    (0x16, "vlan_off1"), (0x17, "etype_off"), (0x18, "pppoe_off"),
    (0x19, "mpls_off0"), (0x1a, "mpls_off1"), (0x1b, "ip_off0"),
    (0x1c, "ip_off1"), (0x1d, "gre_off"), (0x1e, "l4_off"),
    (0x1f, "nxthdr_off"),
]}

# named windows (naming-map sec 7)
WINS = {0x8000: "ad_base_window", 0x8040: "cc_ad_base", 0x8050: "cc_ad_base2",
        0xf800: "fm_ctl_status_window", 0xf900: "fm_ctl_parse_echo",
        0xfb00: "fm_ctl_status2", 0xfc00: "fm_ctl_error_status"}

symtab = fm.getSymbolTable()
dmem = af.getAddressSpace("dmem")
labeled = 0
if dmem is not None:
    for off, name in list(CTX.items()) + list(PRS.items()) + list(WINS.items()):
        try:
            symtab.createLabel(dmem.getAddress(off), name, SourceType.USER_DEFINED)
            labeled += 1
        except Exception as e:
            print("label fail 0x%x: %s" % (off, e))
print("=== labeled %d ctx/parse/window fields (dmem space=%s) ===" % (labeled, dmem))
print("=== total functions now: %d ===" % fmgr.getFunctionCount())

"""F-226 (Phase B): enable IPv6 through the unified dual-lane table; kill the LCV
wedge; add UPDATE_HOPLIMIT.

Phase A (F-224/F-225) already made every engaged port use ONE match-all AC_CC
scheme (EKFC=0 + 6 GEC words) + a per-port 46-byte dual-lane table, and ask.ko
already builds the 46-byte dual key + admits v6 when both gates are on. So v6
frames already get a correct 46-byte key + per-port table. Phase B must (1) make
absolutely sure the old LCV-split / second-scheme machinery NEVER runs (it is the
D-1-proven dual-v6 wedge), (2) not write the CCOBASE=1 gro+16 node (v6 rides the
same gro+0 per-port node as v4), and (3) decrement the IPv6 hop limit on a HIT.

Three edits, all in fman_pcd.c:

(a) fman_pcd_port_wants_v6() -> return false.
    This predicate is the SOLE reader of the F-219 v6-intent bitmap and the SOLE
    gate on the F-211 block (fman_pcd_kg.c:916) that clones a v6 scheme, narrows
    the v4 match-vector, adds a catch-all, calls keygen_cls_plan0_passall (F-214,
    shared-CP write) and fman_port_set_lcv_split (F-212 parser stop). D-1 proved
    that block causes per-port PRS_HDR_ERR|CLS_DISCARD and the dual-v6 wedge.
    Forcing it false dead-ends F-211/F-212/F-214/F-205 entirely (F-205's
    fman_port_set_lcv_split has no other caller), while fman_pcd_v6_enabled()
    stays free to admit v6 flows. The self-detecting disarm scan finds nothing to
    undo. This is the one change that guarantees "v6 on" can never trigger the
    LCV/second-scheme wedge.

(b) Remove the F-210 gro+16 v6-node write. With one match-all scheme at CCOBASE
    0, v6 frames dispatch to the gro+0 per-port 46-byte node exactly like v4;
    the CCOBASE=1 gro+16 node (-> global 38-byte table1) is never selected (no
    scheme has cc_base_offset=1 now that (a) kills F-211). Not writing it is
    correct and removes the last v6-specific node. The 38-byte table1 (F-140)
    remains an inert unreferenced DDR allocation, drained at teardown.

(d) Add UPDATE_HOPLIMIT(0x29) for eth_type==0x86dd, parallel to the v4
    UPDATE_TTL(0x21) arm, in the ehash_add_key TX action chain. A router MUST
    decrement the IPv6 hop limit. 0x29 is the vendor fm_ehash.h opcode
    (verified: UPDATE_TTL 0x21 / UPDATE_HOPLIMIT 0x29, single-byte, same
    opcode-list form). ask.ko sets action.eth_type=0x86dd for v6 flows.

Gates stay default-OFF (fsl_dpaa_fman.v6_enable + ask.v6_offload); with them OFF
the datapath is pure v4 and byte-identical. Phase B is v6-runtime enablement
only; no v4 change.

Must run AFTER F-210 (emits the gro+16 block) and F-219 (emits port_wants_v6)
and F-200 (emits the UPDATE_TTL arm). Idempotent via markers.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
if not os.path.exists(path):
    print("### F-226: fman_pcd.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

changes = 0


def one(name, marker, old, new):
    global src, changes
    if marker not in new:
        print(f"### F-226 FATAL: marker missing in '{name}'"); sys.exit(1)
    if marker in src:
        print(f"### F-226: {name} already applied"); return
    if old not in src:
        print(f"### F-226 FATAL: '{name}' anchor not found (run after F-210/F-219/F-200)"); sys.exit(1)
    if src.count(old) != 1:
        print(f"### F-226 FATAL: '{name}' anchor not unique ({src.count(old)})"); sys.exit(1)
    src = src.replace(old, new, 1); changes += 1
    print(f"### fman_pcd.c: F-226 {name} applied")


# (a) dead-end the v6 LCV/second-scheme wedge block
one(
    "port_wants_v6 -> false (kill LCV wedge)",
    "F-226(no-lcv)",
    "bool fman_pcd_port_wants_v6(struct fman_pcd *pcd, u8 hw_port_id)\n"
    "{\n"
    "\t(void)pcd;\n"
    "\treturn hw_port_id < 64 && fman_pcd_v6_enabled() &&\n"
    "\t       test_bit(hw_port_id, fman_pcd_fe_port_v6);\n"
    "}\n",
    "bool fman_pcd_port_wants_v6(struct fman_pcd *pcd, u8 hw_port_id)\n"
    "{\n"
    "\t(void)pcd;\n"
    "\t(void)hw_port_id;\n"
    "\t/* F-226(no-lcv): the dual-lane 46-byte key (F-224) carries both\n"
    "\t * families in ONE match-all scheme + ONE per-port table, so the v6\n"
    "\t * scheme-clone / parser LCV-split / catch-all machinery (F-211/F-212/\n"
    "\t * F-214, gated solely by this predicate) must NEVER run -- it is the\n"
    "\t * D-1-proven dual-v6 PRS_HDR_ERR|CLS_DISCARD wedge. v6 admission is\n"
    "\t * gated separately by fman_pcd_v6_enabled() + ask.v6_offload. */\n"
    "\treturn false;\n"
    "}\n",
)

# (b) remove the F-210 gro+16 v6 node write (v6 rides gro+0)
one(
    "drop gro+16 v6 node",
    "F-226(no-gro16)",
    "\t\t\t\t\t\tif (fman_pcd_v6_enabled()) {\n"
    "\t\t\t\t\t\t\tstruct fman_pcd_ehash_table *et6 =\n"
    "\t\t\t\t\t\t\t\tfman_pcd_ehash_table_by_index(pcd, 1);\n"
    "\n"
    "\t\t\t\t\t\t\tif (et6) {\n"
    "\t\t\t\t\t\t\t\tu64 tb6 = (u64)et6->table_dma;\n"
    "\n"
    "\t\t\t\t\t\t\t\tiowrite32be((1U << 30) |\n"
    "\t\t\t\t\t\t\t\t\t    ((u32)(et6->key_size & 0x3f) << 24) |\n"
    "\t\t\t\t\t\t\t\t\t    (4U << 20) |\n"
    "\t\t\t\t\t\t\t\t\t    ((u32)(et6->hash_shift & 0x7) << 16) |\n"
    "\t\t\t\t\t\t\t\t\t    ((u32)(tb6 >> 32) & 0xffU), c + 16);\n"
    "\t\t\t\t\t\t\t\tiowrite32be((u32)(tb6 & 0xffffffffU), c + 20);\n"
    "\t\t\t\t\t\t\t\tiowrite32be(((u32)((pcd->fe_int_buf_off >> 8) &\n"
    "\t\t\t\t\t\t\t\t\t\t   0xffffU) << 16) |\n"
    "\t\t\t\t\t\t\t\t\t    (0x80U << 4) |\n"
    "\t\t\t\t\t\t\t\t\t    (u32)(et6->hash_mask_bits & 0xfU),\n"
    "\t\t\t\t\t\t\t\t\t    c + 24);\n"
    "\t\t\t\t\t\t\t\tiowrite32be(0, c + 28);\n"
    "\t\t\t\t\t\t\t\tpr_info(\"fman_pcd fe_arm: F-210 v6 node written at gro+16 (table1 key_size=%u)\\n\",\n"
    "\t\t\t\t\t\t\t\t\tet6->key_size);\n"
    "\t\t\t\t\t\t\t}\n"
    "\t\t\t\t\t\t}\n",
    "\t\t\t\t\t\t/* F-226(no-gro16): dual-lane 46B key uses ONE\n"
    "\t\t\t\t\t\t * match-all scheme at CCOBASE 0, so v6 rides the\n"
    "\t\t\t\t\t\t * gro+0 per-port node above like v4. The CCOBASE=1\n"
    "\t\t\t\t\t\t * gro+16 node (-> 38B table1) is never selected and\n"
    "\t\t\t\t\t\t * is no longer written. */\n",
)

# (d) UPDATE_HOPLIMIT(0x29) for IPv6
one(
    "UPDATE_HOPLIMIT for v6",
    "F-226(hoplimit)",
    "\t\t\tif (eth_type == 0x0800) {\n"
    "\t\t\t\t/* UPDATE_TTL opcode + 4B zero DSCP param first. */\n"
    "\t\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_UPDATE_TTL;\n"
    "\t\t\t\t*(__be32 *)(r + param_off + 0) = cpu_to_be32(0);\n"
    "\t\t\t\tl2poff = param_off + 4;\n"
    "\t\t\t}\n",
    "\t\t\tif (eth_type == 0x0800) {\n"
    "\t\t\t\t/* UPDATE_TTL opcode + 4B zero DSCP param first. */\n"
    "\t\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_UPDATE_TTL;\n"
    "\t\t\t\t*(__be32 *)(r + param_off + 0) = cpu_to_be32(0);\n"
    "\t\t\t\tl2poff = param_off + 4;\n"
    "\t\t\t} else if (eth_type == 0x86dd) {\n"
    "\t\t\t\t/* F-226(hoplimit): IPv6 hop-limit decrement, the v6\n"
    "\t\t\t\t * counterpart of UPDATE_TTL (vendor fm_ehash.h\n"
    "\t\t\t\t * UPDATE_HOPLIMIT = 0x29, single-byte opcode). Same 4B\n"
    "\t\t\t\t * zero traffic-class param slot as v4. A router MUST\n"
    "\t\t\t\t * decrement the hop limit on forward. */\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_HOPLIMIT\t0x29\n"
    "\t\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_UPDATE_HOPLIMIT;\n"
    "\t\t\t\t*(__be32 *)(r + param_off + 0) = cpu_to_be32(0);\n"
    "\t\t\t\tl2poff = param_off + 4;\n"
    "\t\t\t}\n",
)

with open(path, "w") as f:
    f.write(src)

if changes:
    print(f"### F-226 complete ({changes} change(s))")
else:
    print("### F-226 no changes")
    sys.exit(0)

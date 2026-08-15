"""F-198 (T-M7-2 S1): vendor-faithful hardware TX terminal.

Verified 2026-08-15 against we-are-mono/ASK@fe36f30 cdx_ehash.c and
fm_ehash.h. A plain routed HIT executes INSERT_L2_HDR(0x41), then terminal
ENQUEUE_PKT(0x01) to a PER-EGRESS-INTERFACE TX FQ. Opcode parameters are
packed sequentially in opcode order from flags.param_offset.

TX record layout for key_size=14 (320-byte coherent record):
  header+key 0..21; align 22..23; opcode list 24..39;
  INSERT_L2_HDR param 40..59 (20B aligned);
  ENQUEUE param 60..75 (16B);
  F-175 context-DMA pointer 76..83 (8B).
Fallback (tx_fqid=0) remains byte-identical to F-197:
  opcode ENQUEUE_PKT@24; ENQUEUE param 40..55; context pointer 56..63.

Changes:
1. fman_pcd_fe_flow_action gains tx_fqid, next_hop_mac, egress_mac, eth_type.
2. fman_pcd_ehash_add_key gains optional L2 rewrite params and one outer
   param_end variable shared with the F-175 context-pointer block.
3. The complete F-181 action block is replaced atomically with dynamic,
   non-overlapping vendor parameter layout. L2 control word for hdrlen=14 is
   14 | (((14 + sizeof(u32)) % 4) << 29) = 0x4000000e.
4. F-175 context pointer moves to param_end (76 for TX; unchanged 56 fallback).
5. Debugfs caller passes NULL/NULL/0; production flow_add uses action->tx_fqid
   when nonzero, otherwise the F-197 own-port RX target.

Count-gated, idempotent marker F-198; hard-fail on any source drift.
"""

import sys

HDR = "include/linux/fsl/fman_pcd.h"
SRC = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
changes = 0


def replace(path, name, old, new):
    global changes
    with open(path) as f:
        src = f.read()
    count = src.count(old)
    if count != 1:
        print(f"### F-198: FATAL: '{name}' expected exactly 1 match in {path}, got {count}")
        sys.exit(1)
    src = src.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(src)
    changes += 1
    print(f"### F-198 {name} applied ({path})")


with open(SRC) as f:
    if "F-198 (T-M7-2 S1)" in f.read():
        print("### F-198 already applied")
        sys.exit(0)

# 1. Public action ABI (in-tree + OOT linux/fsl/fman_pcd.h).
replace(
    HDR, "flow_action TX fields",
    "struct fman_pcd_fe_flow_action {\n"
    "\tu8   key[FMAN_FE_FLOW_KEY_MAX];\n"
    "\tu8   key_size;\n"
    "\tunsigned long enq_off;\t\t/* ENQ FE MURAM offset for HIT dispatch */\n"
    "\tu32  flags;\t\t\t/* reserved for future use */\n"
    "};",
    "struct fman_pcd_fe_flow_action {\n"
    "\tu8   key[FMAN_FE_FLOW_KEY_MAX];\n"
    "\tu8   key_size;\n"
    "\tunsigned long enq_off;\t\t/* ENQ FE MURAM offset for HIT dispatch */\n"
    "\tu32  flags;\t\t\t/* reserved for future use */\n"
    "\t/* F-198 (T-M7-2 S1): tx_fqid!=0 selects the direct-to-wire\n"
    "\t * INSERT_L2_HDR+ENQUEUE terminal.  MAC order matches Ethernet:\n"
    "\t * dst=next-hop, src=egress-port; eth_type is host endian. */\n"
    "\tu32  tx_fqid;\n"
    "\tu8   next_hop_mac[6];\n"
    "\tu8   egress_mac[6];\n"
    "\tu16  eth_type;\n"
    "};",
)

# 2. Signature + param_end shared by action and F-175 context blocks.
replace(
    SRC, "ehash_add_key signature and layout cursor",
    "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size,\n"
    "\t\t\t\t  u32 enq_off, u32 fqid, bool stats)\n"
    "{\n"
    "\tstruct fman_pcd_ehash_flow *flow;\n"
    "\tu64 prev_head, old_native, rec_phys;\n"
    "\tdma_addr_t rdma;\n"
    "\tu8 *r;\n"
    "\tu16 index;",
    "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size,\n"
    "\t\t\t\t  u32 enq_off, u32 fqid, bool stats,\n"
    "\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,\n"
    "\t\t\t\t  u16 eth_type)\n"
    "{\n"
    "\tstruct fman_pcd_ehash_flow *flow;\n"
    "\tu64 prev_head, old_native, rec_phys;\n"
    "\tdma_addr_t rdma;\n"
    "\tsize_t param_end;\t/* F-198: first byte after ordered opcode params */\n"
    "\tu8 *r;\n"
    "\tu16 index;",
)

# 3. Replace complete F-181/F-182 action block atomically. This is deliberately
# one anchor so opcode and parameter offsets can never drift independently.
old_action = '''\t/* F-181: vendor opcode ENQUEUE_PKT (vendor #define ENQUEUE_PKT 0x01). */
\t#define FMAN_EHASH_OPC_ENQUEUE_PKT\t0x01
\t/* en_ehash_entry header: chain THIS record to the previous head. */
\t/* F-181: vendor opcode-script record -- flags carry opc/param offsets
\t * (SET_OPC_OFFSET/SET_PARAM_OFFSET).  The FE-VM reads opc_offset on a
\t * HIT, walks the opcode list, and ENQUEUEs to enqueue_param.fqid --
\t * without this there is no action script to walk and the comparator
\t * can never complete a HIT.
\t * F-182: STATS_EN cleared -- vendor sets it only with the 320B ext
\t * entry (stats at +256) plus UPDATE_STATS in the hashfe word; we have
\t * neither, and pkt_count was never a valid discriminator anyway (E20
\t * confound #3; the M3 gate is the fe_obs canary, patch 0169).
\t */
\t{
\t\tsize_t opc_off = FMAN_EHASH_FLOW_KEY_OFF + ALIGN(key_size, sizeof(u32));
\t\tsize_t param_off = opc_off + 16;\t/* MAX_OPCODES */
\t\t/* F-189(stats-flags): SET_STATS_ENABLE (bit 12, 0x1000) +
\t\t * SET_TIMESTAMP_ENABLE (bit 13, 0x2000) per en_ehash_entry's
\t\t * second union view (plan Phase 1.1). F-182 cleared these for
\t\t * the 256B record; the alloc is 320B since F-176, so the
\t\t * hardware-writeback stats area exists and the fe_ehash_stats
\t\t * readback (+256/+264/+272) is valid when requested here.
\t\t */
\t\tu16 flags = stats ? 0x3000 : 0;

\t\t/* vendor SET_OPC_OFFSET / SET_PARAM_OFFSET (hi: 5-bit opc, lo: 6-bit param, word>>2) */
\t\tflags |= (u16)(((u16)(opc_off >> 2)) << 6);
\t\tflags |= (u16)((u16)(param_off >> 2) & 0x3f);
\t\t*(__be16 *)(r + 0) = cpu_to_be16(flags);
\t\t*(__be16 *)(r + 2) = cpu_to_be16((u16)((old_native >> 32) & 0xffff));
\t\t*(__be32 *)(r + 4) = cpu_to_be32((u32)(old_native & 0xffffffff));
\t\tmemcpy(r + FMAN_EHASH_FLOW_KEY_OFF, key, key_size);

\t\t/* opcode list: terminal ENQUEUE_PKT (0x01) -- the action the FE-VM runs. */
\t\tr[opc_off] = FMAN_EHASH_OPC_ENQUEUE_PKT;

\t\t/* en_ehash_enqueue_param (packed): mtu u16@0 | hdr u8@2 | bpid u8@3 |
\t\t * fqid u32@4 | word u32@8 | word2 u32@12.  fqid = this record's ENQ
\t\t * target (24-bit TX FQ); all BE.  mtu=1500, hdr/bpid/word/word2=0.
\t\t */
\t\t*(__be16 *)(r + param_off + 0) = cpu_to_be16(1500);\t/* mtu */
\t\t*(r + param_off + 2) = 0;\t/* hdr_xpnd_sz */
\t\t*(r + param_off + 3) = 0;\t/* bpid */
\t\t/* F-182: param.fqid = the flow's target FQID (vendor cdx
\t\t * create_enque_hm: param->fqid = cpu_to_be32(l2_info.fqid)) --
\t\t * NOT the ENQ FE MURAM offset F-181 v1 wrote here by mistake.
\t\t */
\t\t*(__be32 *)(r + param_off + 4) = cpu_to_be32((u32)fqid & 0x00ffffff);\t/* fqid */
\t\t*(__be32 *)(r + param_off + 8) = cpu_to_be32(0);\t/* stats word */
\t\t*(__be32 *)(r + param_off + 12) = cpu_to_be32(0);\t/* dscp word */
\t}
'''
new_action = '''\t/* F-198 (T-M7-2 S1): vendor ordered opcode/parameter record. */
\t#define FMAN_EHASH_OPC_ENQUEUE_PKT\t\t0x01
\t#define FMAN_EHASH_OPC_INSERT_L2_HDR\t0x41
\t{
\t\tsize_t opc_off = FMAN_EHASH_FLOW_KEY_OFF + ALIGN(key_size, sizeof(u32));
\t\tsize_t param_off = opc_off + 16;\t/* MAX_OPCODES */
\t\tsize_t enqueue_off;
\t\tu16 flags = stats ? 0x3000 : 0;

\t\tflags |= (u16)(((u16)(opc_off >> 2)) << 6);
\t\tflags |= (u16)((u16)(param_off >> 2) & 0x3f);
\t\t*(__be16 *)(r + 0) = cpu_to_be16(flags);
\t\t*(__be16 *)(r + 2) = cpu_to_be16((u16)((old_native >> 32) & 0xffff));
\t\t*(__be32 *)(r + 4) = cpu_to_be32((u32)(old_native & 0xffffffff));
\t\tmemcpy(r + FMAN_EHASH_FLOW_KEY_OFF, key, key_size);

\t\tif (l2_dst && l2_src && eth_type) {
\t\t\t/* Vendor create_ethernet_hm(): INSERT_L2_HDR parameter is
\t\t\t * first because opcode 0x41 is first. hdrlen=14; the 4-byte
\t\t\t * struct header + 14-byte L2 payload needs two pad bytes, so
\t\t\t * word = 14 | (2 << 29) = 0x4000000e. Param consumes 20B. */
\t\t\tr[opc_off + 0] = FMAN_EHASH_OPC_INSERT_L2_HDR;
\t\t\tr[opc_off + 1] = FMAN_EHASH_OPC_ENQUEUE_PKT;
\t\t\t*(__be32 *)(r + param_off + 0) = cpu_to_be32(0x4000000e);
\t\t\tmemcpy(r + param_off + 4, l2_dst, 6);
\t\t\tmemcpy(r + param_off + 10, l2_src, 6);
\t\t\t*(__be16 *)(r + param_off + 16) = cpu_to_be16(eth_type);
\t\t\tenqueue_off = param_off + 20;\t/* ALIGN(4 + 14, 4) */
\t\t} else {
\t\t\t/* Byte-identical F-197 RX-reinjection terminal. */
\t\t\tr[opc_off] = FMAN_EHASH_OPC_ENQUEUE_PKT;
\t\t\tenqueue_off = param_off;
\t\t}

\t\t/* Vendor en_ehash_enqueue_param (16B), immediately following
\t\t * the preceding opcode's parameters. */
\t\t*(__be16 *)(r + enqueue_off + 0) = cpu_to_be16(1500);
\t\t*(r + enqueue_off + 2) = 0;\t/* hdr_xpnd_sz */
\t\t*(r + enqueue_off + 3) = 0;\t/* bpid; fragmentation is S2 */
\t\t*(__be32 *)(r + enqueue_off + 4) = cpu_to_be32(fqid & 0x00ffffff);
\t\t*(__be32 *)(r + enqueue_off + 8) = cpu_to_be32(0);
\t\t*(__be32 *)(r + enqueue_off + 12) = cpu_to_be32(0);
\t\tparam_end = enqueue_off + 16;
\t}
'''
replace(SRC, "ordered TX action block", old_action, new_action)

# 4. F-175 context pointer starts immediately after the dynamic params.
replace(
    SRC, "F-175 context pointer relocation",
    "\t\tsize_t fe_ptr_off = FMAN_EHASH_FLOW_KEY_OFF +\n"
    "\t\t\t\t     ALIGN(key_size, sizeof(u32)) + 16 + 16;\n",
    "\t\t/* F-198: opcode parameters are dynamic: fallback param_end is\n"
    "\t\t * param_off+16 (unchanged); TX terminal is param_off+20+16. */\n"
    "\t\tsize_t fe_ptr_off = param_end;\n",
)

# 5. Debugfs is deliberately RX-reinjection (no L2 rewrite).
replace(
    SRC, "debugfs add_key caller",
    "\terr = fman_pcd_ehash_add_key(t, key, key_size,\n"
    "\t\t\t\t     (u32)enq_obj->muram_off, (u32)fqid,\n"
    "\t\t\t\t     stats);\t/* F-189(fe-flow-add-stats-pass) */\n",
    "\terr = fman_pcd_ehash_add_key(t, key, key_size,\n"
    "\t\t\t\t     (u32)enq_obj->muram_off, (u32)fqid,\n"
    "\t\t\t\t     stats, NULL, NULL, 0);\n",
)

# 6. Production path selects TX terminal when action->tx_fqid is set.
replace(
    SRC, "flow_add add_key caller",
    "\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n"
    "\t\t\t\t\t\t (u32)enq_obj->muram_off,\n"
    "\t\t\t\t\t\t target_fqid,\n"
    "\t\t\t\t\t\t false /* F-189(genl-stats-false) */);\n",
    "\t\tu32 hit_fqid = action->tx_fqid ? action->tx_fqid : target_fqid;\n"
    "\t\tconst u8 *l2_dst = action->tx_fqid ? action->next_hop_mac : NULL;\n"
    "\t\tconst u8 *l2_src = action->tx_fqid ? action->egress_mac : NULL;\n"
    "\t\tu16 l2_eth = action->tx_fqid ? action->eth_type : 0;\n"
    "\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n"
    "\t\t\t\t\t\t (u32)enq_obj->muram_off, hit_fqid,\n"
    "\t\t\t\t\t\t false /* F-189(genl-stats-false) */,\n"
    "\t\t\t\t\t\t l2_dst, l2_src, l2_eth);\n",
)

print(f"### fman_pcd.c/fman_pcd.h: F-198 complete ({changes} blocks)")

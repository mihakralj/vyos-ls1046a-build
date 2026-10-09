"""F-262 (T-M6-SP4 hardware MTU check, 2026-10-08, revised 2026-10-09):
vendor PREEMPTIVE_CHECKS (0x05) + ENQUEUE MTU context on records whose egress
MTU is below what the ingress port can deliver. Oversize frames are punted
to the host; the hardware never fragments.

Vendor cdx_ehash.c fill_actions() puts 05 PREEMPTIVE_CHECKS_ON_PKT first in
every routed record; seal_preemptive_checks_hm() fills its 8-byte param
en_ehash_preempt_op { u8 mtu_offset (ENQUEUE param - 05 param, bytes),
u8 OpMask (PREEMPT_TX_VALIDATE 0x01 | PREEMPT_DFBIT_HONOR 0x02 for IPv4),
dscp/policer bytes 0 }, and create_enque_hm() writes the ENQUEUE param
{ be16 mtu = route MTU, u8 hdr_xpnd_sz, u8 bpid = fragmentation pool,
be32 fqid, be32 stats word, be32 word2 = MURAM offset of the
cdx_ucode_frag_info_t block }. Live vendor record (.106, 2026-10-04):
05 param '38 03 00..', ENQ mtu 0x05dc, word2 0x00049540.
cdx_init_frag_module() initialises that block to frag_options =
BPID_ENABLE 0x08 | OPT_COUNTER_EN 0x04, counters 0, v6_identification 1.

ASK2 so far emitted no 05, ENQ mtu 1500, bpid 0, word2 0. Without 05 the
mtu field is inert (MTU battery 2026-08-17: 2500-byte frames forwarded
through mtu-1500 records). 05 with word2 0 would point the microcode's
frag-info reads at MURAM 0 (the DMA CAM), so 05 is only ever emitted
together with a real block.

Silicon result (board .185, image 2320, 2026-10-09): with BPID_ENABLE set
the microcode fragments in hardware but emits ONE fragment per oversize
frame (first fragment only, 1514 B on the wire, no tail), so the datagram
is silently lost. With BPID_ENABLE clear (frag_options 0x0004, bpid 0) the
05 check still fires and oversize frames, DF set or clear, are handed to the
host: the kernel fragments DF-clear datagrams (8/8 and a 3000-datagram soak
reassembled, frag counters 0) and sends ICMP frag-needed for DF-set ones
(PMTUD works), with no Err FD and the port RX staying alive. So the vendor
fragmentation pool is NOT created: no pool, bpid 0, BPID_ENABLE clear.

This fixup:
  * extends the owned FE internal-buffer MURAM reservation by a 32-byte
    frag-info block (pcd->fe_frag_off, after the 0216 stats scratch),
    initialised frag_options = OPT_COUNTER_EN, v6_identification 1; same
    refcounted lifetime, so the S1->S0 MURAM baseline still returns to zero;
  * adds u16 egress_mtu to fman_pcd_fe_flow_action and fman_pcd_vlan_params
    (0 = no check). A non-zero value on an L2/TX record makes the emitter
    put 05 first and seal it, and write ENQ mtu = egress_mtu and
    word2 = fe_frag_off. Records with egress_mtu 0 stay byte-identical.

ask.ko sets egress_mtu only when the flowtable route MTU is below the
ingress port MTU (e.g. LAN 1500 -> PPPoE 1492). IPv6 flows that would need
the check stay in software for now (not yet measured on silicon). VSP stays
off (RM 5.12 requires it off when fragmentation is enabled).

Must run after F-261. Idempotent; exits non-zero unless every anchor matches
the expected number of times.
"""

import sys

MARK = "F-262"
PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
HDR = "include/linux/fsl/fman_pcd.h"

EDITS = [
    # -- header: the per-record request --
    (HDR,
     "\tu16  pppoe_sid;\t\t/* F-261: egress PPPoE session, host order */\n};\n",
     "\tu16  pppoe_sid;\t\t/* F-261: egress PPPoE session, host order */\n"
     "\t/* F-262: egress IP MTU for the vendor PREEMPTIVE_CHECKS (05) +\n"
     "\t * ENQUEUE MTU context; oversize frames are punted to the host.\n"
     "\t * 0 = no check (byte-identical record). Only honoured on L2/TX\n"
     "\t * records. */\n"
     "\tu16  egress_mtu;\n"
     "};\n", 1),
    (HDR,
     "\tu16 pppoe_sid;\t\t/* F-261: INSERT_PPPoE_HDR session, host order */\n};\n",
     "\tu16 pppoe_sid;\t\t/* F-261: INSERT_PPPoE_HDR session, host order */\n"
     "\tu16 egress_mtu;\t\t/* F-262: 05 + ENQ mtu/word2; 0 = none */\n"
     "};\n", 1),
    # -- frag-info MURAM block, inside the owned FE reservation --
    (PCD,
     "\tunsigned long fe_stats_off;",
     "\tunsigned long fe_frag_off;\t  /* F-262: frag-info block, 0 if none */\n"
     "\tunsigned long fe_stats_off;", 1),
    (PCD,
     "#define FMAN_EHASH_STATS_SCRATCH_SIZE\t256\n",
     "#define FMAN_EHASH_STATS_SCRATCH_SIZE\t256\n"
     "/* F-262: vendor cdx_ucode_frag_info_t (28 B) after the stats scratch:\n"
     " * be16 frag_options, pad, be32 alloc_buff_failures, v4/v6 frame counters,\n"
     " * v4/v6 fragment counters, v6_identification. */\n"
     "#define FMAN_EHASH_FRAG_INFO_SIZE\t32\n", 1),
    (PCD,
     "\traw_size = FMAN_EHASH_INT_BUF_TOTAL + FMAN_EHASH_STATS_SCRATCH_SIZE +\n",
     "\traw_size = FMAN_EHASH_INT_BUF_TOTAL + FMAN_EHASH_STATS_SCRATCH_SIZE +\n"
     "\t\t   FMAN_EHASH_FRAG_INFO_SIZE +\t/* F-262 */\n", 1),
    (PCD,
     "\tmemset_io(v, 0, FMAN_EHASH_INT_BUF_TOTAL + FMAN_EHASH_STATS_SCRATCH_SIZE);\n",
     "\tmemset_io(v, 0, FMAN_EHASH_INT_BUF_TOTAL + FMAN_EHASH_STATS_SCRATCH_SIZE +\n"
     "\t\t  FMAN_EHASH_FRAG_INFO_SIZE);\n"
     "\t/* F-262: vendor cdx_init_frag_module() minus BPID_ENABLE (0x08):\n"
     "\t * with it the microcode fragments in hardware but emits one\n"
     "\t * fragment per frame (silicon, 2026-10-09). OPT_COUNTER_EN only,\n"
     "\t * so oversize frames reach the host. v6_identification 1. */\n"
     "\tiowrite16be(0x0004, v + FMAN_EHASH_INT_BUF_TOTAL +\n"
     "\t\t    FMAN_EHASH_STATS_SCRATCH_SIZE);\n"
     "\tiowrite32be(1, v + FMAN_EHASH_INT_BUF_TOTAL +\n"
     "\t\t    FMAN_EHASH_STATS_SCRATCH_SIZE + 24);\n", 1),
    (PCD,
     "\tpcd->fe_stats_off = aligned_off + FMAN_EHASH_INT_BUF_TOTAL;\n",
     "\tpcd->fe_stats_off = aligned_off + FMAN_EHASH_INT_BUF_TOTAL;\n"
     "\tpcd->fe_frag_off = pcd->fe_stats_off + FMAN_EHASH_STATS_SCRATCH_SIZE;\n", 1),
    (PCD,
     "\tpcd->fe_stats_off = 0;\n",
     "\tpcd->fe_stats_off = 0;\n"
     "\tpcd->fe_frag_off = 0;\t/* F-262 */\n", 1),
    # -- refuse an MTU check the record cannot carry --
    (PCD,
     "\tif (key_size > FMAN_EHASH_FLOW_KEY_MAX)\n\t\treturn -EINVAL;\n",
     "\tif (key_size > FMAN_EHASH_FLOW_KEY_MAX)\n\t\treturn -EINVAL;\n"
     "\t/* F-262: an MTU check needs the L2/TX terminal and the frag-info\n"
     "\t * block; refuse rather than emit 05 without them. */\n"
     "\tif (vlan && vlan->egress_mtu &&\n"
     "\t    (!(l2_dst && l2_src && eth_type) || !t->pcd->fe_frag_off))\n"
     "\t\treturn -EOPNOTSUPP;\n", 1),
    # -- emitter: 05 first --
    (PCD,
     "\t\tsize_t enqueue_off;\n",
     "\t\tsize_t enqueue_off;\n"
     "\t\tsize_t pre_off = 0;\t/* F-262: 05 param, 0 if no 05 */\n", 1),
    (PCD,
     "\t\t\tbool vlan_pop = vlan && (vlan->flags & FMAN_PCD_VLANF_POP);\n",
     "\t\t\tbool vlan_pop = vlan && (vlan->flags & FMAN_PCD_VLANF_POP);\n"
     "\n"
     "\t\t\t/* F-262: vendor PREEMPTIVE_CHECKS_ON_PKT leads the record;\n"
     "\t\t\t * its 8-byte en_ehash_preempt_op is sealed once the ENQUEUE\n"
     "\t\t\t * param offset is known. vlan_oi counts it, so the NAT\n"
     "\t\t\t * rebuild keeps it. */\n"
     "\t\t\tif (vlan && vlan->egress_mtu) {\n"
     "\t\t\t\tr[opc_off + oi++] = 0x05;\t/* PREEMPTIVE_CHECKS */\n"
     "\t\t\t\tpre_off = param_off;\n"
     "\t\t\t\tparam_off += 8;\n"
     "\t\t\t\tttl_param_off = param_off;\n"
     "\t\t\t}\n", 1),
    # -- emitter: seal 05 + ENQUEUE fragmentation context --
    (PCD,
     "\t\tparam_end = enqueue_off + 16;\n",
     "\t\t/* F-262: vendor seal_preemptive_checks_hm() + create_enque_hm():\n"
     "\t\t * mtu_offset, TX_VALIDATE (| DFBIT_HONOR for IPv4); ENQ mtu =\n"
     "\t\t * egress MTU, bpid 0 (no fragmentation pool), word2 = frag-info\n"
     "\t\t * block. */\n"
     "\t\tif (pre_off) {\n"
     "\t\t\tr[pre_off + 0] = (u8)(enqueue_off - pre_off);\n"
     "\t\t\tr[pre_off + 1] = 0x01 | (eth_type == 0x0800 ? 0x02 : 0);\n"
     "\t\t\t*(__be16 *)(r + enqueue_off + 0) =\n"
     "\t\t\t\tcpu_to_be16(vlan->egress_mtu);\n"
     "\t\t\t*(__be32 *)(r + enqueue_off + 12) =\n"
     "\t\t\t\tcpu_to_be32(t->pcd->fe_frag_off & 0x00ffffff);\n"
     "\t\t}\n"
     "\t\tparam_end = enqueue_off + 16;\n", 1),
    # -- thread the request through the production flow-add path --
    (PCD,
     "\t\t\t.pppoe_sid = action->pppoe_sid,\t/* F-261 */\n",
     "\t\t\t.pppoe_sid = action->pppoe_sid,\t/* F-261 */\n"
     "\t\t\t.egress_mtu = action->egress_mtu,\t/* F-262 */\n", 1),
    (PCD,
     "\t\t\t\t\t\t action->vlan_flags ? &_vlan : NULL);\n",
     "\t\t\t\t\t\t (action->vlan_flags ||\n"
     "\t\t\t\t\t\t  action->egress_mtu) ? &_vlan : NULL);\n", 1),
]

srcs = {}
for path, _, _, _ in EDITS:
    if path not in srcs:
        try:
            with open(path) as f:
                srcs[path] = f.read()
        except FileNotFoundError:
            print(f"### F-262: FATAL: {path} not found")
            sys.exit(1)

if all(MARK in s for s in srcs.values()):
    print("### F-262: already applied")
    sys.exit(0)

for path, old, new, want in EDITS:
    n = srcs[path].count(old)
    if n != want:
        print(f"### F-262: FATAL: anchor in {path} found {n} times (expected {want})")
        sys.exit(1)
    srcs[path] = srcs[path].replace(old, new)

for path, s in srcs.items():
    with open(path, "w") as f:
        f.write(s)
print("### F-262: ehash PREEMPTIVE_CHECKS (05) + frag-info MURAM behind egress_mtu")

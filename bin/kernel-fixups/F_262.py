"""F-262 (T-M6-SP4 hardware MTU check, 2026-10-08): vendor PREEMPTIVE_CHECKS
(0x05) + ENQUEUE fragmentation context on records whose egress MTU is below
what the ingress port can deliver.

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
BPID_ENABLE 0x08 | OPT_COUNTER_EN 0x04 (DF action 0x00 = error), counters 0,
v6_identification 1; cdx_create_fragment_bufpool() seeds a dedicated BMan
pool (2048 buffers).

ASK2 so far emitted no 05, ENQ mtu 1500, bpid 0, word2 0. Without 05 the
mtu field is inert (MTU battery 2026-08-17: 2500-byte frames forwarded
through mtu-1500 records). 05 with word2 0 would point the microcode's
frag-info reads at MURAM 0 (the DMA CAM), so 05 is only ever emitted
together with a real block.

This fixup:
  * extends the owned FE internal-buffer MURAM reservation by a 32-byte
    frag-info block (pcd->fe_frag_off, after the 0216 stats scratch),
    initialised like the vendor's; same refcounted lifetime, so the S1->S0
    MURAM baseline still returns to zero;
  * adds fman_pcd_frag_pool_bpid(): a dedicated BMan pool of 2048 x 2 KiB
    DMA-mapped buffers, created on the first flow that needs the check and
    never freed (hardware may hold its buffers at any time);
  * adds u16 egress_mtu to fman_pcd_fe_flow_action and fman_pcd_vlan_params
    (0 = no check). A non-zero value on an L2/TX record makes the emitter
    put 05 first and seal it, and write ENQ mtu = egress_mtu, bpid = frag
    pool, word2 = fe_frag_off. Records with egress_mtu 0 stay byte-identical.

ask.ko sets egress_mtu only when the flowtable route MTU is below the
ingress port MTU (e.g. LAN 1500 -> PPPoE 1492), and keeps IPv6 flows that
would need it in software (routers do not fragment IPv6). VSP stays off
(RM 5.12: required when fragmentation is enabled; mainline never enables
it). What the microcode does with an oversize DF frame (DF action 0x00,
"error") is unmeasured: board-test it before relying on PMTUD.

Must run after F-261. Idempotent; exits non-zero unless every anchor matches
the expected number of times.
"""

import sys

MARK = "F-262"
PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
HDR = "include/linux/fsl/fman_pcd.h"

FRAG_POOL = '''
/* F-262: the IP fragmentation buffer pool named by ENQUEUE_PKT's bpid
 * (vendor cdx_create_fragment_bufpool(): a dedicated BMan pool the
 * microcode takes fragment header buffers from; the TX port releases them
 * back). Created on the first record that needs an MTU check and kept for
 * the life of the system: hardware may hold its buffers at any time, so it
 * is never drained or freed. Returns the BPID or a negative errno. */
#define FMAN_FRAG_BUF_SIZE	2048
#define FMAN_FRAG_BUF_COUNT	2048

static int fman_pcd_frag_pool_bpid(struct fman_pcd *pcd)
{
	static DEFINE_MUTEX(frag_lock);
	static struct bman_pool *frag_pool;
	struct device *dev = fman_get_dev(pcd->fman);
	struct bm_buffer bmb[8];
	int i, j, n = 0, seeded = 0, err = 0;

	mutex_lock(&frag_lock);
	if (frag_pool)
		goto out;
	frag_pool = bman_new_pool();
	if (!frag_pool) {
		err = -ENODEV;
		goto out;
	}
	for (i = 0; !err &&
	     i < FMAN_FRAG_BUF_COUNT * FMAN_FRAG_BUF_SIZE / PAGE_SIZE; i++) {
		struct page *p = alloc_page(GFP_KERNEL);
		dma_addr_t a;

		if (!p) {
			err = -ENOMEM;
			break;
		}
		a = dma_map_page(dev, p, 0, PAGE_SIZE, DMA_BIDIRECTIONAL);
		if (dma_mapping_error(dev, a)) {
			__free_page(p);
			err = -ENOMEM;
			break;
		}
		for (j = 0; !err && j < PAGE_SIZE / FMAN_FRAG_BUF_SIZE; j++) {
			bm_buffer_set64(&bmb[n++], a + j * FMAN_FRAG_BUF_SIZE);
			if (n == ARRAY_SIZE(bmb)) {
				err = bman_release(frag_pool, bmb, n);
				seeded += err ? 0 : n;
				n = 0;
			}
		}
	}
	if (!err && n) {
		err = bman_release(frag_pool, bmb, n);
		seeded += err ? 0 : n;
	}
	if (!seeded) {
		/* Nothing reached hardware: safe to give the BPID back. */
		bman_free_pool(frag_pool);
		frag_pool = NULL;
		err = err ? err : -ENOMEM;
		goto out;
	}
	if (err)
		pr_warn("fman_pcd: F-262 frag pool short: %d of %d buffers (%d)\\n",
			seeded, FMAN_FRAG_BUF_COUNT, err);
	err = 0;
	pr_info("fman_pcd: F-262 frag pool bpid %d, %d x %d B, frag info @MURAM 0x%lx\\n",
		bman_get_bpid(frag_pool), seeded, FMAN_FRAG_BUF_SIZE,
		pcd->fe_frag_off);
out:
	if (!err)
		err = bman_get_bpid(frag_pool);
	mutex_unlock(&frag_lock);
	return err;
}

'''

EDITS = [
    # -- header: the per-record request --
    (HDR,
     "\tu16  pppoe_sid;\t\t/* F-261: egress PPPoE session, host order */\n};\n",
     "\tu16  pppoe_sid;\t\t/* F-261: egress PPPoE session, host order */\n"
     "\t/* F-262: egress IP MTU for the vendor PREEMPTIVE_CHECKS (05) +\n"
     "\t * ENQUEUE fragmentation context; 0 = no check (byte-identical\n"
     "\t * record). Only honoured on L2/TX records. */\n"
     "\tu16  egress_mtu;\n"
     "};\n", 1),
    (HDR,
     "\tu16 pppoe_sid;\t\t/* F-261: INSERT_PPPoE_HDR session, host order */\n};\n",
     "\tu16 pppoe_sid;\t\t/* F-261: INSERT_PPPoE_HDR session, host order */\n"
     "\tu16 egress_mtu;\t\t/* F-262: 05 + ENQ mtu/bpid/word2; 0 = none */\n"
     "};\n", 1),
    # -- frag-info MURAM block, inside the owned FE reservation --
    (PCD,
     "#include <linux/fsl/fman_pcd.h>\n",
     "#include <linux/fsl/fman_pcd.h>\n"
     "#include <soc/fsl/bman.h>\t/* F-262: fragmentation buffer pool */\n", 1),
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
     "\t/* F-262: vendor cdx_init_frag_module(): BPID_ENABLE | OPT_COUNTER_EN,\n"
     "\t * DF action 0 (error), counters 0, v6_identification 1. */\n"
     "\tiowrite16be(0x0008 | 0x0004, v + FMAN_EHASH_INT_BUF_TOTAL +\n"
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
    # -- frag pool, ahead of the emitter --
    (PCD,
     "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
     "\t\t\t\t  const u8 *key, u8 key_size,\n"
     "\t\t\t\t  u32 enq_off, u32 fqid, bool stats,\n"
     "\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,\n"
     "\t\t\t\t  u16 eth_type,\n"
     "\t\t\t\t  const struct fman_pcd_nat_params *nat,\n"
     "\t\t\t\t  const struct fman_pcd_vlan_params *vlan)\n{\n",
     FRAG_POOL +
     "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
     "\t\t\t\t  const u8 *key, u8 key_size,\n"
     "\t\t\t\t  u32 enq_off, u32 fqid, bool stats,\n"
     "\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,\n"
     "\t\t\t\t  u16 eth_type,\n"
     "\t\t\t\t  const struct fman_pcd_nat_params *nat,\n"
     "\t\t\t\t  const struct fman_pcd_vlan_params *vlan)\n{\n"
     "\tint frag_bpid = 0;\t/* F-262 */\n", 1),
    # -- resolve the pool before anything is allocated --
    (PCD,
     "\tif (key_size > FMAN_EHASH_FLOW_KEY_MAX)\n\t\treturn -EINVAL;\n",
     "\tif (key_size > FMAN_EHASH_FLOW_KEY_MAX)\n\t\treturn -EINVAL;\n"
     "\t/* F-262: an MTU check needs the L2/TX terminal, the frag-info block\n"
     "\t * and the frag pool; refuse rather than emit 05 without them. */\n"
     "\tif (vlan && vlan->egress_mtu) {\n"
     "\t\tif (!(l2_dst && l2_src && eth_type) || !t->pcd->fe_frag_off)\n"
     "\t\t\treturn -EOPNOTSUPP;\n"
     "\t\tfrag_bpid = fman_pcd_frag_pool_bpid(t->pcd);\n"
     "\t\tif (frag_bpid < 0)\n"
     "\t\t\treturn frag_bpid;\n"
     "\t}\n", 1),
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
     "\t\t * egress MTU, bpid = frag pool, word2 = frag-info block. */\n"
     "\t\tif (pre_off) {\n"
     "\t\t\tr[pre_off + 0] = (u8)(enqueue_off - pre_off);\n"
     "\t\t\tr[pre_off + 1] = 0x01 | (eth_type == 0x0800 ? 0x02 : 0);\n"
     "\t\t\t*(__be16 *)(r + enqueue_off + 0) =\n"
     "\t\t\t\tcpu_to_be16(vlan->egress_mtu);\n"
     "\t\t\t*(r + enqueue_off + 3) = (u8)frag_bpid;\n"
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
print("### F-262: ehash PREEMPTIVE_CHECKS (05) + frag pool/MURAM behind egress_mtu")

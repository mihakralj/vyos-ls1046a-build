"""F-228 (2026-08-22): key-addressed FE/ehash per-flow stats getter.

T-M8-3 per-flow counter population. The FMan FE engine writes packet_count
(record+256) and packet_bytes (record+264) into every 320-byte ehash flow
record on this 210.10.1 silicon, independent of the per-entry STATS_EN flag
(live-confirmed on image 0031: production records carry 7M-49M pkts / GBs even
though genl inserts pass stats=false). `fe_ehash_stats` already reads these,
but only positionally (per table, no key/cookie), so ask.ko's `dump-flows`
per-flow `packets`/`bytes` fields read 0.

This fixup adds a READ-ONLY, key-addressed getter that ask.ko can call with a
flow's 46-byte dual-lane key to pull that flow's silicon counters:

    int fman_pcd_fe_flow_get_stats(struct fman *fm, u8 hw_port_id,
                                   const u8 *key, u8 key_size,
                                   u64 *packets, u64 *bytes, u32 *timestamp);

It mirrors fman_pcd_fe_flow_del() VERBATIM for table resolution and locking
(same pcd->fe_lock the F-202 panic fix requires; same key_size==38 -> table 1
else per-port table branch) and fman_pcd_ehash_del_key()'s memcmp key scan,
then reads the counters with a dma_rmb() before the be64/be32 loads (the FE
engine is a concurrent DMA writer). No record mutation, no unlink, no insert-
path change: the shipped HIT datapath is untouched.

Returns 0 on match (outputs filled), -ENOENT if no record matches the key,
-ENODEV/-EINVAL on bad args. Timestamp is 0 in production (TIMESTAMP_EN and its
MURAM pool are not implemented) but plumbed for completeness.

Count-gated, idempotent (marker "F-228"); hard-fail on source drift.
"""

import sys

FMAN_PCD_C = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
FMAN_PCD_H = "include/linux/fsl/fman_pcd.h"

# ---- 1. Insert the getter after fman_pcd_fe_flow_del's EXPORT line. ---------
C_ANCHOR = "EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_del);\n"

C_GETTER = '''
/* F-228 (T-M8-3): read one flow's silicon packet/byte counters by key.
 *
 * The FE engine writes packet_count@record+256 and packet_bytes@record+264
 * (big-endian) into every 320-byte ehash record; timestamp@+272 stays 0 in
 * production (TIMESTAMP_EN + MURAM ts pool not implemented). This getter
 * resolves the same per-port table fman_pcd_fe_flow_del() uses, scans the
 * collision chain for @key exactly like fman_pcd_ehash_del_key(), and reads
 * the counters under pcd->fe_lock with a dma_rmb() before the loads (the FE
 * engine is a concurrent DMA writer). Read-only: no record mutation.
 */
int fman_pcd_fe_flow_get_stats(struct fman *fm, u8 hw_port_id,
			       const u8 *key, u8 key_size,
			       u64 *packets, u64 *bytes, u32 *timestamp)
{
	struct fman_pcd *pcd = fman_get_pcd(fm);
	struct fman_pcd_ehash_table *t;
	struct fman_pcd_ehash_flow *f, *hit = NULL;
	int rc = -ENOENT;

	if (!pcd || !key || key_size == 0)
		return -EINVAL;

	mutex_lock(&pcd->fe_lock);
	/* Same table selection as fman_pcd_fe_flow_del(): v6 (38-byte key) ->
	 * global table 1; everything else -> this ingress port's table. */
	if (key_size == 38)
		t = fman_pcd_ehash_table_by_index(pcd, 1);
	else
		t = fman_pcd_ehash_table_for_port(pcd, hw_port_id);
	if (!t) {
		mutex_unlock(&pcd->fe_lock);
		return -ENODEV;
	}

	list_for_each_entry(f, &t->flows, node) {
		if (f->key_size == key_size &&
		    !memcmp((u8 *)f->record + FMAN_EHASH_FLOW_KEY_OFF,
			    key, key_size)) {
			hit = f;
			break;
		}
	}

	if (hit) {
		const u8 *r = hit->record;

		/* Order the counter loads after the DMA writer's stores. */
		dma_rmb();
		if (packets)
			*packets = be64_to_cpu(*(const __be64 *)(r + 256));
		if (bytes)
			*bytes = be64_to_cpu(*(const __be64 *)(r + 264));
		if (timestamp)
			*timestamp = be32_to_cpu(*(const __be32 *)(r + 272));
		rc = 0;
	}

	mutex_unlock(&pcd->fe_lock);
	return rc;
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_get_stats);
'''

# ---- 2. Declare it in the public header after fman_pcd_fe_flow_del. ---------
H_ANCHOR = (
    "int fman_pcd_fe_flow_del(struct fman *fm, u8 hw_port_id,\n"
    "\t\t\t const u8 *key, u8 key_size);\n"
)
H_DECL = (
    "int fman_pcd_fe_flow_del(struct fman *fm, u8 hw_port_id,\n"
    "\t\t\t const u8 *key, u8 key_size);\n"
    "/* F-228 (T-M8-3): read one flow's silicon packet/byte counters by key. */\n"
    "int fman_pcd_fe_flow_get_stats(struct fman *fm, u8 hw_port_id,\n"
    "\t\t\t       const u8 *key, u8 key_size,\n"
    "\t\t\t       u64 *packets, u64 *bytes, u32 *timestamp);\n"
)

changed = 0

with open(FMAN_PCD_C) as fh:
    csrc = fh.read()

# Safety: the getter reads packet_count@+256 / packet_bytes@+264 / ts@+272,
# which only exist in the 320-byte extended record (F-176). If F-176 has not
# bumped FMAN_EHASH_FLOW_REC_SIZE to 320 yet, refuse to run rather than emit a
# reader that walks 64 bytes past a 256-byte DMA-coherent allocation.
if "#define FMAN_EHASH_FLOW_REC_SIZE\t320" not in csrc:
    print("### F-228: FATAL: FMAN_EHASH_FLOW_REC_SIZE is not 320 "
          "(F-176 must run before F-228)")
    sys.exit(1)

if "fman_pcd_fe_flow_get_stats" in csrc:
    print("### F-228: getter already present in fman_pcd.c")
else:
    n = csrc.count(C_ANCHOR)
    if n != 1:
        print(f"### F-228: FATAL: fman_pcd_fe_flow_del EXPORT anchor count {n} != 1")
        sys.exit(1)
    csrc = csrc.replace(C_ANCHOR, C_ANCHOR + C_GETTER, 1)
    with open(FMAN_PCD_C, "w") as fh:
        fh.write(csrc)
    changed += 1
    print("### F-228: fman_pcd_fe_flow_get_stats() inserted into fman_pcd.c")

with open(FMAN_PCD_H) as fh:
    hsrc = fh.read()

if "fman_pcd_fe_flow_get_stats" in hsrc:
    print("### F-228: getter already declared in fman_pcd.h")
else:
    n = hsrc.count(H_ANCHOR)
    if n != 1:
        print(f"### F-228: FATAL: fman_pcd.h del decl anchor count {n} != 1")
        sys.exit(1)
    hsrc = hsrc.replace(H_ANCHOR, H_DECL, 1)
    with open(FMAN_PCD_H, "w") as fh:
        fh.write(hsrc)
    changed += 1
    print("### F-228: fman_pcd_fe_flow_get_stats() declared in fman_pcd.h")

if changed == 0:
    print("### F-228: idempotent no-op")

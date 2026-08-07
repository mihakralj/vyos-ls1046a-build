"""F-175: per-flow workspace context block, vendor-correct ENQ (T-M3-R attempt 8).

CONTEXT (2026-08-07): after F-172 (real key+mask), F-173 (write barrier), and
a live register read confirming FMBM_RFPNE/FMBM_RCCB are correctly wired,
every frame -- matching or not -- still converged on the same FQID. A deep
re-read of this project's own qdrant history surfaced a 2026-07-15 finding
("KERNEL PANIC ROOT-CAUSE ANALYSIS", citing ~/ask-ref/ask-kernel-5.4.patch
line numbers) that this project's own arch doc reconstruction is WRONG in
load-bearing places: the real NXP design is a TWO-LAYER machine --

  layer 1: static singleton FEs (EXT_HASH -> MUX -> Transition -> ENQ -> EXIT)
  layer 2: a per-flow 256B context, attached to each ehash flow record,
           auto-loaded by hardware into the frame's transient FE workspace
           on a genuine HIT. MUX/Transition/ENQ read THEIR OWN routing
           decision from this workspace, not from their own static
           descriptor words.

Two concrete divergences from that design, both still live in this branch's
code as of this fixup:

1. ENQ's word1 has always carried a raw FQID (fman_pcd_fe_enq_build():
   `p.nia = fqid`) instead of a genuine NIA action code. The vendor-correct
   encoding -- w0=TYPE|FQID-enable|ws_offset(8), w1=NIA_ENG_BMI|
   NIA_BMI_AC_ENQ_FRAME (0x00500002), w3=exit_off -- was actually tried on
   this exact silicon on 2026-07-16 (F-073B) and got ONE frame through
   (neighbor table showed STALE, not FAILED) before delivery stopped --
   evidence the mechanism is real, not that it's a dead end.

2. The flow record's trailing bytes (after the key) have always carried a
   single 4-byte "next-FE MURAM offset" (fman_pcd_ehash_add_key()). The SDK
   oracle's BuildContextByFE (~line 8954) documents a 5-field workspace
   context block instead: +0 MUX next-FE, +4 Transition next-AD, +8 ENQ
   (rspid<<24)|fqid, +12 ppid<<16, +16 HM pointer. This project's own
   Transition singleton already sets AD_FROM_WS (next-AD taken from
   workspace, not its own descriptor) -- consistent with, and requiring,
   exactly this context block -- but nothing has ever written one. F-073B's
   own fallback attempt (writing an FQID override to a *separate* DDR
   buffer) failed for exactly this reason: ENQ reads its override from the
   live per-frame workspace, not an arbitrary DDR buffer the driver
   pre-populates -- the context block IS the mechanism that gets it there
   (hardware copies it from the matched flow record into the workspace on
   HIT), and this branch never populated it.

Fix: rewrite the flow record's context bytes to the documented 5-field
block, and correct ENQ's own encoding to the board-tested vendor form.
rspid and ppid are both 0 (no storage-profile/policer override in this
branch's design); HM pointer is 0 (no header manipulation chain). Because
`fman_pcd_ehash_add_key()` is shared by both the fe_flow debugfs path and
the ask.ko-facing kernel API (fman_pcd_fe_flow_add(), retyped by F-094),
both call sites are updated so the build stays consistent; the public
`fman_pcd_fe_flow_action` struct's `enq_off` field is reinterpreted as the
target FQID rather than renamed, to avoid a second breaking API/header
change on top of F-094's.

fe_enq's debugfs `build` verb drops its fqid argument -- ENQ is now a plain
singleton (matches MUX/Transition/Exit): `echo build > fe_enq`. fe_flow's
`add` verb keeps its syntax (`add <table_idx> <keyhex> <hex>`), but the
third argument's role changes from an ENQ FE MURAM offset to a target FQID.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

changes = 0


def apply_block(name, old, new):
    global src, changes
    marker = f"F-175: {name}"
    if marker in src:
        print(f"### F-175: {name} already applied")
        return
    if old not in src:
        print(
            f"### F-175: FATAL: expected '{name}' text not found verbatim "
            "-- a prior patch/fixup may not have applied, or source has "
            "drifted. Refusing to guess."
        )
        sys.exit(1)
    src = src.replace(old, new, 1)
    changes += 1
    print(f"### fman_pcd.c: F-175 {name} applied")


# --- 1. New #defines: ENQ workspace-context offset + genuine NIA constant.
old_defs = (
    "/* ENQ FE word1 holds the 24-bit target FQID when FMAN_FE_ENQ_FQID is set. */\n"
    "#define FMAN_FE_ENQ_NIA_MASK\t\t0x00ffffff\n"
)
new_defs = (
    "/* ENQ FE word1 holds the 24-bit target FQID when FMAN_FE_ENQ_FQID is set. */\n"
    "#define FMAN_FE_ENQ_NIA_MASK\t\t0x00ffffff\n"
    "/* F-175: vendor-correct ENQ encoding (board-tested, F-073B, 2026-07-16).\n"
    " * word1 carries a genuine NIA, not a raw FQID; the FQID is read from\n"
    " * the per-frame workspace at ws_offset 8 (FE_ENQUEUE_CONTEXT_OFFSET).\n"
    " */\n"
    "#define FMAN_FE_ENQ_CONTEXT_OFFSET\t8\n"
    "#define FMAN_NIA_BMI_AC_ENQ_FRAME\t0x00500002\n"
)
apply_block("ENQ context-offset/NIA #defines", old_defs, new_defs)

# --- 2. fman_pcd_fe_enq_build(): drop fqid param, vendor-correct encoding.
old_enq_build = '''static int fman_pcd_fe_enq_build(struct fman_pcd *pcd, u32 fqid,
				 unsigned long next_fe_off)
{
	struct muram_info *muram = fman_get_muram(pcd->fman);
	struct fman_pcd_fe_params p;
	struct fman_pcd_fe_obj *obj;

	if (!muram)
		return -ENXIO;
	if (pcd->fe_refcount == 0)
		return -ENXIO;		/* engage the pool first */
	if (fqid & ~FMAN_FE_ENQ_NIA_MASK)
		return -EINVAL;		/* FQIDs are 24-bit */

	obj = list_first_entry_or_null(&pcd->fe_available,
				       struct fman_pcd_fe_obj, node);
	if (!obj)
		return -ENOSPC;

	memset(&p, 0, sizeof(p));
	p.type = FMAN_FE_TYPE_ENQ;
	p.flags = FMAN_FE_ENQ_FQID;
	p.nia = fqid;
	p.next_fe_off = next_fe_off;

	list_del(&obj->node);
	fman_pcd_fe_build(muram, obj->muram_off, &p);
	list_add_tail(&obj->node, &pcd->fe_enq);
	return 0;
}'''
new_enq_build = '''static int fman_pcd_fe_enq_build(struct fman_pcd *pcd)
{
	struct muram_info *muram = fman_get_muram(pcd->fman);
	struct fman_pcd_fe_params p;
	struct fman_pcd_fe_obj *obj;

	if (!muram)
		return -ENXIO;
	if (pcd->fe_refcount == 0)
		return -ENXIO;		/* engage the pool first */
	if (!pcd->fe_exit_off)
		return -ENOENT;	/* F-175: fe_singletons build first */

	obj = list_first_entry_or_null(&pcd->fe_available,
				       struct fman_pcd_fe_obj, node);
	if (!obj)
		return -ENOSPC;

	/* F-175: vendor-correct ENQ (board-tested, F-073B) -- word1 is a
	 * genuine NIA (BMI enqueue action), not a raw FQID; the target FQID
	 * is read from the per-frame workspace at ws+8, populated by
	 * hardware from the matched flow record's context block on a
	 * genuine HIT. ENQ always chains to EXIT (SDK: never terminal).
	 */
	memset(&p, 0, sizeof(p));
	p.type = FMAN_FE_TYPE_ENQ;
	p.ws_offset = FMAN_FE_ENQ_CONTEXT_OFFSET;
	p.flags = FMAN_FE_ENQ_FQID;
	p.nia = FMAN_NIA_BMI_AC_ENQ_FRAME;
	p.next_fe_off = pcd->fe_exit_off;

	list_del(&obj->node);
	fman_pcd_fe_build(muram, obj->muram_off, &p);
	list_add_tail(&obj->node, &pcd->fe_enq);
	return 0;
}'''
apply_block("fe_enq_build vendor encoding", old_enq_build, new_enq_build)

# --- 3. fman_pcd_fe_enq_write(): drop fqid argument, plain build/clear.
old_enq_write = '''static ssize_t fman_pcd_fe_enq_write(struct file *file,
				     const char __user *ubuf,
				     size_t count, loff_t *ppos)
{
	struct seq_file *s = file->private_data;
	struct fman_pcd *pcd = s->private;
	unsigned long next_fe_off;
	unsigned int fqid;
	char buf[48];
	int err;

	if (count == 0 || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\\0';

	mutex_lock(&pcd->fe_lock);
	if (sscanf(buf, "build %x %lx", &fqid, &next_fe_off) == 2) {
		err = fman_pcd_fe_enq_build(pcd, fqid, next_fe_off);
	} else if (sscanf(buf, "build %x", &fqid) == 1) {
		err = fman_pcd_fe_enq_build(pcd, fqid, 0);
	} else if (!strncmp(buf, "clear", 5)) {
		fman_pcd_fe_enq_free(pcd);
		err = 0;
	} else {
		err = -EINVAL;
	}
	mutex_unlock(&pcd->fe_lock);

	return err ? err : count;
}'''
new_enq_write = '''static ssize_t fman_pcd_fe_enq_write(struct file *file,
				     const char __user *ubuf,
				     size_t count, loff_t *ppos)
{
	struct seq_file *s = file->private_data;
	struct fman_pcd *pcd = s->private;
	char buf[48];
	int err;

	if (count == 0 || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\\0';

	mutex_lock(&pcd->fe_lock);
	if (!strncmp(buf, "build", 5)) {
		/* F-175: no fqid argument -- ENQ is a plain singleton now;
		 * the per-flow FQID lives in the flow record's own context
		 * block (see fe_flow), not this shared FE descriptor.
		 */
		err = fman_pcd_fe_enq_build(pcd);
	} else if (!strncmp(buf, "clear", 5)) {
		fman_pcd_fe_enq_free(pcd);
		err = 0;
	} else {
		err = -EINVAL;
	}
	mutex_unlock(&pcd->fe_lock);

	return err ? err : count;
}'''
apply_block("fe_enq_write no-fqid form", old_enq_write, new_enq_write)

# --- 4. fman_pcd_ehash_add_key(): widen signature.
old_sig = (
    "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size,\n"
    "\t\t\t\t  unsigned long enq_fe_off)"
)
new_sig = (
    "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size,\n"
    "\t\t\t\t  u32 mux_off, u32 enq_off, u32 fqid)"
)
apply_block("ehash_add_key signature", old_sig, new_sig)

# --- 5. fman_pcd_ehash_add_key(): context-block write (was single pointer).
old_ctx = (
    "\t/* next-FE pointer (ENQ FE MURAM offset) after the 8-byte-aligned key. */\n"
    "\tfe_ptr_off = FMAN_EHASH_FLOW_KEY_OFF + ((key_size + 7U) & ~7U);\n"
    "\t*(__be32 *)(r + fe_ptr_off) = cpu_to_be32((u32)enq_fe_off);\n"
)
new_ctx = (
    "\t/* F-175: per-flow workspace context block (SDK BuildContextByFE),\n"
    "\t * loaded by hardware into the frame's transient FE workspace on a\n"
    "\t * genuine HIT -- MUX/Transition/ENQ read their routing decision\n"
    "\t * from here (workspace-relative), not from their own static FE\n"
    "\t * descriptor words. Replaces the single next-FE pointer this\n"
    "\t * branch wrote previously.\n"
    "\t */\n"
    "\tfe_ptr_off = FMAN_EHASH_FLOW_KEY_OFF + ((key_size + 7U) & ~7U);\n"
    "\t*(__be32 *)(r + fe_ptr_off + 0)  = cpu_to_be32(mux_off);\t\t/* MUX next-FE */\n"
    "\t*(__be32 *)(r + fe_ptr_off + 4)  = cpu_to_be32(enq_off);\t\t/* Transition next-AD */\n"
    "\t*(__be32 *)(r + fe_ptr_off + 8)  = cpu_to_be32(fqid & 0x00ffffff);\t/* ENQ (rspid<<24)|fqid */\n"
    "\t*(__be32 *)(r + fe_ptr_off + 12) = cpu_to_be32(0);\t\t\t/* ppid<<16 */\n"
    "\t*(__be32 *)(r + fe_ptr_off + 16) = cpu_to_be32(0);\t\t\t/* HM pointer */\n"
)
apply_block("ehash_add_key context-block write", old_ctx, new_ctx)

# --- 6. fman_pcd_fe_flow_write(): look up mux/enq offsets, pass through.
old_flow_write = '''static ssize_t fman_pcd_fe_flow_write(struct file *file,
				      const char __user *ubuf,
				      size_t count, loff_t *ppos)
{
	struct seq_file *s = file->private_data;
	struct fman_pcd *pcd = s->private;
	struct fman_pcd_ehash_table *t;
	unsigned long enq_fe_off = 0;
	unsigned int tbl_idx;
	char buf[160], keytok[2 * FMAN_EHASH_FLOW_KEY_MAX + 2];
	u8 key[FMAN_EHASH_FLOW_KEY_MAX];
	u8 key_size = 0;
	int nf, err;
	const char *k;

	if (count == 0 || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\\0';

	mutex_lock(&pcd->fe_lock);
	if (!strncmp(buf, "clear", 5)) {
		fman_pcd_ehash_flow_clear_all(pcd);
		mutex_unlock(&pcd->fe_lock);
		return count;
	}

	nf = sscanf(buf, "add %u %113s %lx", &tbl_idx, keytok, &enq_fe_off);
	if (nf < 2) {
		mutex_unlock(&pcd->fe_lock);
		return -EINVAL;
	}

	/* Parse the hex key string into bytes. */
	for (k = keytok; k[0] && k[1]; k += 2) {
		int hi = fman_pcd_hexval(k[0]);
		int lo = fman_pcd_hexval(k[1]);

		if (hi < 0 || lo < 0 || key_size >= FMAN_EHASH_FLOW_KEY_MAX) {
			mutex_unlock(&pcd->fe_lock);
			return -EINVAL;
		}
		key[key_size++] = (u8)((hi << 4) | lo);
	}
	if (key_size == 0 || keytok[2 * key_size] != '\\0') {
		mutex_unlock(&pcd->fe_lock);
		return -EINVAL;		/* odd-length or trailing junk */
	}

	t = fman_pcd_ehash_table_by_index(pcd, tbl_idx);
	if (!t) {
		mutex_unlock(&pcd->fe_lock);
		return -ENODEV;
	}

	err = fman_pcd_ehash_add_key(t, key, key_size, enq_fe_off);
	mutex_unlock(&pcd->fe_lock);

	return err ? err : count;
}'''
new_flow_write = '''static ssize_t fman_pcd_fe_flow_write(struct file *file,
				      const char __user *ubuf,
				      size_t count, loff_t *ppos)
{
	struct seq_file *s = file->private_data;
	struct fman_pcd *pcd = s->private;
	struct fman_pcd_ehash_table *t;
	struct fman_pcd_fe_obj *enq_obj;
	unsigned long fqid = 0;
	unsigned int tbl_idx;
	char buf[160], keytok[2 * FMAN_EHASH_FLOW_KEY_MAX + 2];
	u8 key[FMAN_EHASH_FLOW_KEY_MAX];
	u8 key_size = 0;
	int nf, err;
	const char *k;

	if (count == 0 || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\\0';

	mutex_lock(&pcd->fe_lock);
	if (!strncmp(buf, "clear", 5)) {
		fman_pcd_ehash_flow_clear_all(pcd);
		mutex_unlock(&pcd->fe_lock);
		return count;
	}

	/* F-175: 3rd arg is now the target FQID, not an ENQ FE MURAM
	 * offset -- the real per-flow dispatch target lives in the flow
	 * record's own workspace context block (see fman_pcd_ehash_add_key).
	 */
	nf = sscanf(buf, "add %u %113s %lx", &tbl_idx, keytok, &fqid);
	if (nf < 2) {
		mutex_unlock(&pcd->fe_lock);
		return -EINVAL;
	}

	/* Parse the hex key string into bytes. */
	for (k = keytok; k[0] && k[1]; k += 2) {
		int hi = fman_pcd_hexval(k[0]);
		int lo = fman_pcd_hexval(k[1]);

		if (hi < 0 || lo < 0 || key_size >= FMAN_EHASH_FLOW_KEY_MAX) {
			mutex_unlock(&pcd->fe_lock);
			return -EINVAL;
		}
		key[key_size++] = (u8)((hi << 4) | lo);
	}
	if (key_size == 0 || keytok[2 * key_size] != '\\0') {
		mutex_unlock(&pcd->fe_lock);
		return -EINVAL;		/* odd-length or trailing junk */
	}

	t = fman_pcd_ehash_table_by_index(pcd, tbl_idx);
	if (!t) {
		mutex_unlock(&pcd->fe_lock);
		return -ENODEV;
	}

	enq_obj = list_first_entry_or_null(&pcd->fe_enq,
					   struct fman_pcd_fe_obj, node);
	if (!pcd->fe_mux_off || !enq_obj) {
		mutex_unlock(&pcd->fe_lock);
		return -ENOENT;	/* fe_singletons/fe_enq build first */
	}

	err = fman_pcd_ehash_add_key(t, key, key_size,
				     (u32)pcd->fe_mux_off,
				     (u32)enq_obj->muram_off, (u32)fqid);
	mutex_unlock(&pcd->fe_lock);

	return err ? err : count;
}'''
apply_block("fe_flow_write mux/enq lookup", old_flow_write, new_flow_write)

# --- 7. fman_pcd_fe_flow_add() (ask.ko-facing API, F-094's retyped body):
#        same mux/enq lookup so it stays consistent with the new signature.
old_api = '''int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,
			 const struct fman_pcd_fe_flow_action *action)
{
	struct fman_pcd *pcd;
	struct fman_pcd_ehash_table *t;

	if (!fm || !action || action->key_size == 0)
		return -EINVAL;
	pcd = fman_get_pcd(fm);
	if (!pcd)
		return -ENXIO;

	t = fman_pcd_ehash_table_by_index(pcd, 0);
	if (!t)
		return -ENODEV;

	return fman_pcd_ehash_add_key(t, action->key, action->key_size,
				      action->enq_off);
}'''
new_api = '''int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,
			 const struct fman_pcd_fe_flow_action *action)
{
	struct fman_pcd *pcd;
	struct fman_pcd_ehash_table *t;
	struct fman_pcd_fe_obj *enq_obj;

	if (!fm || !action || action->key_size == 0)
		return -EINVAL;
	pcd = fman_get_pcd(fm);
	if (!pcd)
		return -ENXIO;

	t = fman_pcd_ehash_table_by_index(pcd, 0);
	if (!t)
		return -ENODEV;

	/* F-175: action->enq_off is now interpreted as the target FQID;
	 * MUX/ENQ singleton offsets are looked up here, matching the
	 * fe_flow debugfs path (fman_pcd_fe_flow_write).
	 */
	enq_obj = list_first_entry_or_null(&pcd->fe_enq,
					   struct fman_pcd_fe_obj, node);
	if (!pcd->fe_mux_off || !enq_obj)
		return -ENOENT;

	return fman_pcd_ehash_add_key(t, action->key, action->key_size,
				      (u32)pcd->fe_mux_off,
				      (u32)enq_obj->muram_off,
				      (u32)action->enq_off);
}'''
apply_block("fe_flow_add (ask.ko API) mux/enq lookup", old_api, new_api)

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd.c: F-175 {changes} change(s) applied")
else:
    print("### fman_pcd.c: F-175 no changes applied")
    sys.exit(1)

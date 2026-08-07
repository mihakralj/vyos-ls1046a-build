"""F-175: per-flow FE context buffer, vendor-correct ENQ (T-M3-R attempt 8).

CONTEXT (2026-08-07): after F-172 (real key+mask), F-173 (write barrier), and
a live register read confirming FMBM_RFPNE/FMBM_RCCB are correctly wired,
every frame -- matching or not -- still converged on the same FQID. Also
board-confirmed and fixed separately: F-073D's hidden "F-070b: rewire w6 to
ENQ" was clobbering the MISS disposition to point at the same ENQ object the
HIT path used (see F-073D.py).

A deep re-read of this project's own qdrant history surfaced a 2026-07-15
finding ("KERNEL PANIC ROOT-CAUSE ANALYSIS") that this project's own arch
doc reconstruction is WRONG in load-bearing places: the real NXP design is a
TWO-LAYER machine -- static singleton FEs (EXT_HASH -> MUX -> ENQ -> EXIT)
plus a per-flow FE CONTEXT, auto-loaded by hardware into the frame's
transient FE workspace on a genuine HIT, from which MUX/ENQ read their
actual routing decision (not from their own static descriptor words).

An EARLIER fixup (F-057) had already tried something in this direction --
writing 4 bytes trailing the key inline in the ehash flow record -- and
found it corrupts the record ("SDK's en_ehash_entry has NO per-record
next-FE... causing hardware to read garbage and crash"). F-057 was RIGHT
about that specific design, but not for the reason it gave. This fixup
resolves the disagreement by reading the actual SDK oracle source directly
(~/ask-ref/ask/patches/kernel/999-layerscape-ask-kernel_linux_5_4_3_00_0.patch,
the real, non-stub FmPcdCcBuildContextByFE() at line 8954, and
ExternalHashTableAddKey() at line 8442):

  - The FE context is a SEPARATELY allocated 256B buffer (from its own pool,
    "GetFEContext"), never inline trailing bytes in the ehash flow/bucket
    record itself -- confirming F-057's fix was correct for that location.
  - The per-key "result" associated with a match (t_FmExtHashResult: 16B,
    {contex_addr, monitoring_addr}) is a POINTER to that separate context
    buffer, not the context data itself.
  - FmPcdCcBuildContextByFE()'s real (non-stub) body, byte-exact:
      case ENQ:  word[off+0] = (rspid<<24)|fqid;  word[off+4] = ppid<<16;
      case MUX:  word[off+0] = MURAM offset of next FE;
    -- confirming the byte-level encoding this project's own F-175 attempt
    already used was correct; only the *destination* (inline vs a separate
    buffer) was wrong.
  - For a plain ENQ with no header-manipulation/replication (this branch's
    only use case), ExternalHashTableAddKey()'s real flow builds the ENQ FE
    once, writes the FQID into ITS OWN context at FE_ENQUEUE_CONTEXT_OFFSET
    (8), then writes the MUX context (offset 0) pointing DIRECTLY at that
    ENQ FE -- Transition is skipped entirely (it exists only for frame-
    replication/HM chaining). So the minimum needed per flow is 16 bytes:
    {MUX->ENQ offset, (rspid<<24)|fqid, ppid<<16}, not the full 256B/5-field
    block this fixup originally wrote.

Fix: allocate a small (16B), separate DMA-coherent context buffer per flow
(new fman_pcd_ehash_flow fields ctx/ctx_dma, freed alongside the record on
drain), write the 3 fields the SDK's real encoder writes, and store a
pointer to that buffer (not inline context data) at the flow record's
trailing offset -- an 8-byte DMA bus address, matching t_FmExtHashResult's
contex_addr width (this project's original F-057-removed field was only
4 bytes, a MURAM offset, a different width AND a different meaning; that
mismatch may have been part of why it corrupted things). Also corrects
ENQ's own encoding to the board-tested vendor form (word1 = genuine NIA,
not a raw FQID; the vendor encoding was tried on this exact silicon on
2026-07-16, F-073B, and got ONE frame through before stopping).

Because fman_pcd_ehash_add_key() is shared by both the fe_flow debugfs path
and the ask.ko-facing kernel API (fman_pcd_fe_flow_add(), retyped by
F-094), both call sites are updated so the build stays consistent; the
public fman_pcd_fe_flow_action struct's enq_off field is reinterpreted as
the target FQID rather than renamed, to avoid a second breaking API/header
change on top of F-094's.

fe_enq's debugfs build verb drops its fqid argument -- ENQ is now a plain
singleton (matches MUX/Transition/Exit): `echo build > fe_enq`. fe_flow's
add verb keeps its syntax (`add <table_idx> <keyhex> <hex>`), but the third
argument's role changes from an ENQ FE MURAM offset to a target FQID.
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
    "/* F-175: per-flow FE context buffer (SDK: separately allocated, NEVER\n"
    " * inline in the ehash record -- see FmPcdCcBuildContextByFE). Only\n"
    " * MUX (offset 0, ENQ's own MURAM offset) and ENQ (offset 8/12,\n"
    " * (rspid<<24)|fqid and ppid<<16) are needed -- this branch has no\n"
    " * header-manipulation/replication chain, so Transition/HM are unused.\n"
    " */\n"
    "#define FMAN_FE_CTX_SIZE\t\t16\n"
    "#define FMAN_FE_CTX_MUX_OFF\t\t0\n"
    "#define FMAN_FE_CTX_ENQ_OFF\t\t8\n"
)
apply_block("ENQ context-offset/NIA #defines", old_defs, new_defs)

# --- 2. fman_pcd_fe_enq_build(): drop fqid param, vendor-correct encoding.
#        NOTE: F-073D (2026-07-16, already wired in and runs before this
#        fixup) rewrote the next_fe_off line to hardcode pcd->fe_exit_off
#        -- anchor against ITS output, not patch 0127's original text.
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
	p.next_fe_off = pcd->fe_exit_off;	/* F-073D: chain to EXIT(DEALLOCATE) */

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
	 * hardware from the matched flow record's context buffer on a
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
		 * buffer (see fe_flow), not this shared FE descriptor.
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

# --- 4. struct fman_pcd_ehash_flow: add ctx/ctx_dma fields for the new
#        separately-allocated per-flow context buffer.
old_flow_struct_field = (
    "\tdma_addr_t record_dma;\t\t/* bus address of record (bucket head holds it) */\n"
)
new_flow_struct_field = (
    "\tdma_addr_t record_dma;\t\t/* bus address of record (bucket head holds it) */\n"
    "\tvoid *ctx;\t\t\t/* F-175: separate 16B FE context buffer */\n"
    "\tdma_addr_t ctx_dma;\t\t/* F-175: bus address of ctx (stored in record) */\n"
)
apply_block("ehash_flow struct ctx fields", old_flow_struct_field, new_flow_struct_field)

# --- 5. fman_pcd_ehash_add_key(): simplified signature (enq_off, fqid).
old_sig = (
    "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size,\n"
    "\t\t\t\t  unsigned long enq_fe_off)"
)
new_sig = (
    "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size,\n"
    "\t\t\t\t  u32 enq_off, u32 fqid)"
)
apply_block("ehash_add_key signature", old_sig, new_sig)

# --- 6. fman_pcd_ehash_add_key(): allocate the separate context buffer,
#        write it per the SDK's real FmPcdCcBuildContextByFE() encoding,
#        and store a POINTER to it (not inline context data) in the flow
#        record -- this is the fix for F-057's corruption: the record gets
#        an 8-byte DMA address (matching t_FmExtHashResult.contex_addr),
#        not inline next-FE data.
#
#        NOTE: F-057 (already wired in, runs much earlier) REMOVES the old
#        "next-FE pointer" block outright -- comment, computation, AND the
#        `size_t fe_ptr_off;` declaration -- not just the semantics this
#        fixup once assumed were merely stale. Anchoring on what F-057
#        deletes (or the exact blank-line residue its deletion leaves
#        behind) is fragile, so this inserts right after the key memcpy
#        instead (untouched by F-057, unambiguous), and declares its own
#        block-scoped fe_ptr_off rather than relying on the now-deleted
#        outer declaration.
old_memcpy = (
    "\tmemcpy(r + FMAN_EHASH_FLOW_KEY_OFF, key, key_size);\n"
)
new_memcpy_ctx = (
    "\tmemcpy(r + FMAN_EHASH_FLOW_KEY_OFF, key, key_size);\n"
    "\n"
    "\t/* F-175: separate per-flow FE context buffer (SDK\n"
    "\t * FmPcdCcBuildContextByFE, ~line 8954 of the real oracle) --\n"
    "\t * NEVER inline in the ehash record (F-057 correctly found that\n"
    "\t * corrupts it). MUX's context (offset 0) points at the ENQ FE\n"
    "\t * directly (no Transition -- this branch has no HM/replication\n"
    "\t * chain); ENQ's context (offset 8/12) carries (rspid<<24)|fqid\n"
    "\t * and ppid<<16, byte-exact to the SDK's real encoder. rspid and\n"
    "\t * ppid are both 0 in this branch's design.\n"
    "\t */\n"
    "\t{\n"
    "\t\tsize_t fe_ptr_off = FMAN_EHASH_FLOW_KEY_OFF +\n"
    "\t\t\t\t     ((key_size + 7U) & ~7U);\n"
    "\t\tdma_addr_t ctx_dma;\n"
    "\t\tu8 *ctx = dma_alloc_coherent(t->dev, FMAN_FE_CTX_SIZE, &ctx_dma,\n"
    "\t\t\t\t\t     GFP_KERNEL);\n"
    "\n"
    "\t\tif (!ctx) {\n"
    "\t\t\tdma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE, r, rdma);\n"
    "\t\t\tkfree(flow);\n"
    "\t\t\treturn -ENOMEM;\n"
    "\t\t}\n"
    "\t\t*(__be32 *)(ctx + FMAN_FE_CTX_MUX_OFF) = cpu_to_be32(enq_off);\n"
    "\t\t*(__be32 *)(ctx + FMAN_FE_CTX_ENQ_OFF) = cpu_to_be32(fqid & 0x00ffffff);\n"
    "\t\t*(__be32 *)(ctx + FMAN_FE_CTX_ENQ_OFF + 4) = cpu_to_be32(0);\t/* ppid<<16 */\n"
    "\t\tflow->ctx = ctx;\n"
    "\t\tflow->ctx_dma = ctx_dma;\n"
    "\n"
    "\t\t*(__be64 *)(r + fe_ptr_off) = cpu_to_be64((u64)ctx_dma);\n"
    "\t}\n"
)
apply_block("ehash_add_key ctx buffer alloc+write", old_memcpy, new_memcpy_ctx)

# --- 7. fman_pcd_ehash_flow_drain(): free the ctx buffer alongside record.
old_drain = (
    "\t\tdma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,\n"
    "\t\t\t\t  flow->record, flow->record_dma);\n"
    "\t\tkfree(flow);\n"
)
new_drain = (
    "\t\tdma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,\n"
    "\t\t\t\t  flow->record, flow->record_dma);\n"
    "\t\tif (flow->ctx)\t/* F-175 */\n"
    "\t\t\tdma_free_coherent(t->dev, FMAN_FE_CTX_SIZE, flow->ctx,\n"
    "\t\t\t\t\t  flow->ctx_dma);\n"
    "\t\tkfree(flow);\n"
)
apply_block("ehash_flow_drain ctx free", old_drain, new_drain)

# --- 8. fman_pcd_fe_flow_write(): look up the ENQ singleton offset, pass
#        it + the target FQID through. Split into small surgical edits
#        (F-118, already wired in, inserts a "del <keyhex>" verb between
#        the "clear" branch and the "add" sscanf, so anchoring the WHOLE
#        function body is fragile against unrelated insertions like that).

# 8a. Variable declarations: enq_fe_off -> fqid, add enq_obj.
old_flow_decls = (
    "\tstruct fman_pcd_ehash_table *t;\n"
    "\tunsigned long enq_fe_off = 0;\n"
    "\tunsigned int tbl_idx;\n"
)
new_flow_decls = (
    "\tstruct fman_pcd_ehash_table *t;\n"
    "\tstruct fman_pcd_fe_obj *enq_obj;\n"
    "\tunsigned long fqid = 0;\t/* F-175: was an ENQ FE MURAM offset */\n"
    "\tunsigned int tbl_idx;\n"
)
apply_block("fe_flow_write decls (fqid)", old_flow_decls, new_flow_decls)

# 8b. The "add" sscanf: 3rd arg is now the target FQID.
old_flow_scan = (
    '\tnf = sscanf(buf, "add %u %113s %lx", &tbl_idx, keytok, &enq_fe_off);\n'
)
new_flow_scan = (
    "\t/* F-175: 3rd arg is now the target FQID, not an ENQ FE MURAM\n"
    "\t * offset -- the real per-flow dispatch target lives in a separate\n"
    "\t * context buffer (see fman_pcd_ehash_add_key).\n"
    "\t */\n"
    '\tnf = sscanf(buf, "add %u %113s %lx", &tbl_idx, keytok, &fqid);\n'
)
apply_block("fe_flow_write sscanf (fqid)", old_flow_scan, new_flow_scan)

# 8c. Final block: look up the ENQ singleton offset, pass through.
old_flow_tail = (
    "\tt = fman_pcd_ehash_table_by_index(pcd, tbl_idx);\n"
    "\tif (!t) {\n"
    "\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\treturn -ENODEV;\n"
    "\t}\n"
    "\n"
    "\terr = fman_pcd_ehash_add_key(t, key, key_size, enq_fe_off);\n"
    "\tmutex_unlock(&pcd->fe_lock);\n"
    "\n"
    "\treturn err ? err : count;\n"
    "}"
)
new_flow_tail = (
    "\tt = fman_pcd_ehash_table_by_index(pcd, tbl_idx);\n"
    "\tif (!t) {\n"
    "\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\treturn -ENODEV;\n"
    "\t}\n"
    "\n"
    "\tenq_obj = list_first_entry_or_null(&pcd->fe_enq,\n"
    "\t\t\t\t\t   struct fman_pcd_fe_obj, node);\n"
    "\tif (!enq_obj) {\n"
    "\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\treturn -ENOENT;\t/* fe_enq build first */\n"
    "\t}\n"
    "\n"
    "\terr = fman_pcd_ehash_add_key(t, key, key_size,\n"
    "\t\t\t\t     (u32)enq_obj->muram_off, (u32)fqid);\n"
    "\tmutex_unlock(&pcd->fe_lock);\n"
    "\n"
    "\treturn err ? err : count;\n"
    "}"
)
apply_block("fe_flow_write enq lookup", old_flow_tail, new_flow_tail)

# --- 9. fman_pcd_fe_flow_add() (ask.ko-facing API, F-094's retyped body):
#        same enq lookup so it stays consistent with the new signature.
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
	 * the ENQ singleton offset is looked up here, matching the
	 * fe_flow debugfs path (fman_pcd_fe_flow_write).
	 */
	enq_obj = list_first_entry_or_null(&pcd->fe_enq,
					   struct fman_pcd_fe_obj, node);
	if (!enq_obj)
		return -ENOENT;

	return fman_pcd_ehash_add_key(t, action->key, action->key_size,
				      (u32)enq_obj->muram_off,
				      (u32)action->enq_off);
}'''
apply_block("fe_flow_add (ask.ko API) enq lookup", old_api, new_api)

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd.c: F-175 {changes} change(s) applied")
else:
    print("### fman_pcd.c: F-175 no changes applied")
    sys.exit(1)

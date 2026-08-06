"""F-171: fe_group debugfs verb — wire FE_ENTER behind a genuine CONT_LOOKUP
group AD instead of writing FE_ENTER directly to FMBM_RCCB (T-M3-R attempt 5).

CONTEXT (2026-08-06): arch/fman-microcode-210-programming-reference.md
section 7.11 documents the settled dispatch topology (2026-07-16):

    RCCB -> CONT_LOOKUP group AD
       |- numKeys=0 (shipping): every frame -> miss-AD -> kernel FQ
       `- numKeys>0 (HIT): match entry AD -> FE_ENTER (sec 7.7) -> EXT_HASH

FE_ENTER is a *different* 16-byte AD species from the group AD, and per this
documented topology is reached only *indirectly* through the group's match
table -- never written to RCCB directly. Every T-M3-R arm this session
(`fe_arm engage <port> <off!=0> <fqid>`) has instead written the FE_ENTER AD
built by `fe_enter build` DIRECTLY to RCCB -- exactly the deprecated
"RCCB->FE_ENTER direct" topology the RM says was superseded weeks before
this campaign started. Attempts 2-4 independently ruled out FQID choice and
key content (a control experiment with the already-hardware-validated
13-byte key also missed under identical conditions) -- the remaining
untested variable is this AD-species mismatch.

Note: F-158 (2026-08-01) already built and byte-verified an equivalent
group/match/AD-table structure via a *different* tool (`cc_test`) and found
the CC comparator did not dispatch into it -- so this is not guaranteed to
be the fix, but it is the most concrete untested structural gap, and this
fixup sidesteps F-158's own remaining open question (does the CC comparator
read the compare window in EKFC order or some other layout?) entirely: the
match row's mask is programmed ALL-WILDCARD (0x00 on every byte), so the CC
comparator's byte interpretation cannot matter -- any frame reaching this
group AD trivially "matches" regardless of compare-window layout, and
dispatches into the existing, already-built, already-byte-verified FE_ENTER
chain (`pcd->fe_root_ad_off`, built by the existing `fe_enter build` verb --
this fixup does not duplicate that construction, it wraps it).

Adds a new debugfs node `fe_group`:
  echo "build <miss_fqid_hex>" > fe_group   -- builds match table (2 rows,
    64 B: row0 = all-zero key + all-zero/wildcard mask, row1 unused),
    AD table (2 entries, 32 B: ato[0] = verbatim copy of the already-built
    FE_ENTER root AD's 4 words, ato[1] = miss AD word0=miss_fqid), and the
    16-byte CONT_LOOKUP group AD itself (numKeys=1, matchTableAddr,
    adTableAddr, keySize=16 per RM 8.7.4.1 / F-158's board-confirmed
    w2=0x4f000000 decode). Requires `fe_enter build` to have already run
    (pcd->fe_root_ad_off must be set).
  echo "clear" > fe_group                   -- frees all three allocations.
  cat fe_group                              -- shows the built group AD's
    own MURAM offset (pass THIS to `fe_arm engage <port> <this_offset>
    <fqid>` instead of fe_enter's root_ad offset -- fe_arm itself is
    unmodified, it just receives a different offset to write to RCCB).

Purely additive: no existing fe_* verb, struct field, or write path is
modified. Depends on fe_enter already being built; does not touch fe_arm.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

changes = 0

# --- 1. struct fman_pcd: three new offset fields, anchored right after
#        fe_root_ad_off (kept adjacent to the FE_ENTER field it wraps). ---
struct_anchor = "\tunsigned long fe_root_ad_off;\t/* sec.5 FE_ENTER root AD (16 B) */\n"
struct_new = (
    struct_anchor
    + "\tunsigned long fe_group_off;\t/* F-171: CONT_LOOKUP group AD (16 B) */\n"
    + "\tunsigned long fe_group_mto_off;/* F-171: group match table (64 B) */\n"
    + "\tunsigned long fe_group_ato_off;/* F-171: group AD table (32 B) */\n"
)
if "fe_group_off;" in src:
    print("### F-171: struct fields already present")
elif struct_anchor in src:
    src = src.replace(struct_anchor, struct_new, 1)
    changes += 1
    print("### fman_pcd.c: F-171 struct fields added")
else:
    print(
        "### F-171: FATAL: fe_root_ad_off struct field anchor not found "
        "verbatim -- source has drifted. Refusing to guess."
    )
    sys.exit(1)

# --- 2. build/free/show/write/fops block, anchored right before
#        fman_pcd_fe_arm_fops (same stable anchor F-167/F-169 used). ---
anchor1 = "static const struct file_operations fman_pcd_fe_arm_fops = {"

group_block = '''/* ── fe_group: wrap the existing FE_ENTER chain in a genuine
 * CONT_LOOKUP group AD (numKeys=1, all-wildcard match) -- F-171,
 * T-M3-R attempt 5. See arch/fman-microcode-210-programming-reference.md
 * §7.11/§10.5a. Build order: fe_enter build (already exists) must run
 * first; this wraps pcd->fe_root_ad_off, it does not replace it.
 */
#define FMAN_GROUP_MTO_SIZE	64	/* 2 rows x (16B key + 16B mask) */
#define FMAN_GROUP_ATO_SIZE	32	/* 2 entries x 16B */
#define FMAN_GROUP_AD_SIZE	16	/* group AD itself */

static int fman_pcd_fe_group_build(struct fman_pcd *pcd, u32 miss_fqid)
{
	struct muram_info *muram = fman_get_muram(pcd->fman);
	u32 __iomem *fe_ad, *ato, *gro;
	unsigned long mto, ato_off, gro_off;
	unsigned int i;

	if (!muram)
		return -ENXIO;
	if (!pcd->fe_root_ad_off)
		return -ENOENT;		/* fe_enter build must run first */
	if (pcd->fe_group_off)
		return -EEXIST;

	mto = fman_pcd_muram_alloc(pcd, FMAN_GROUP_MTO_SIZE);
	if (IS_ERR_VALUE(mto))
		return (int)mto;

	ato_off = fman_pcd_muram_alloc(pcd, FMAN_GROUP_ATO_SIZE);
	if (IS_ERR_VALUE(ato_off)) {
		fman_pcd_muram_free(pcd, mto, FMAN_GROUP_MTO_SIZE);
		return (int)ato_off;
	}

	gro_off = fman_pcd_muram_alloc(pcd, FMAN_GROUP_AD_SIZE);
	if (IS_ERR_VALUE(gro_off)) {
		fman_pcd_muram_free(pcd, ato_off, FMAN_GROUP_ATO_SIZE);
		fman_pcd_muram_free(pcd, mto, FMAN_GROUP_MTO_SIZE);
		return (int)gro_off;
	}

	/* Match table: row0 = all-zero key + all-zero (wildcard) mask;
	 * row1 unused (correct for numKeys=1, per F-156/F-158). All-zero
	 * mask means every byte is don't-care, so the CC comparator's
	 * compare-window byte layout cannot matter -- any frame reaching
	 * this group AD trivially matches.
	 */
	memset_io((void __iomem *)fman_muram_offset_to_vbase(muram, mto), 0,
		  FMAN_GROUP_MTO_SIZE);

	/* AD table: ato[0] = verbatim copy of the already-built FE_ENTER
	 * root AD (HIT path); ato[1] = miss AD, word0=miss_fqid (F-158's
	 * board-confirmed miss-AD shape).
	 */
	ato = (u32 __iomem *)fman_muram_offset_to_vbase(muram, ato_off);
	fe_ad = (u32 __iomem *)fman_muram_offset_to_vbase(muram,
							   pcd->fe_root_ad_off);
	for (i = 0; i < 4; i++)
		iowrite32be(ioread32be(fe_ad + i), ato + i);
	iowrite32be(miss_fqid, ato + 4);
	iowrite32be(0, ato + 5);
	iowrite32be(0, ato + 6);
	iowrite32be(0, ato + 7);

	/* Group AD itself, RM 8.7.4.1 / §7.11: numKeys=1, matchTableAddr,
	 * adTableAddr, keySize=16 (0x4f000000 = 0x40000000|((16-1)<<24),
	 * matching F-158's board-confirmed decode).
	 */
	gro = (u32 __iomem *)fman_muram_offset_to_vbase(muram, gro_off);
	iowrite32be((1u << 24) | (mto & 0xFFFFFF), gro + 0);
	iowrite32be((u32)(ato_off & 0xFFFFFF), gro + 1);
	iowrite32be(0x4F000000, gro + 2);
	iowrite32be(0, gro + 3);

	pcd->fe_group_mto_off = mto;
	pcd->fe_group_ato_off = ato_off;
	pcd->fe_group_off = gro_off;
	return 0;
}

static void fman_pcd_fe_group_free(struct fman_pcd *pcd)
{
	struct muram_info *muram = fman_get_muram(pcd->fman);

	if (!pcd->fe_group_off)
		return;
	if (muram) {
		memset_io((void __iomem *)
			  fman_muram_offset_to_vbase(muram, pcd->fe_group_off),
			  0, FMAN_GROUP_AD_SIZE);
		memset_io((void __iomem *)
			  fman_muram_offset_to_vbase(muram,
						     pcd->fe_group_ato_off),
			  0, FMAN_GROUP_ATO_SIZE);
		memset_io((void __iomem *)
			  fman_muram_offset_to_vbase(muram,
						     pcd->fe_group_mto_off),
			  0, FMAN_GROUP_MTO_SIZE);
	}
	fman_pcd_muram_free(pcd, pcd->fe_group_off, FMAN_GROUP_AD_SIZE);
	fman_pcd_muram_free(pcd, pcd->fe_group_ato_off, FMAN_GROUP_ATO_SIZE);
	fman_pcd_muram_free(pcd, pcd->fe_group_mto_off, FMAN_GROUP_MTO_SIZE);
	pcd->fe_group_off = 0;
	pcd->fe_group_ato_off = 0;
	pcd->fe_group_mto_off = 0;
}

static int fman_pcd_fe_group_show(struct seq_file *s, void *unused)
{
	struct fman_pcd *pcd = s->private;
	struct muram_info *muram = fman_get_muram(pcd->fman);
	u32 __iomem *ad;
	unsigned int i;

	mutex_lock(&pcd->fe_lock);
	if (!muram || !pcd->fe_group_off) {
		seq_puts(s, "fe_group   (not built)\\n");
		mutex_unlock(&pcd->fe_lock);
		return 0;
	}
	ad = (u32 __iomem *)fman_muram_offset_to_vbase(muram,
						       pcd->fe_group_off);
	seq_printf(s, "group_ad   off=0x%05lx ", pcd->fe_group_off);
	for (i = 0; i < FMAN_GROUP_AD_SIZE / 4; i++)
		seq_printf(s, "%08x ", ioread32be(ad + i));
	seq_printf(s, "\\nmto off=0x%05lx ato off=0x%05lx\\n",
		  pcd->fe_group_mto_off, pcd->fe_group_ato_off);
	seq_puts(s, "usage: echo \\"build <miss_fqid_hex>\\" > fe_group | echo clear > fe_group\\n");
	seq_puts(s, "arm with: fe_arm engage <port> <group_ad_off_above> <fqid>\\n");
	mutex_unlock(&pcd->fe_lock);
	return 0;
}

static int fman_pcd_fe_group_open(struct inode *inode, struct file *file)
{
	return single_open(file, fman_pcd_fe_group_show, inode->i_private);
}

static ssize_t fman_pcd_fe_group_write(struct file *file,
				       const char __user *ubuf,
				       size_t count, loff_t *ppos)
{
	struct seq_file *s = file->private_data;
	struct fman_pcd *pcd = s->private;
	char buf[48];
	unsigned int miss_fqid;
	int err;

	if (!pcd || count == 0 || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\\0';

	mutex_lock(&pcd->fe_lock);
	if (sscanf(buf, "build %x", &miss_fqid) == 1)
		err = fman_pcd_fe_group_build(pcd, miss_fqid);
	else if (!strncmp(buf, "clear", 5)) {
		fman_pcd_fe_group_free(pcd);
		err = 0;
	} else {
		err = -EINVAL;
	}
	mutex_unlock(&pcd->fe_lock);
	return err ? err : count;
}

static const struct file_operations fman_pcd_fe_group_fops = {
	.owner		= THIS_MODULE,
	.open		= fman_pcd_fe_group_open,
	.read		= seq_read,
	.write		= fman_pcd_fe_group_write,
	.llseek		= seq_lseek,
	.release	= single_release,
};


''' + anchor1

if group_block in src:
    print("### F-171: fe_group block already applied")
elif anchor1 in src:
    src = src.replace(anchor1, group_block, 1)
    changes += 1
    print("### fman_pcd.c: F-171 fe_group build/free/show/write/fops inserted")
else:
    print(
        "### F-171: FATAL: fman_pcd_fe_arm_fops anchor not found verbatim "
        "-- source has drifted. Refusing to guess."
    )
    sys.exit(1)

# --- 3. Register the debugfs node, anchored on fe_kg_ekfc's own
#        registration (F-169, the most recently-added neighbor). ---
import re

if 'debugfs_create_file("fe_group"' in src:
    print("### F-171: fe_group debugfs registration already applied")
else:
    pat = re.compile(
        r'([ \t]*)debugfs_create_file\(\s*"fe_kg_ekfc"\s*,\s*0644\s*,'
        r'[\s\S]*?&fman_pcd_fe_kg_ekfc_fops\s*\)\s*;\n'
    )
    m = pat.search(src)
    if not m:
        print(
            "### F-171: FATAL: fe_kg_ekfc debugfs_create_file call not "
            "found (even with a whitespace-tolerant regex) -- F-169 may "
            "not have applied, or source has drifted. Refusing to guess."
        )
        sys.exit(1)
    indent = m.group(1)
    insertion = (
        f'{indent}debugfs_create_file("fe_group", 0644,\n'
        f'{indent}\t\t\t    pcd->debugfs_dir, pcd,\n'
        f'{indent}\t\t\t    &fman_pcd_fe_group_fops);\n'
    )
    src = src[: m.end()] + insertion + src[m.end() :]
    changes += 1
    print("### fman_pcd.c: F-171 fe_group registered in debugfs")

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd.c: F-171 {changes} change(s) applied")
else:
    print("### fman_pcd.c: F-171 no changes applied")
    sys.exit(1)

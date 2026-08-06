"""F-172: extend fe_group to accept an explicit key+mask (T-M3-R attempt 6).

CONTEXT (2026-08-06): F-171's fe_group always wrote an all-wildcard match
row (mask=0x00 on every byte) to sidestep the CC compare-window-layout
question. That test armed cleanly and showed port 0x11/0x17 both healthy,
but a discriminator test proved it does NOT distinguish HIT from MISS --
ALL traffic (including non-matching ICMP) dispatched through the same
delivery path, regardless of the inserted ehash flow.

Deep documentation review found a confound that changes the picture: F-158
(2026-08-01) already built a near-identical group/match/AD-table structure
via a different tool (cc_test) using a REAL key + FULL participate-mask
(0xff on all 13 real key bytes) and got the OPPOSITE symptom -- "always
MISS" (matching-direction frames never dispatched into FE_ENTER at all).
Critically, F-158 ran *before* F-168 (FMFP_EXTC SYNC fix) existed --
F-168 board-confirmedly fixes a real dispatch defect, and F-158's
"decisive negative" never had it applied. Neither F-158's real-key/real-
mask test NOR F-171's wildcard test has ever run WITH F-168's fix present
using a real, non-degenerate match row. This fixup makes that test
possible: extends fe_group_build()/fe_group_write() to accept an explicit
16-byte key and 16-byte mask instead of always defaulting to wildcard.

New write syntax (backward compatible -- omitting key/mask reproduces
F-171's original wildcard behaviour exactly):
  echo "build <miss_fqid_hex>" > fe_group
  echo "build <miss_fqid_hex> <32-hex-char key> <32-hex-char mask>" > fe_group

e.g. to reproduce F-158's exact match row for the 13-byte key
110A63026A0A6302B906AD9CD903-minus-portid (0A63026A0A6302B906AD9CD903,
13 real bytes) padded to 16B with the same mask convention (0xff on
participating bytes, 0x00 on the 3 trailing pad bytes):
  echo "build 300 0a63026a0a6302b906ad9cd903000000 ffffffffffffffffffffffffff000000" > fe_group

Purely additive on top of F-171: no other fe_* verb or struct field
touched.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

changes = 0

# --- 1. Widen fman_pcd_fe_group_build()'s signature + match-table write. ---
old_sig = "static int fman_pcd_fe_group_build(struct fman_pcd *pcd, u32 miss_fqid)\n{"
new_sig = (
    "static int fman_pcd_fe_group_build(struct fman_pcd *pcd, u32 miss_fqid,\n"
    "\t\t\t\t   const u8 *key, const u8 *mask)\n{"
)

old_mto_write = (
    "\t/* Match table: row0 = all-zero key + all-zero (wildcard) mask;\n"
    "\t * row1 unused (correct for numKeys=1, per F-156/F-158). All-zero\n"
    "\t * mask means every byte is don't-care, so the CC comparator's\n"
    "\t * compare-window byte layout cannot matter -- any frame reaching\n"
    "\t * this group AD trivially matches.\n"
    "\t */\n"
    "\tmemset_io((void __iomem *)fman_muram_offset_to_vbase(muram, mto), 0,\n"
    "\t\t  FMAN_GROUP_MTO_SIZE);\n"
)
new_mto_write = (
    "\t/* F-172: match table row0 = caller-supplied key+mask (16B each),\n"
    "\t * or all-zero/wildcard when key/mask are NULL (F-171's original\n"
    "\t * default, preserved). Row1 unused (numKeys=1, per F-156/F-158).\n"
    "\t */\n"
    "\t{\n"
    "\t\tvoid __iomem *mtov = (void __iomem *)\n"
    "\t\t\tfman_muram_offset_to_vbase(muram, mto);\n"
    "\n"
    "\t\tmemset_io(mtov, 0, FMAN_GROUP_MTO_SIZE);\n"
    "\t\tif (key)\n"
    "\t\t\tmemcpy_toio(mtov, key, 16);\n"
    "\t\tif (mask)\n"
    "\t\t\tmemcpy_toio(mtov + 16, mask, 16);\n"
    "\t}\n"
)

if "const u8 *key, const u8 *mask" in src:
    print("### F-172: fe_group_build already widened")
elif old_sig in src and old_mto_write in src:
    src = src.replace(old_sig, new_sig, 1)
    src = src.replace(old_mto_write, new_mto_write, 1)
    changes += 2
    print("### fman_pcd.c: F-172 fe_group_build signature + match-table write widened")
else:
    print(
        "### F-172: FATAL: expected F-171 fe_group_build body not found "
        "verbatim -- F-171 may not have applied, or source has drifted. "
        "Refusing to guess."
    )
    sys.exit(1)

# --- 2. Widen the write handler: parse an optional key+mask pair, add a
#        small local hex parser (self-contained, no kernel hex2bin
#        dependency assumption). ---
old_write = '''static ssize_t fman_pcd_fe_group_write(struct file *file,
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
}'''

new_write = '''/* F-172: minimal self-contained hex parser -- 2*len hex chars -> len
 * bytes. Returns 0 on success, -EINVAL on any non-hex-digit character.
 */
static int fman_pcd_fe_group_hex2bin(const char *hex, u8 *bin, int len)
{
	int i;

	for (i = 0; i < len; i++) {
		int hi = hex_to_bin(hex[2 * i]);
		int lo = hex_to_bin(hex[2 * i + 1]);

		if (hi < 0 || lo < 0)
			return -EINVAL;
		bin[i] = (u8)((hi << 4) | lo);
	}
	return 0;
}

static ssize_t fman_pcd_fe_group_write(struct file *file,
				       const char __user *ubuf,
				       size_t count, loff_t *ppos)
{
	struct seq_file *s = file->private_data;
	struct fman_pcd *pcd = s->private;
	char buf[96];
	char key_hex[33], mask_hex[33];
	u8 key[16], mask[16];
	unsigned int miss_fqid;
	int err, n;

	if (!pcd || count == 0 || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\\0';

	mutex_lock(&pcd->fe_lock);
	n = sscanf(buf, "build %x %32s %32s", &miss_fqid, key_hex, mask_hex);
	if (n == 3) {
		if (strlen(key_hex) != 32 || strlen(mask_hex) != 32) {
			err = -EINVAL;
		} else if (fman_pcd_fe_group_hex2bin(key_hex, key, 16) ||
			   fman_pcd_fe_group_hex2bin(mask_hex, mask, 16)) {
			err = -EINVAL;
		} else {
			err = fman_pcd_fe_group_build(pcd, miss_fqid, key, mask);
		}
	} else if (sscanf(buf, "build %x", &miss_fqid) == 1) {
		err = fman_pcd_fe_group_build(pcd, miss_fqid, NULL, NULL);
	} else if (!strncmp(buf, "clear", 5)) {
		fman_pcd_fe_group_free(pcd);
		err = 0;
	} else {
		err = -EINVAL;
	}
	mutex_unlock(&pcd->fe_lock);
	return err ? err : count;
}'''

if "fman_pcd_fe_group_hex2bin" in src:
    print("### F-172: fe_group_write already widened")
elif old_write in src:
    src = src.replace(old_write, new_write, 1)
    changes += 1
    print("### fman_pcd.c: F-172 fe_group_write widened (key+mask parsing)")
else:
    print(
        "### F-172: FATAL: expected F-171 fe_group_write body not found "
        "verbatim -- F-171 may not have applied, or source has drifted. "
        "Refusing to guess."
    )
    sys.exit(1)

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd.c: F-172 {changes} change(s) applied")
else:
    print("### fman_pcd.c: F-172 no changes applied")
    sys.exit(1)

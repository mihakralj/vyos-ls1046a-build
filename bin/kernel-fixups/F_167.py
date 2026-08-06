"""F-167: standalone FMFP_EXTC SYNC probe (Task #26 follow-up, Option D).

CONTEXT (2026-08-06, DPAA Reference Manual research, RM section
5.12.14.1 "Dynamic Update of Custom Classifier and HM Tables",
documented in arch/fman-microcode-210-programming-reference.md §5.3):
all three RM-documented flows for dynamically updating a live,
FMan-controller-walked table (Direct Access Direct Sync, Direct Access
HC Sync, Full HC) require a SYNC step before the change is safe to
rely on. The register-level flow (no Host Command needed, and this
microcode blob has HC_DISPATCH disabled anyway -- caps=0x17) is:
assert FMFP_EXTC[INV0] (CCSR offset 0x074, MSB bit, value 0x80000000),
poll until hardware clears it, then the update is synchronized.

This branch's fman_pcd_ehash_add_key() (drivers/net/ethernet/freescale/
fman/fman_pcd.c) writes a new bucket-chain head pointer with a single
unsynchronized MMIO/DMA write and never asserts this register at all --
a previously unknown gap, and a concrete candidate for both the ehash
MISS (arch §10.5a) and the AC_CC/FE_ENTER port-wedge (arming dispatch
on port 0x11 hangs the port 100% reproducibly, silent WAIT, zero fault
signature in any DCSR error tap -- matches "a first frame walking into
a structure the FMan controller was never told is ready").

CAVEAT (kept deliberately narrow because of that wedge history): the
RM's §5.12.14.1 protocol is documented for swapping Action Descriptors
in a MURAM AD table specifically. Whether the same register also
governs the external-hash bucket chain (a DDR-resident linked list --
a different structure) is NOT confirmed by the RM text found so far.
Given the port-wedge's cost (every prior bad hypothesis has needed a
full cold power cycle to clear, no soft recovery exists -- see
arch §5.2's fman_resume_stalled_port() dead-end, gated off for FM
majorRev >= 6 which this silicon reports), this fixup does NOT wire a
SYNC assertion into the default fe_flow add path. It only adds a new,
inert-by-default debugfs probe (`fe_extc`) so the register's basic
behavior -- does it accept the write, does hardware clear it, does
touching it alone cause any adverse effect -- can be tested standalone,
WITHOUT engaging any port and WITHOUT going anywhere near the wedge
condition. `cat fe_extc` reads the current register value; `echo sync
> fe_extc` asserts INV0, polls (bounded, 100000 iterations), and
reports the outcome via dev_info/dev_warn (kernel log) plus the
write()'s return value (0 on cleared, -ETIMEDOUT on stuck).

Disposition: debugfs-probe-only, zero change to any existing code path
(fman_pcd_ehash_add_key, fe_arm engage/disengage, and all other fe_*
verbs are byte-for-byte unchanged). Purely additive. Only after this
probe confirms the register is safe and responsive does it make sense
to design a second fixup that actually wires a SYNC assertion into
fman_pcd_ehash_add_key() and/or fe_arm engage.
"""

import sys, os, re

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-167: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# --- 0. fman_pcd.c has no existing udelay()/linux/delay.h user; add the
#        include explicitly (matches fman.c/fman_port.c/fman_dtsec.c
#        convention in this same driver) rather than relying on an
#        unconfirmed transitive cpu_relax()/udelay() availability. ---
anchor0 = "#include <linux/uaccess.h>\n"
new0 = anchor0 + "#include <linux/delay.h>\n"

if "#include <linux/delay.h>" in src:
    print("### F-167: linux/delay.h already included")
elif anchor0 in src:
    src = src.replace(anchor0, new0, 1)
    changes += 1
    print("### F-167: linux/delay.h include added")
else:
    print(
        "### F-167: FATAL: expected '#include <linux/uaccess.h>' anchor "
        "not found verbatim in fman_pcd.c -- source has likely drifted "
        "since this fixup was written. Refusing to guess; fix the anchor "
        "text in F_167.py against the current fman_pcd.c before retrying."
    )
    sys.exit(1)

# --- 1. Insert the fe_extc show/write/fops block, right before fe_arm's fops ---
anchor1 = "static const struct file_operations fman_pcd_fe_arm_fops = {"

probe_block = '''/* ── fe_extc: FMFP_EXTC (FPM External Requests Control, CCSR 0x074)
 * SYNC probe -- F-167, Task #26 follow-up. See arch/fman-microcode-210-
 * programming-reference.md §5.3.4. Standalone: does not touch any
 * existing fe_* code path. `cat fe_extc` reads the live register;
 * `echo sync > fe_extc` asserts INV0 and polls for hardware to clear
 * it, reporting the outcome to the kernel log.
 */
#define FMAN_FPM_EXTC_INV0	0x80000000U
#define FMAN_FPM_EXTC_POLL_MAX	100000U

static int fman_pcd_fe_extc_show(struct seq_file *s, void *unused)
{
	struct fman_pcd *pcd = s->private;
	struct fman *fman = fman_pcd_get_fman(pcd);
	u32 val;

	if (!fman) {
		seq_puts(s, "fman not available\\n");
		return 0;
	}
	val = ioread32be(&fman->fpm_regs->fmfp_extc);
	seq_printf(s, "fmfp_extc: 0x%08x (INV0=%u)\\n", val,
		   !!(val & FMAN_FPM_EXTC_INV0));
	return 0;
}

static int fman_pcd_fe_extc_open(struct inode *inode, struct file *file)
{
	return single_open(file, fman_pcd_fe_extc_show, inode->i_private);
}

static ssize_t fman_pcd_fe_extc_write(struct file *file,
				      const char __user *ubuf,
				      size_t count, loff_t *ppos)
{
	struct seq_file *s = file->private_data;
	struct fman_pcd *pcd = s->private;
	struct fman *fman;
	char buf[16];
	u32 val;
	unsigned int i;

	if (!pcd || count == 0 || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\\0';

	if (strncmp(buf, "sync", 4))
		return -EINVAL;

	fman = fman_pcd_get_fman(pcd);
	if (!fman)
		return -ENODEV;

	val = ioread32be(&fman->fpm_regs->fmfp_extc);
	dev_info(fman_get_dev(pcd->fman),
		 "fe_extc: fmfp_extc before sync = 0x%08x\\n", val);

	iowrite32be(FMAN_FPM_EXTC_INV0, &fman->fpm_regs->fmfp_extc);

	for (i = 0; i < FMAN_FPM_EXTC_POLL_MAX; i++) {
		val = ioread32be(&fman->fpm_regs->fmfp_extc);
		if (!(val & FMAN_FPM_EXTC_INV0))
			break;
		udelay(1);
	}

	if (val & FMAN_FPM_EXTC_INV0) {
		dev_warn(fman_get_dev(pcd->fman),
			 "fe_extc: sync TIMED OUT after %u polls, fmfp_extc=0x%08x\\n",
			 FMAN_FPM_EXTC_POLL_MAX, val);
		return -ETIMEDOUT;
	}

	dev_info(fman_get_dev(pcd->fman),
		 "fe_extc: sync cleared after %u poll(s), fmfp_extc=0x%08x\\n",
		 i, val);
	return count;
}

static const struct file_operations fman_pcd_fe_extc_fops = {
	.owner		= THIS_MODULE,
	.open		= fman_pcd_fe_extc_open,
	.read		= seq_read,
	.write		= fman_pcd_fe_extc_write,
	.llseek		= seq_lseek,
	.release	= single_release,
};


''' + anchor1

if probe_block in src:
    print("### F-167: fe_extc probe block already applied")
elif anchor1 in src:
    src = src.replace(anchor1, probe_block, 1)
    changes += 1
    print("### F-167: fe_extc show/write/fops block inserted")
else:
    print(
        "### F-167: FATAL: expected fman_pcd_fe_arm_fops anchor not found "
        "verbatim in fman_pcd.c -- source has likely drifted since this "
        "fixup was written. Refusing to guess; fix the anchor text in "
        "F_167.py against the current fman_pcd.c before retrying."
    )
    sys.exit(1)

# --- 2. Register the new debugfs node, right after fe_arm's registration.
#        Whitespace-tolerant regex (not a literal string match): earlier
#        drift showed the fe_arm debugfs_create_file call's exact
#        indentation/line-wrap in the real CI-built tree does not match
#        any locally-available snapshot of this file byte-for-byte, even
#        though the surrounding code (F-165's guard, the fe_arm fops
#        struct itself) is otherwise identical. Only the call's shape
#        (name/mode/args/fops-in-order) is load-bearing here. ---
if 'debugfs_create_file("fe_extc"' in src:
    print("### F-167: fe_extc debugfs registration already applied")
else:
    pat = re.compile(
        r'([ \t]*)debugfs_create_file\(\s*"fe_arm"\s*,\s*0600\s*,'
        r'[\s\S]*?&fman_pcd_fe_arm_fops\s*\)\s*;\n'
    )
    m = pat.search(src)
    if not m:
        print(
            "### F-167: FATAL: fe_arm debugfs_create_file call not found "
            "(even with a whitespace-tolerant regex) in fman_pcd.c -- "
            "source has likely drifted structurally, not just in "
            "formatting, since this fixup was written. Refusing to "
            "guess; fix the anchor pattern in F_167.py against the "
            "current fman_pcd.c before retrying."
        )
        sys.exit(1)

    indent = m.group(1)
    insertion = (
        f'{indent}debugfs_create_file("fe_extc", 0644,\n'
        f'{indent}\t\t\t    pcd->debugfs_dir, pcd,\n'
        f'{indent}\t\t\t    &fman_pcd_fe_extc_fops);\n'
    )
    src = src[: m.end()] + insertion + src[m.end() :]
    changes += 1
    print("### F-167: fe_extc registered in fman_pcd debugfs directory")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-167: {changes} change(s) applied")
else:
    print("### F-167: no changes applied")
    sys.exit(1)

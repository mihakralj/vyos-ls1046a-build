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

v2 FIX (2026-08-06, CI run 31060168305): the first version dereferenced
`fman->fpm_regs->fmfp_extc` directly from fman_pcd.c. Compile failed:
"invalid use of undefined type 'struct fman_fpm_regs'" -- that struct
is fully defined only in fman.c; fman.h's `struct fman` merely holds an
opaque `struct fman_fpm_regs __iomem *fpm_regs` pointer, which is
enough to compile fman.c itself but not enough for any other
translation unit to dereference through it. This project already has
the exact same pattern solved for other registers/fields: fman.c
defines small accessor functions (fman_get_dev(), fman_get_pcd(),
fman_get_id() -- each with a comment literally noting "Used by
fman_pcd.c ... [since] fman_pcd cannot walk ... directly"), declared
in fman.h, and fman_pcd.c calls them. fman.c and fman_pcd.c compile
into the same module (fsl_dpaa_fman.o per the driver Makefile), so no
new EXPORT is strictly required for in-module linkage, but this fixup
uses EXPORT_SYMBOL_GPL anyway to match every neighboring accessor's
convention. v2 adds fman_get_fpm_extc()/fman_set_fpm_extc() to
fman.c/fman.h and switches fman_pcd.c's fe_extc probe to call them
instead of touching fpm_regs directly.

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
fman_c = os.path.join(kroot, "fman.c")
fman_h = os.path.join(kroot, "fman.h")

for p in (pcd_c, fman_c, fman_h):
    if not os.path.exists(p):
        print(f"### F-167: {p} not found")
        sys.exit(0)

changes = 0

# =============================================================================
# Part A: fman.h -- declare the two new accessors, right after fman_get_dev's
# declaration (a stable, already-established anchor: this exact function was
# itself added by an earlier project fixup for the same "fman_pcd.c can't see
# opaque fman internals" reason this fixup now hits for fpm_regs).
# =============================================================================
with open(fman_h) as f:
    h_src = f.read()

if "u32 fman_get_fpm_extc(struct fman *fman);" in h_src:
    print("### F-167: fman.h accessor declarations already applied")
else:
    anchor_h = "struct device *fman_get_dev(struct fman *fman);\n"
    if anchor_h not in h_src:
        print(
            "### F-167: FATAL: expected 'struct device *fman_get_dev(...)' "
            "declaration not found verbatim in fman.h -- source has likely "
            "drifted since this fixup was written. Refusing to guess; fix "
            "the anchor text in F_167.py against the current fman.h before "
            "retrying."
        )
        sys.exit(1)
    new_h = anchor_h + (
        "u32 fman_get_fpm_extc(struct fman *fman);\n"
        "void fman_set_fpm_extc(struct fman *fman, u32 val);\n"
    )
    h_src = h_src.replace(anchor_h, new_h, 1)
    changes += 1
    print("### F-167: fman.h accessor declarations added")

# =============================================================================
# Part B: fman.c -- define the two new accessors, right after fman_get_dev's
# EXPORT_SYMBOL_GPL line.
# =============================================================================
with open(fman_c) as f:
    c_src = f.read()

if "u32 fman_get_fpm_extc(struct fman *fman)" in c_src:
    print("### F-167: fman.c accessor definitions already applied")
else:
    anchor_c = "EXPORT_SYMBOL_GPL(fman_get_dev);\n"
    if anchor_c not in c_src:
        print(
            "### F-167: FATAL: expected 'EXPORT_SYMBOL_GPL(fman_get_dev);' "
            "not found verbatim in fman.c -- source has likely drifted "
            "since this fixup was written. Refusing to guess; fix the "
            "anchor text in F_167.py against the current fman.c before "
            "retrying."
        )
        sys.exit(1)
    new_c = anchor_c + (
        "\n"
        "/**\n"
        " * fman_get_fpm_extc\n"
        " * @fman: A pointer to FMan device\n"
        " *\n"
        " * Return: raw FMFP_EXTC (FPM External Requests Control, CCSR\n"
        " *         offset 0x074) register value. struct fman_fpm_regs is\n"
        " *         only fully defined in this file -- fman_pcd.c's fe_extc\n"
        " *         debugfs probe (F-167) cannot dereference fpm_regs\n"
        " *         directly, same reason fman_get_dev() etc exist.\n"
        " */\n"
        "u32 fman_get_fpm_extc(struct fman *fman)\n"
        "{\n"
        "\treturn ioread32be(&fman->fpm_regs->fmfp_extc);\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_get_fpm_extc);\n"
        "\n"
        "/**\n"
        " * fman_set_fpm_extc\n"
        " * @fman: A pointer to FMan device\n"
        " * @val: value to write to FMFP_EXTC\n"
        " *\n"
        " * Raw write to FMFP_EXTC. See fman_get_fpm_extc().\n"
        " */\n"
        "void fman_set_fpm_extc(struct fman *fman, u32 val)\n"
        "{\n"
        "\tiowrite32be(val, &fman->fpm_regs->fmfp_extc);\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_set_fpm_extc);\n"
    )
    c_src = c_src.replace(anchor_c, new_c, 1)
    changes += 1
    print("### F-167: fman.c accessor definitions added")

# =============================================================================
# Part C: fman_pcd.c -- the fe_extc debugfs probe itself, using the new
# accessors instead of dereferencing fpm_regs directly.
# =============================================================================
with open(pcd_c) as f:
    src = f.read()

# --- C0. fman_pcd.c has no existing udelay()/linux/delay.h user; add the
#         include explicitly (matches fman.c/fman_port.c/fman_dtsec.c
#         convention in this same driver) rather than relying on an
#         unconfirmed transitive cpu_relax()/udelay() availability. ---
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

# --- C1. Insert the fe_extc show/write/fops block, right before fe_arm's fops ---
anchor1 = "static const struct file_operations fman_pcd_fe_arm_fops = {"

probe_block = '''/* ── fe_extc: FMFP_EXTC (FPM External Requests Control, CCSR 0x074)
 * SYNC probe -- F-167, Task #26 follow-up. See arch/fman-microcode-210-
 * programming-reference.md §5.3.4. Standalone: does not touch any
 * existing fe_* code path. `cat fe_extc` reads the live register;
 * `echo sync > fe_extc` asserts INV0 and polls for hardware to clear
 * it, reporting the outcome to the kernel log. Uses fman_get_fpm_extc()/
 * fman_set_fpm_extc() (fman.c) rather than dereferencing fpm_regs
 * directly -- struct fman_fpm_regs is opaque outside fman.c.
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
	val = fman_get_fpm_extc(fman);
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

	val = fman_get_fpm_extc(fman);
	dev_info(fman_get_dev(pcd->fman),
		 "fe_extc: fmfp_extc before sync = 0x%08x\\n", val);

	fman_set_fpm_extc(fman, FMAN_FPM_EXTC_INV0);

	for (i = 0; i < FMAN_FPM_EXTC_POLL_MAX; i++) {
		val = fman_get_fpm_extc(fman);
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

# --- C2. Register the new debugfs node, right after fe_arm's registration.
#         Whitespace-tolerant regex (not a literal string match): earlier
#         drift showed the fe_arm debugfs_create_file call's exact
#         indentation/line-wrap in the real CI-built tree does not match
#         any locally-available snapshot of this file byte-for-byte, even
#         though the surrounding code (F-165's guard, the fe_arm fops
#         struct itself) is otherwise identical. Only the call's shape
#         (name/mode/args/fops-in-order) is load-bearing here. ---
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
    with open(fman_h, "w") as f:
        f.write(h_src)
    with open(fman_c, "w") as f:
        f.write(c_src)
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-167: {changes} change(s) applied")
else:
    print("### F-167: no changes applied")
    sys.exit(1)

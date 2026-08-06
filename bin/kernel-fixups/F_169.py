"""F-169: debugfs verb to reconfigure a live KeyGen scheme's EKFC
(Task #26 / T-M3-R attempt 2 follow-up to F-167/F-168).

CONTEXT (2026-08-06): T-M3-R attempt 1 built the full FE-VM/ehash chain,
inserted a real flow with F-163's corrected 14-byte PORT_ID-prefixed key,
and armed via the FE_ENTER-direct debugfs path (`fe_arm engage <port>
<off> <fqid>`, off != 0) -- the port stalled. dmesg showed KeyGen
scheme4's EKFC was still 0x00180006 (its own 12-byte CC-tree format) at
arm time, NOT the 14-byte ehash format (0x801C0006) the inserted flow
actually used. The FE_ENTER-direct arm path never reconfigures KeyGen to
match whatever ehash structure it's pointed at -- so attempt 1 had a
structural key-length mismatch baked in and was not a fair trial of
F-163's key format. See arch/fman-microcode-210-programming-reference.md
section 5.4 for the full writeup.

This fixup adds the missing capability: a debugfs verb to reconfigure an
already-bound KeyGen scheme's EKFC live, so the T-M3-R test harness can
set scheme4 to 0x801C0006 immediately before `fe_arm engage`, closing the
mismatch and giving F-163's key format its first fair test.

WHY NOT REUSE fman_pcd_kg_scheme_set_ekfc() (fman_pcd_kg.c, already
exists, EXPORT_SYMBOL_GPL): reading it closely found a real bug that
explains why it has zero callers anywhere in this codebase. It does:

    scheme->ekfc = ekfc; keygen->schemes[scheme->id].ekfc = ekfc;
    if (scheme->bound) err = keygen_scheme_setup(keygen, scheme->id, true);

-- a SINGLE call to keygen_scheme_setup(..., enable=true). But
keygen_scheme_setup() (fman_keygen.c) explicitly rejects that:

    if (enable && scheme->used) {
        pr_err("The requested Scheme is already used\n");
        return -EINVAL;
    }

-- and `scheme->used` is set true by the FIRST successful enable and
never cleared except by an explicit enable=false call. So this function
would return -EINVAL every single time it's called on a scheme that is
already bound/in-service -- which is the only case anyone would want to
change its EKFC. It also requires a `struct fman_pcd_kg_scheme *`
wrapper object that fman_pcd.c's debugfs code has no way to obtain for
an externally-created scheme (scheme4 is created by ask.ko/cc_test, not
tracked in any list fman_pcd.c can walk).

THE FIX (implemented fresh here, not by patching the broken function):
the correct sequence to change a LIVE scheme's EKFC is disable, mutate,
re-enable:

  1. keygen_scheme_setup(keygen, id, false)  -- writes mode=disabled,
     clears scheme->used, so the next enable=true call is allowed.
  2. keygen->schemes[id].ekfc = new_ekfc     -- mutate only this field;
     every other cached field (match_vector, base_fqid, next_engine,
     etc.) is left exactly as already configured, so keygen_scheme_setup()
     recomputes and rewrites the SAME scheme content with only EKFC
     changed (confirmed by reading its body: `if (scheme->ekfc)
     scheme_regs.kgse_ekfc = scheme->ekfc;` overrides whatever the
     use_hashing branch would otherwise compute).
  3. keygen_scheme_setup(keygen, id, true)   -- re-enable with the new
     EKFC now in the cached state.

This is a full KGAR indirect register write both times (the driver has
no per-field write path for scheme registers -- confirmed by reading
keygen_write_scheme(), which always writes the whole 23-word window),
so there is a brief window where the scheme is disabled. This is
acceptable for a debug-only test harness invoked before `fe_arm engage`
(i.e. before AC_CC dispatch is turned on at all) -- not intended for use
against a scheme carrying live production traffic.

Adds a new debugfs node `fe_kg_ekfc` (mirrors F-167's `fe_extc` in shape:
a small, self-contained, purely additive probe). Usage:
  echo "set <scheme_id_hex> <ekfc_hex>" > fe_kg_ekfc
e.g. `echo "set 4 801c0006" > fe_kg_ekfc` to match F-163's 14-byte
PORT_ID-prefixed key format on scheme4 before arming.

Depends on F-167/F-168 already applied (ci-setup-kernel.sh ordering).
Does not touch fe_arm, fman_pcd_ehash_add_key(), or any existing fe_*
code path -- purely additive, and only fires on an explicit `set` write.
"""

import sys, os, re

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-169: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# --- 1. Include the KeyGen internal header (struct fman_keygen /
#        struct keygen_scheme / keygen_scheme_setup() -- explicitly
#        designed, per its own docstring, to be included by sibling
#        translation units within fsl_dpaa_fman.ko). ---
anchor0 = '#include "fman_port.h"\n'
new0 = anchor0 + '#include "fman_keygen_internal.h"\n'

if '#include "fman_keygen_internal.h"' in src:
    print("### F-169: fman_keygen_internal.h already included")
elif anchor0 in src:
    src = src.replace(anchor0, new0, 1)
    changes += 1
    print("### F-169: fman_keygen_internal.h include added")
else:
    print(
        "### F-169: FATAL: expected '#include \"fman_port.h\"' anchor not "
        "found verbatim in fman_pcd.c -- source has likely drifted since "
        "this fixup was written. Refusing to guess; fix the anchor text "
        "in F_169.py against the current fman_pcd.c before retrying."
    )
    sys.exit(1)

# --- 2. Insert the fe_kg_ekfc show/write/fops block, right before
#        fe_arm's fops (same anchor F-167 used; stable across F-167's
#        own insertion since F-167 leaves this exact line intact
#        immediately after its own new block). ---
anchor1 = "static const struct file_operations fman_pcd_fe_arm_fops = {"

probe_block = '''/* ── fe_kg_ekfc: reconfigure a live KeyGen scheme's EKFC -- F-169,
 * Task #26 / T-M3-R attempt 2. See arch/fman-microcode-210-programming-
 * reference.md §5.4. `echo "set <scheme_id_hex> <ekfc_hex>" >
 * fe_kg_ekfc` disables the scheme, mutates its cached EKFC, and
 * re-enables it -- the two-step dance keygen_scheme_setup() requires
 * for an already-bound scheme (a single enable=true call against an
 * in-use scheme is rejected with -EINVAL, confirmed by reading
 * fman_keygen.c). Intended to be run immediately before `fe_arm
 * engage`, before any traffic dispatch is turned on.
 */
static int fman_pcd_fe_kg_ekfc_show(struct seq_file *s, void *unused)
{
	seq_puts(s, "usage: echo \\"set <scheme_id_hex> <ekfc_hex>\\" > fe_kg_ekfc\\n");
	return 0;
}

static int fman_pcd_fe_kg_ekfc_open(struct inode *inode, struct file *file)
{
	return single_open(file, fman_pcd_fe_kg_ekfc_show, inode->i_private);
}

static int fman_pcd_fe_kg_ekfc_reconfig(struct fman_pcd *pcd, u8 scheme_id,
					u32 ekfc)
{
	struct fman *fman = fman_pcd_get_fman(pcd);
	struct fman_keygen *keygen;
	struct mutex *lock;
	struct keygen_scheme *scheme;
	int err;

	if (!fman || !fman->keygen)
		return -ENXIO;
	if (scheme_id >= FM_KG_MAX_NUM_OF_SCHEMES)
		return -EINVAL;

	keygen = fman->keygen;
	scheme = &keygen->schemes[scheme_id];
	lock = fman_pcd_get_lock(pcd);

	mutex_lock(lock);

	if (!scheme->used) {
		mutex_unlock(lock);
		return -ENODEV;		/* nothing bound here to reconfigure */
	}

	err = keygen_scheme_setup(keygen, scheme_id, false);
	if (err) {
		mutex_unlock(lock);
		return err;
	}

	scheme->ekfc = ekfc;

	err = keygen_scheme_setup(keygen, scheme_id, true);
	mutex_unlock(lock);
	return err;
}

static ssize_t fman_pcd_fe_kg_ekfc_write(struct file *file,
					 const char __user *ubuf,
					 size_t count, loff_t *ppos)
{
	struct seq_file *s = file->private_data;
	struct fman_pcd *pcd = s->private;
	char buf[48];
	unsigned int scheme_id, ekfc;
	int err;

	if (!pcd || count == 0 || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\\0';

	if (sscanf(buf, "set %x %x", &scheme_id, &ekfc) != 2)
		return -EINVAL;

	err = fman_pcd_fe_kg_ekfc_reconfig(pcd, (u8)scheme_id, (u32)ekfc);
	return err ? err : count;
}

static const struct file_operations fman_pcd_fe_kg_ekfc_fops = {
	.owner		= THIS_MODULE,
	.open		= fman_pcd_fe_kg_ekfc_open,
	.read		= seq_read,
	.write		= fman_pcd_fe_kg_ekfc_write,
	.llseek		= seq_lseek,
	.release	= single_release,
};


''' + anchor1

if probe_block in src:
    print("### F-169: fe_kg_ekfc probe block already applied")
elif anchor1 in src:
    src = src.replace(anchor1, probe_block, 1)
    changes += 1
    print("### F-169: fe_kg_ekfc show/write/fops block inserted")
else:
    print(
        "### F-169: FATAL: expected fman_pcd_fe_arm_fops anchor not found "
        "verbatim in fman_pcd.c -- source has likely drifted since this "
        "fixup was written. Refusing to guess; fix the anchor text in "
        "F_169.py against the current fman_pcd.c before retrying."
    )
    sys.exit(1)

# --- 3. Register the new debugfs node. Whitespace-tolerant regex, same
#        lesson learned from F-167 v1's build failure: the real CI-built
#        tree's exact indentation/line-wrap for a multi-line
#        debugfs_create_file() call cannot be trusted to match any
#        locally-available snapshot byte-for-byte this deep into the
#        fixup chain. Anchor on fe_extc's own registration (F-167,
#        the most recently-added neighbor) rather than fe_arm's. ---
if 'debugfs_create_file("fe_kg_ekfc"' in src:
    print("### F-169: fe_kg_ekfc debugfs registration already applied")
else:
    pat = re.compile(
        r'([ \t]*)debugfs_create_file\(\s*"fe_extc"\s*,\s*0644\s*,'
        r'[\s\S]*?&fman_pcd_fe_extc_fops\s*\)\s*;\n'
    )
    m = pat.search(src)
    if not m:
        print(
            "### F-169: FATAL: fe_extc debugfs_create_file call not found "
            "(even with a whitespace-tolerant regex) in fman_pcd.c -- "
            "F-167 may not have applied, or source has drifted "
            "structurally since this fixup was written. Refusing to "
            "guess; fix the anchor pattern in F_169.py against the "
            "current fman_pcd.c before retrying."
        )
        sys.exit(1)

    indent = m.group(1)
    insertion = (
        f'{indent}debugfs_create_file("fe_kg_ekfc", 0644,\n'
        f'{indent}\t\t\t    pcd->debugfs_dir, pcd,\n'
        f'{indent}\t\t\t    &fman_pcd_fe_kg_ekfc_fops);\n'
    )
    src = src[: m.end()] + insertion + src[m.end() :]
    changes += 1
    print("### F-169: fe_kg_ekfc registered in fman_pcd debugfs directory")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-169: {changes} change(s) applied")
else:
    print("### F-169: no changes applied")
    sys.exit(1)

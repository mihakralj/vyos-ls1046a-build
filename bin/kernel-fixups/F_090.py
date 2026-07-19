"""F-090: Wire __fman_pcd_fe_build_vm_chain to debugfs fe_chain node.

Adds a debugfs node `fe_chain` under fman_pcd/<N>/ that accepts:
  - show: displays build status, FE_ENTER AD offset, ehash table info
  - write "build": calls __fman_pcd_fe_build_vm_chain(), builds full chain
  - write "destroy": tears down VM chain (reverse order)

This bridges the gap between the existing-but-unwired chain builder
(defined in patch 0158) and the debugfs-interactive HIT gate test.

Disposition: fold-into 0158 (belongs in the patch that defines the builder)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")
changes = 0

if not os.path.exists(pcd_c):
    print("### F-090: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

# ── 1. Add struct fields ──────────────────────────────────────────
struct_anchor = "unsigned long fe_exit_off;"
if struct_anchor not in src:
    print("### F-090: struct anchor 'fe_exit_off' not found")
else:
    new_fields = "unsigned long fe_exit_off;\n\tbool fe_vm_chain_built;\t\t/* F-090: chain built flag */\n\tunsigned long fe_enter_ad_off;\t/* F-090: FE_ENTER root AD MURAM offset */"
    if "fe_vm_chain_built" not in src:
        src = src.replace(struct_anchor, new_fields, 1)
        changes += 1
        print("### F-090: struct fman_pcd: added fe_vm_chain_built + fe_enter_ad_off")

# ── 2. Add forward declarations before fman_pcd_init ────────────────
fwd_anchor = "struct fman_pcd *fman_pcd_init(struct fman *fman)"
if fwd_anchor not in src:
    print("### F-090: fman_pcd_init not found")
elif "fman_pcd_fe_chain_fops;" not in src:
    fwd_block = """/* F-090: forward declarations for debugfs fops defined later in file */
static const struct file_operations fman_pcd_fe_chain_fops;
static const struct file_operations fman_pcd_fe_disengage_full_fops;
static const struct file_operations fman_pcd_fe_buffer_fops;

struct fman_pcd *fman_pcd_init(struct fman *fman)"""
    src = src.replace(fwd_anchor, fwd_block, 1)
    changes += 1
    print("### F-090: added forward declarations before fman_pcd_init")

# ── 3. Add fe_chain debugfs after __fman_pcd_fe_build_vm_chain ────
func_close = "err = fman_pcd_fe_enter_build(pcd, e->muram_off);\n\t\tif (err)\n\t\t\treturn err;\n\t}\n\n\treturn 0;\n}"
if func_close not in src:
    print("### F-090: build_vm_chain func_close not found")
elif "fe_chain_show" not in src:
    debugfs_code = """
/*
 * fe_chain debugfs — build/destroy the FE-VM descriptor chain (F-090).
 * Wires the otherwise-uncallable __fman_pcd_fe_build_vm_chain() to a
 * debugfs write node so the chain can be built interactively at the HIT gate.
 *
 * Verbs:  build  |  destroy
 */
static int fman_pcd_fe_chain_show(struct seq_file *s, void *unused)
{
	struct fman_pcd *pcd = s->private;

	seq_printf(s, "vm_chain_built: %s\\n",
		   pcd->fe_vm_chain_built ? "YES" : "NO");
	if (pcd->fe_vm_chain_built) {
		seq_printf(s, "fe_enter_ad_off: 0x%lx\\n", pcd->fe_enter_ad_off);
		seq_printf(s, "mux_off:         0x%lx\\n", pcd->fe_mux_off);
		seq_printf(s, "exit_off:        0x%lx\\n", pcd->fe_exit_off);
		if (!list_empty(&pcd->fe_ehash_tables)) {
			struct fman_pcd_ehash_table *t =
				list_first_entry(&pcd->fe_ehash_tables,
					struct fman_pcd_ehash_table, node);
			seq_printf(s, "ehash:           mask=0x%x keysize=%u ddr=%pad\\n",
				   t->hash_mask, t->key_size, &t->table_dma);
		}
	} else {
		seq_puts(s, "(chain not built — echo build to construct)\\n");
	}
	return 0;
}

static int fman_pcd_fe_chain_open(struct inode *inode, struct file *file)
{
	return single_open(file, fman_pcd_fe_chain_show, inode->i_private);
}

static ssize_t fman_pcd_fe_chain_write(struct file *file,
				       const char __user *ubuf,
				       size_t count, loff_t *ppos)
{
	struct seq_file *s = file->private_data;
	struct fman_pcd *pcd = s->private;
	char buf[16];
	int err;

	if (count == 0 || count >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\\0';

	mutex_lock(&pcd->fe_lock);

	if (!strncmp(buf, "build", 5)) {
		if (pcd->fe_vm_chain_built) {
			mutex_unlock(&pcd->fe_lock);
			return -EBUSY;
		}
		err = __fman_pcd_fe_build_vm_chain(pcd);
		if (err) {
			mutex_unlock(&pcd->fe_lock);
			pr_err("fman_pcd: fe_chain build failed: %d\\n", err);
			return err;
		}
		pcd->fe_vm_chain_built = true;
		pr_info("fman_pcd: fe_chain built (ehash ready, FE_ENTER at 0x%lx)\\n",
			pcd->fe_enter_ad_off);
	} else if (!strncmp(buf, "destroy", 7)) {
		if (!pcd->fe_vm_chain_built) {
			mutex_unlock(&pcd->fe_lock);
			return -ENOENT;
		}
		fman_pcd_fe_enq_free(pcd);
		fman_pcd_fe_hash_free(pcd);
		fman_pcd_ehash_drain(pcd);
		fman_pcd_fe_singletons_free(pcd);
		fman_pcd_fe_pool_put(pcd);
		pcd->fe_vm_chain_built = false;
		pcd->fe_enter_ad_off = 0;
		pr_info("fman_pcd: fe_chain destroyed\\n");
		err = 0;
	} else {
		mutex_unlock(&pcd->fe_lock);
		return -EINVAL;
	}

	mutex_unlock(&pcd->fe_lock);
	return err ? err : count;
}

static const struct file_operations fman_pcd_fe_chain_fops = {
	.owner		= THIS_MODULE,
	.open		= fman_pcd_fe_chain_open,
	.read		= seq_read,
	.write		= fman_pcd_fe_chain_write,
	.llseek		= seq_lseek,
	.release	= single_release,
};

"""
    src = src.replace(func_close, func_close + debugfs_code, 1)
    changes += 1
    print("### F-090: inserted fe_chain debugfs functions after build_vm_chain")

# ── 4. Register fe_chain debugfs node ───────────────────────────────
dbg_reg_anchor = 'debugfs_create_file("fe_hash_probe", 0600,'
if dbg_reg_anchor not in src:
    print("### F-090: fe_hash_probe debugfs registration not found")
elif "fe_chain" not in src:
    reg_line = 'debugfs_create_file("fe_chain\", 0600,\n\t\t\t\t\t    pcd->debugfs_dir, pcd,\n\t\t\t\t\t    &fman_pcd_fe_chain_fops);\n\t\t\t'
    src = src.replace(dbg_reg_anchor, reg_line + dbg_reg_anchor, 1)
    changes += 1
    print("### F-090: registered fe_chain debugfs node")

# ── 5. Initialize new struct fields ──────────────────────────────────
init_anchor = "pcd->fe_exit_off = 0;"
if init_anchor not in src:
    print("### F-090: fe_exit_off init not found")
elif "fe_vm_chain_built = false" not in src:
    new_init = "pcd->fe_exit_off = 0;\n\tpcd->fe_vm_chain_built = false;\t/* F-090 */\n\tpcd->fe_enter_ad_off = 0;\t\t/* F-090 */"
    src = src.replace(init_anchor, new_init, 1)
    changes += 1
    print("### F-090: initialized fe_vm_chain_built and fe_enter_ad_off")

# ── 6. Capture FE_ENTER AD offset during build ───────────────────────
enter_store_anchor = "err = fman_pcd_fe_enter_build(pcd, e->muram_off);"
if enter_store_anchor not in src:
    print("### F-090: fe_enter_build call not found")
elif "pcd->fe_enter_ad_off" not in src:
    # Insert store AFTER the fe_enter_build call + error check
    old = "err = fman_pcd_fe_enter_build(pcd, e->muram_off);\n\t\tif (err)\n\t\t\treturn err;"
    new = "err = fman_pcd_fe_enter_build(pcd, e->muram_off);\n\t\tif (err)\n\t\t\treturn err;\n\t\tpcd->fe_enter_ad_off = e->muram_off;\t/* F-090: capture for debugfs */"
    src = src.replace(old, new, 1)
    changes += 1
    print("### F-090: captured fe_enter_ad_off in build_vm_chain")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-090: {changes} change(s) applied")
else:
    print("### F-090: no changes applied")

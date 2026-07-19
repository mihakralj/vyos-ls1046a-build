"""F-090: Add fe_vm_chain_built + fe_enter_ad_off struct fields.

These struct fields are REQUIRED by F-092 (production fe_engage/disengage).
The fe_chain debugfs node (originally planned here) is deferred — the
FE-VM chain is now built via fman_pcd_fe_engage() API (F-092), not
interactive debugfs.

Disposition: fold-into 0158 (struct fields belong with the chain builder)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-090: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Add struct fields ──────────────────────────────────────────
struct_anchor = "unsigned long fe_exit_off;"
if struct_anchor not in src:
    print("### F-090: struct anchor 'fe_exit_off' not found")
else:
    new_fields = "unsigned long fe_exit_off;\n\tbool fe_vm_chain_built;\t\t/* F-090/F-092: chain built flag */\n\tunsigned long fe_enter_ad_off;\t/* F-090/F-092: FE_ENTER root AD MURAM offset */"
    if "fe_vm_chain_built" not in src:
        src = src.replace(struct_anchor, new_fields, 1)
        changes += 1
        print("### F-090: struct fman_pcd: added fe_vm_chain_built + fe_enter_ad_off")

# ── 2. Initialize new struct fields ──────────────────────────────────
init_anchor = "pcd->fe_exit_off = 0;"
if init_anchor not in src:
    print("### F-090: fe_exit_off init not found")
elif "fe_vm_chain_built = false" not in src:
    new_init = "pcd->fe_exit_off = 0;\n\tpcd->fe_vm_chain_built = false;\t/* F-090 */\n\tpcd->fe_enter_ad_off = 0;\t\t/* F-090 */"
    src = src.replace(init_anchor, new_init, 1)
    changes += 1
    print("### F-090: initialized fe_vm_chain_built + fe_enter_ad_off")

# ── 3. Add forward declarations before fman_pcd_init ────────────────
fwd_anchor = "struct fman_pcd *fman_pcd_init(struct fman *fman)"
if fwd_anchor not in src:
    print("### F-090: fman_pcd_init not found")
elif "fman_pcd_fe_chain_fops;" not in src and "fman_pcd_fe_disengage_full_fops;" not in src:
    fwd_block = "static const struct file_operations fman_pcd_fe_chain_fops;\nstatic const struct file_operations fman_pcd_fe_disengage_full_fops;\nstatic const struct file_operations fman_pcd_fe_buffer_fops;\n\nstruct fman_pcd *fman_pcd_init(struct fman *fman)"
    src = src.replace(fwd_anchor, fwd_block, 1)
    changes += 1
    print("### F-090: added forward declarations before fman_pcd_init")

# NOTE: fe_chain debugfs node, fe_hash_probe registration, and
# fe_enter_ad_off capture are deferred — chain is now built via
# fman_pcd_fe_engage() API (F-092) and debugfs is diagnostic only.

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-090: {changes} change(s) applied")
else:
    print("### F-090: no changes applied")

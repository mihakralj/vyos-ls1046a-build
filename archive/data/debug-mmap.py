#!/usr/bin/env python3
"""Add debug logging to usdpaa_mmap and ioctl_portal_map"""

FILE = "/opt/vyos-dev/linux/drivers/soc/fsl/qbman/fsl_usdpaa_mainline.c"

with open(FILE, "r") as f:
    c = f.read()

# 1. Add debug logging in usdpaa_mmap after mutex_lock, before list iteration
old_mmap = """\tmutex_lock(&ctx->lock);
\tlist_for_each_entry(pr, &ctx->portal_maps, node) {"""

new_mmap = """\tmutex_lock(&ctx->lock);
\tpr_warn("usdpaa: mmap lookup pgoff=0x%lx len=0x%zx empty=%d\\n",
\t\tpfn, len, list_empty(&ctx->portal_maps));
\tlist_for_each_entry(pr, &ctx->portal_maps, node) {
\t\tpr_warn("usdpaa:   checking portal phys_ce=0x%llx phys_ci=0x%llx "
\t\t\t"size_ce=0x%zx size_ci=0x%zx\\n",
\t\t\t(u64)pr->phys_ce, (u64)pr->phys_ci,
\t\t\tpr->size_ce, pr->size_ci);"""

if old_mmap in c:
    c = c.replace(old_mmap, new_mmap)
    print("Added mmap debug logging")
else:
    print("WARNING: Could not find mmap target string")

# 2. Add debug logging in ioctl_portal_map after adding to list
old_ioctl = """\t/* Track in global list for IRQ device lookup */"""

new_ioctl = """\tpr_warn("usdpaa: PORTAL_MAP added to ctx list: type=%d "
\t\t"phys_ce=0x%llx phys_ci=0x%llx size_ce=0x%zx size_ci=0x%zx\\n",
\t\tpr->type, (u64)pr->phys_ce, (u64)pr->phys_ci,
\t\tpr->size_ce, pr->size_ci);

\t/* Track in global list for IRQ device lookup */"""

if old_ioctl in c:
    c = c.replace(old_ioctl, new_ioctl, 1)
    print("Added ioctl_portal_map debug logging")
else:
    print("WARNING: Could not find ioctl target string")

with open(FILE, "w") as f:
    f.write(c)

print("Debug patch applied successfully")

"""F-133: Add MURAM allocation tracking to identify disengage residual.

The board shows 8229 B MURAM residual after engage→disengage (baseline 720 B,
post-disengage 8949 B).  This fixup adds a debugfs node `muram_allocations`
that dumps every outstanding allocation with its offset, size, and a caller
label so we can identify what's not being freed.

The tracking uses a simple linked list of {offset, size, label} records.
Every fman_pcd_muram_alloc() call adds a record; every fman_pcd_muram_free()
removes it.  The debugfs node walks the list and prints each entry.

This is a DIAGNOSTIC fixup — it adds overhead and should be removed or
compiled out once the residual is identified and fixed.

Disposition: diagnostic only; delete after root-cause
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK diagnostics]
Risk-Tier: A (additive debugfs node, no datapath change)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-133: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Add the tracking list head to struct fman_pcd ──
# Find the struct definition and add a list_head before the closing brace
# of the muram-related fields
struct_marker = "size_t muram_high_water;"
if struct_marker not in src:
    print("### F-133: muram_high_water not found in struct fman_pcd")
    sys.exit(1)

tracking_field = """
\t/* F-133: MURAM allocation tracking for residual diagnosis */
\tstruct list_head muram_track;
\tstruct mutex muram_track_lock;"""

src = src.replace(struct_marker, struct_marker + tracking_field, 1)
changes += 1
print("### F-133: added muram_track fields to struct fman_pcd")

# ── 2. Add the tracking record struct before fman_pcd_muram_alloc ──
alloc_fn = "unsigned long fman_pcd_muram_alloc(struct fman_pcd *pcd, size_t size)"
if alloc_fn not in src:
    print("### F-133: fman_pcd_muram_alloc not found")
    sys.exit(1)

track_struct = """struct fman_pcd_muram_track {
\tstruct list_head node;
\tunsigned long offset;
\tsize_t size;
\tconst char *label;
};

"""
src = src.replace(alloc_fn, track_struct + alloc_fn, 1)
changes += 1
print("### F-133: added muram_track struct")

# ── 3. Instrument fman_pcd_muram_alloc to record allocations ──
# Find the "return offset;" line in fman_pcd_muram_alloc
alloc_return = "\treturn offset;\n}"
# We need to find this within the alloc function
alloc_start = src.find(alloc_fn)
alloc_body_start = src.index("{", alloc_start)
# Find the closing brace of the function
# Look for the next function after alloc
next_fn = src.find("\nvoid fman_pcd_muram_free", alloc_body_start)
if next_fn == -1:
    next_fn = src.find("\nstatic", alloc_body_start + 100)

alloc_scope = src[alloc_body_start:next_fn]

# Find the return offset line
ret_line = "\treturn offset;"
if ret_line not in alloc_scope:
    print("### F-133: return offset not found in muram_alloc")
    sys.exit(1)

track_alloc = """\t/* F-133: record allocation for residual tracking */
\t{
\t\tstruct fman_pcd_muram_track *t = kmalloc(sizeof(*t), GFP_KERNEL);
\t\tif (t) {
\t\t\tt->offset = offset;
\t\t\tt->size = size;
\t\t\tt->label = "unknown";
\t\t\tmutex_lock(&pcd->muram_track_lock);
\t\t\tlist_add_tail(&t->node, &pcd->muram_track);
\t\t\tmutex_unlock(&pcd->muram_track_lock);
\t\t}
\t}

"""
abs_pos = alloc_body_start + alloc_scope.find(ret_line)
src = src[:abs_pos] + track_alloc + src[abs_pos:]
changes += 1
print("### F-133: instrumented muram_alloc")

# ── 4. Instrument fman_pcd_muram_free to remove records ──
free_fn = "void fman_pcd_muram_free(struct fman_pcd *pcd, unsigned long offset,"
if free_fn not in src:
    print("### F-133: fman_pcd_muram_free not found")
    sys.exit(1)

free_start = src.find(free_fn)
free_body_start = src.index("{", free_start)
# Find end of free function
next_after_free = src.find("\nstatic", free_body_start + 100)
if next_after_free == -1:
    next_after_free = src.find("\nint fman_pcd", free_body_start + 100)
free_scope = src[free_body_start:next_after_free]

# Find the mutex_unlock or the closing brace before the next function
# Insert tracking removal before the final mutex_unlock
unlock_line = "\tmutex_unlock(&pcd->lock);"
if unlock_line not in free_scope:
    print("### F-133: mutex_unlock not found in muram_free")
    sys.exit(1)

track_free = """\t/* F-133: remove allocation record */
\t{
\t\tstruct fman_pcd_muram_track *t, *tmp;
\t\tmutex_lock(&pcd->muram_track_lock);
\t\tlist_for_each_entry_safe(t, tmp, &pcd->muram_track, node) {
\t\t\tif (t->offset == offset) {
\t\t\t\tlist_del(&t->node);
\t\t\t\tkfree(t);
\t\t\t\tbreak;
\t\t\t}
\t\t}
\t\tmutex_unlock(&pcd->muram_track_lock);
\t}

"""
abs_pos = free_body_start + free_scope.find(unlock_line)
src = src[:abs_pos] + track_free + src[abs_pos:]
changes += 1
print("### F-133: instrumented muram_free")

# ── 5. Add debugfs show function and hook it up ──
# Find fman_pcd_init where debugfs nodes are created
init_fn = "struct fman_pcd *fman_pcd_init(struct fman *fman)"
if init_fn not in src:
    print("### F-133: fman_pcd_init not found")
    sys.exit(1)

init_start = src.find(init_fn)

# Add INIT_LIST_HEAD and mutex_init after fe_ehash_tables init
ehash_init = "INIT_LIST_HEAD(&pcd->fe_ehash_tables);"
if ehash_init not in src:
    print("### F-133: fe_ehash_tables init not found")
    sys.exit(1)

track_init = """
\tINIT_LIST_HEAD(&pcd->muram_track);
\tmutex_init(&pcd->muram_track_lock);"""

src = src.replace(ehash_init, ehash_init + track_init, 1)
changes += 1
print("### F-133: added muram_track init")

# Add the debugfs show function before fman_pcd_init
show_fn = """static int fman_pcd_muram_allocations_show(struct seq_file *s, void *unused)
{
\tstruct fman_pcd *pcd = s->private;
\tstruct fman_pcd_muram_track *t;
\tunsigned long total = 0;
\tint n = 0;

\tmutex_lock(&pcd->muram_track_lock);
\tlist_for_each_entry(t, &pcd->muram_track, node) {
\t\tseq_printf(s, "  [%d] off=0x%05lx size=%5zu  %s\\n",
\t\t\t   n++, t->offset, t->size, t->label);
\t\ttotal += t->size;
\t}
\tmutex_unlock(&pcd->muram_track_lock);
\tseq_printf(s, "total: %d allocations, %lu bytes\\n", n, total);
\treturn 0;
}

static int fman_pcd_muram_allocations_open(struct inode *inode, struct file *file)
{
\treturn single_open(file, fman_pcd_muram_allocations_show, inode->i_private);
}

static const struct file_operations fman_pcd_muram_allocations_fops = {
\t.owner\t\t= THIS_MODULE,
\t.open\t\t= fman_pcd_muram_allocations_open,
\t.read\t\t= seq_read,
\t.llseek\t\t= seq_lseek,
\t.release\t= single_release,
};

"""
src = src.replace(init_fn, show_fn + init_fn, 1)
changes += 1
print("### F-133: added muram_allocations debugfs show function")

# Hook the debugfs file into fman_pcd_init
# Find the muram_budget debugfs line and insert after it.
# Use line-level insertion to avoid multi-line call breakage.
hook_marker = 'debugfs_create_file("muram_budget"'
if hook_marker not in src:
    print("### F-133: muram_budget debugfs not found")
    sys.exit(1)

# Find the full statement (may span multiple lines with continuations)
hook_pos = src.find(hook_marker)
# Find the semicolon that ends this statement
stmt_end = src.find(";", hook_pos)
if stmt_end == -1:
    print("### F-133: cannot find end of muram_budget debugfs statement")
    sys.exit(1)
# Include the semicolon and newline
stmt_end = src.find("\n", stmt_end)
if stmt_end == -1:
    stmt_end = len(src)
else:
    stmt_end += 1  # include the newline

new_call = """\t\t\tdebugfs_create_file("muram_allocations", 0444,
\t\t\t\t\t    pcd->debugfs_dir, pcd,
\t\t\t\t\t    &fman_pcd_muram_allocations_fops);
"""

# Insert after the full statement
src = src[:stmt_end] + new_call + src[stmt_end:]
changes += 1
print("### F-133: hooked muram_allocations debugfs file")

# ── 6. Add mutex_destroy in fman_pcd_remove ──
remove_fn = "void fman_pcd_remove(struct fman *fman)"
if remove_fn in src:
    destroy_marker = "mutex_destroy(&pcd->fe_lock);"
    if destroy_marker in src:
        track_destroy = """
\tmutex_destroy(&pcd->muram_track_lock);"""
        src = src.replace(destroy_marker, destroy_marker + track_destroy, 1)
        changes += 1
        print("### F-133: added muram_track_lock destroy")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-133: {changes} change(s) applied")
else:
    print("### F-133: no changes applied")
    sys.exit(1)
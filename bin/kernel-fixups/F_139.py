"""F-139: Move scaffold tracking from singleton (pcd->fe_scaffold_*) to per-port (fp->scaffold_*).

Root cause of 304 B/cycle MURAM leak (board-verified 2026-07-30 on .185, ISO 0406):
pcd->fe_scaffold_gro/mto/ato are singleton variables.  When port 0x10 engages,
they hold 0x10's scaffold offsets.  When port 0x11 engages, they are OVERWRITTEN
with 0x11's offsets.  On disengage, port 0x10 frees 0x11's scaffold (the
last-written values), and port 0x11 finds them already zeroed.  Net: one
scaffold (304 B) leaked per cycle.

Fix: store scaffold offsets in struct fman_pcd_fe_port (per-port), not in
struct fman_pcd (singleton).  The scaffold is freed in fman_pcd_fe_port_del()
alongside the other per-port resources (pool, mgmt).  The singleton variables
and fman_pcd_fe_arm_free_scaffold() are removed.

Must run AFTER 0123 (which adds the fp->scaffold_* fields).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-139: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Replace pcd->fe_scaffold_gro = gro with per-port lookup ──
# In __fman_pcd_fe_arm_engage, replace:
#   pcd->fe_scaffold_gro = gro;
#   pcd->fe_scaffold_mto = mto;
#   pcd->fe_scaffold_ato = ato;
# with a lookup of the fe_port and store there.

old_track = """\t\t\tpcd->fe_scaffold_gro = gro;
\t\t\tpcd->fe_scaffold_mto = mto;
\t\t\tpcd->fe_scaffold_ato = ato;"""

new_track = """\t\t\t{
\t\t\t\tstruct fman_pcd_fe_port *fp = fman_pcd_fe_port_find(pcd, (u8)port_id);
\t\t\t\tif (fp) {
\t\t\t\t\tfp->scaffold_gro = gro;
\t\t\t\t\tfp->scaffold_mto = mto;
\t\t\t\t\tfp->scaffold_ato = ato;
\t\t\t\t}
\t\t\t}"""

if old_track in src:
    src = src.replace(old_track, new_track, 1)
    changes += 1
    print("### F-139: scaffold tracking moved to per-port fp->scaffold_*")
else:
    print("### F-139: scaffold tracking assignment not found — may already be converted")
    # Non-fatal — the patch 0123 change may have already removed these

# ── 2. Remove the singleton scaffold fields from struct fman_pcd ──
# These are in patch 0152.  Comment them out.
old_fields = """\tunsigned long fe_scaffold_gro;\t/* group table (256 B) */
\tunsigned long fe_scaffold_mto;\t/* match table (16 B) */
\tunsigned long fe_scaffold_ato;\t/* AD table    (32 B) */"""

if old_fields in src:
    new_fields = """\t/* F-139: scaffold tracking moved to per-port struct fman_pcd_fe_port */
\t/* unsigned long fe_scaffold_gro; */
\t/* unsigned long fe_scaffold_mto; */
\t/* unsigned long fe_scaffold_ato; */"""
    src = src.replace(old_fields, new_fields, 1)
    changes += 1
    print("### F-139: singleton scaffold fields removed from struct fman_pcd")
else:
    print("### F-139: singleton scaffold fields not found — may already be removed")

# ── 3. Remove fman_pcd_fe_arm_free_scaffold() function body ──
# Replace the function body with a no-op that logs a warning if called.
free_func = "static void fman_pcd_fe_arm_free_scaffold(struct fman_pcd *pcd)"
if free_func in src:
    func_idx = src.index(free_func)
    brace_idx = src.index("{", func_idx)
    # Find the closing brace of this function
    depth = 0
    close_idx = brace_idx
    for i in range(brace_idx, len(src)):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                close_idx = i
                break
    old_body = src[brace_idx:close_idx+1]
    new_body = """{
\t/* F-139: Scaffold is now per-port, freed in fman_pcd_fe_port_del().
\t * This function is retained as a no-op for any legacy callers.
\t */
\tpr_debug("fman_pcd: fe_arm_free_scaffold no-op (scaffold now per-port)\\n");
}"""
    src = src[:brace_idx] + new_body + src[close_idx+1:]
    changes += 1
    print("### F-139: fman_pcd_fe_arm_free_scaffold() reduced to no-op")
else:
    print("### F-139: fman_pcd_fe_arm_free_scaffold not found")

# ── 4. Remove calls to fman_pcd_fe_arm_free_scaffold from disengage paths ──
# In __fman_pcd_fe_arm_disengage and the debugfs wrapper, remove the call.
# The scaffold is now freed in fe_port_del which is called before this.
call = "\tfman_pcd_fe_arm_free_scaffold(pcd);"
count = src.count(call)
if count > 0:
    src = src.replace(call, "\t/* F-139: scaffold now freed in fe_port_del */")
    changes += 1
    print(f"### F-139: removed {count} call(s) to fman_pcd_fe_arm_free_scaffold")
else:
    print("### F-139: no calls to fman_pcd_fe_arm_free_scaffold found")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-139: {changes} change(s) applied")
else:
    print("### F-139: no changes applied")
    sys.exit(1)
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
and fman_pcd_fe_arm_free_scaffold() are reduced to no-ops.

Must run AFTER 0123 (which defines struct fman_pcd_fe_port and fe_port_del).
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

# ── 0. Add scaffold fields to struct fman_pcd_fe_port ──
old_struct = """struct fman_pcd_fe_port {
\tstruct list_head node;
\tu8 port_id;
\tunsigned long pool_raw_off;\t/* unaligned off handed to free() */
\tsize_t pool_raw_size;
\tunsigned long mgmt_off;
\tsize_t mgmt_size;
};"""

new_struct = """struct fman_pcd_fe_port {
\tstruct list_head node;
\tu8 port_id;
\tunsigned long pool_raw_off;\t/* unaligned off handed to free() */
\tsize_t pool_raw_size;
\tunsigned long mgmt_off;
\tsize_t mgmt_size;
\tunsigned long scaffold_gro;\t/* CONT_LOOKUP group table (256 B) */
\tunsigned long scaffold_mto;\t/* match table (16 B) */
\tunsigned long scaffold_ato;\t/* AD table (32 B) */
};"""

if old_struct in src:
    src = src.replace(old_struct, new_struct, 1)
    changes += 1
    print("### F-139: added scaffold_gro/mto/ato to struct fman_pcd_fe_port")
else:
    print("### F-139: struct fman_pcd_fe_port not found — may already have scaffold fields")

# ── 1. Add scaffold free to fman_pcd_fe_port_del ──
# After the pool_raw free, before list_del
old_del = """\tfman_pcd_muram_free(pcd, fp->pool_raw_off, fp->pool_raw_size);
\tlist_del(&fp->node);"""

new_del = """\tfman_pcd_muram_free(pcd, fp->pool_raw_off, fp->pool_raw_size);
\t/* F-139: Free per-port CONT_LOOKUP scaffold (gro 256 + mto 16 + ato 32 = 304 B).
\t * Formerly a singleton in pcd->fe_scaffold_* that was overwritten by the
\t * second port's engage, orphaning the first port's scaffold permanently.
\t */
\tif (fp->scaffold_ato)
\t\tfman_pcd_muram_free(pcd, fp->scaffold_ato, 32);
\tif (fp->scaffold_mto)
\t\tfman_pcd_muram_free(pcd, fp->scaffold_mto, 16);
\tif (fp->scaffold_gro)
\t\tfman_pcd_muram_free(pcd, fp->scaffold_gro, 256);
\tlist_del(&fp->node);"""

if old_del in src:
    src = src.replace(old_del, new_del, 1)
    changes += 1
    print("### F-139: added scaffold free to fman_pcd_fe_port_del")
else:
    print("### F-139: fe_port_del free block not found — may already have scaffold free")

# ── 2. Add scaffold free to fman_pcd_fe_port_drain ──
old_drain = """\t\tfman_pcd_muram_free(pcd, fp->pool_raw_off, fp->pool_raw_size);
\t\tlist_del(&fp->node);"""

new_drain = """\t\tfman_pcd_muram_free(pcd, fp->pool_raw_off, fp->pool_raw_size);
\t\tif (fp->scaffold_ato)
\t\t\tfman_pcd_muram_free(pcd, fp->scaffold_ato, 32);
\t\tif (fp->scaffold_mto)
\t\t\tfman_pcd_muram_free(pcd, fp->scaffold_mto, 16);
\t\tif (fp->scaffold_gro)
\t\t\tfman_pcd_muram_free(pcd, fp->scaffold_gro, 256);
\t\tlist_del(&fp->node);"""

if old_drain in src:
    src = src.replace(old_drain, new_drain)
    changes += 1
    print("### F-139: added scaffold free to fman_pcd_fe_port_drain")
else:
    print("### F-139: fe_port_drain free block not found")

# ── 3. In fe_port_set, copy scaffold offsets from singleton to per-port ──
# The scaffold is allocated in __fman_pcd_fe_arm_engage BEFORE fe_port_set
# adds the port to fe_ports.  So fman_pcd_fe_port_find() returns NULL during
# engage.  Instead: keep the singleton assignment in engage, then copy to
# per-port in fe_port_set after list_add_tail, and zero the singleton.
#
# Find the list_add_tail in fe_port_set and add scaffold copy after it.
old_list_add = "\tlist_add_tail(&fp->node, &pcd->fe_ports);"
if old_list_add in src:
    new_list_add = """\tlist_add_tail(&fp->node, &pcd->fe_ports);
\t/* F-139: Copy scaffold offsets from singleton (set during engage before
\t * the port was in fe_ports) to per-port struct for teardown in fe_port_del.
\t */
\tfp->scaffold_gro = pcd->fe_scaffold_gro;
\tfp->scaffold_mto = pcd->fe_scaffold_mto;
\tfp->scaffold_ato = pcd->fe_scaffold_ato;
\tpcd->fe_scaffold_gro = 0;
\tpcd->fe_scaffold_mto = 0;
\tpcd->fe_scaffold_ato = 0;"""
    src = src.replace(old_list_add, new_list_add, 1)
    changes += 1
    print("### F-139: scaffold copy from singleton to per-port in fe_port_set")
else:
    print("### F-139: list_add_tail not found in fe_port_set")

# ── 4. Keep singleton scaffold fields in struct fman_pcd ──
# They are still used during engage (before the port is in fe_ports).
# fe_port_set copies them to per-port and zeros them.
# No change needed — the fields stay.
print("### F-139: singleton scaffold fields kept (used during engage, copied in fe_port_set)")

# ── 5. Reduce fman_pcd_fe_arm_free_scaffold() to no-op ──
free_func = "static void fman_pcd_fe_arm_free_scaffold(struct fman_pcd *pcd)"
if free_func in src:
    func_idx = src.index(free_func)
    brace_idx = src.index("{", func_idx)
    # Find the closing brace
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
}"""
    src = src[:brace_idx] + new_body + src[close_idx+1:]
    changes += 1
    print("### F-139: fman_pcd_fe_arm_free_scaffold() reduced to no-op")
else:
    print("### F-139: fman_pcd_fe_arm_free_scaffold not found")

# ── 6. Remove calls to fman_pcd_fe_arm_free_scaffold from disengage paths ──
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
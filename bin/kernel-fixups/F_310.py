"""F-310 (2026-11-09): FE teardown order -- stop CC dispatch, SYNC, then release.

plans/ASK2-MASTER-PLAN.md section 4.9 item T2. By the time of this fixup
F-269/F-291/F-293 already make ehash table drain SYNC before reclaiming records
and make port delete and PCD release fail closed on a table error. Three
ordering gaps remained in the generated source:

1. fman_pcd_kg_port_disarm_fe() cleared FMBM_RCCB *before* it put the port's
   KeyGen scheme back to RSS (fman_pcd_kg_port_detach_cc() was the last call).
   In that window the scheme is still AC_CC (0x80000006, CCOBASE 0), so every
   frame classified on the port dispatches through RCCB 0, i.e. into whatever
   MURAM offset 0 holds, parsed as an en_exthash_node. That is the
   stale-RCCB class F-134 recorded as a bus lockup. The inverse of arm is
   now: scheme to RSS first (the frame no longer goes to the CC engine), one
   FMFP_EXTC[INV0] SYNC so every FM_CTL task already dispatched through the
   old row completes, then RCCB 0 and the KG-direct/CC_EN clear, then the
   detach readback. The intermediate state (RSS scheme, RFPNE still
   0x00480200, RCCB still at the scaffold) is the state the G4 dispatch probe
   ran on .106 on 2026-11-09 (an RSS scheme bound beside the AC_CC scheme
   delivered every frame to the kernel), so it is a known-good state.
   A failed SYNC returns before RCCB changes: the scaffold stays allocated
   and the caller faults the port.

2. fman_pcd_fe_port_del() unpublished the FE workspace (params page +0x54)
   and then freed the management index, the workspace pool and the 256-byte
   scaffold (the RCCB rows) with no barrier of its own. The only barrier was
   the table drain SYNC, which is skipped when the port has no table, plus a
   5 ms sleep in the caller. A SYNC now sits between the unpublish and the
   frees. On failure everything is retained and -ETIMEDOUT goes to the caller
   (which faults the port, as for every other port_del error).

3. fman_pcd_fe_port_drain(), used only by fman_pcd_release(), freed every
   remaining port's MURAM without disarming KeyGen, without unpublishing the
   workspace and without any SYNC. A port still on the list at release is
   either armed (RCCB live) or faulted (state uncertain), so the drain now
   refuses (-EBUSY) and release keeps the PCD (its existing fail-closed path);
   other entries go through the checked fman_pcd_fe_port_del().

The new SYNC helper mirrors fman_pcd_ehash_sync() without a table: INV0 via
the F-293 serialized fman_fpm_extc_sync(), then the F-285 HC barrier, and
failures count in ehash_sync_timeouts/ehash_sync_wedged like every other
INV0 barrier. It adds no raw fman_set_fpm_extc() writer (F-293 inventory).

Idempotent (marker); every anchor must match exactly once.
"""

import sys

FM = "drivers/net/ethernet/freescale/fman/"
KG = FM + "fman_pcd_kg.c"
PCD = FM + "fman_pcd.c"
MARK = "F-310"

KG_DECL_OLD = "\tbool v6_torn_down = false;\n\tint i, err = 0;\n\tu8 id;\n"
KG_DECL_NEW = ("\tbool v6_torn_down = false;\n\tint i, err = 0;\n\tu8 id;\n"
               "\tunsigned int polls = 0;\t/* F-310 */\n\tu32 extc = 0;\n")

KG_TAIL_OLD = """\
\terr = fman_port_set_cc_base(rxport, 0);
\tif (err)
\t\treturn err;
\tfman_port_clear_kg_direct_scheme(rxport);
\terr = fman_port_verify_fe_detached(rxport);
\tif (err)
\t\treturn err;
\treturn fman_pcd_kg_port_detach_cc(pcd, hw_port_id);
}
EXPORT_SYMBOL_GPL(fman_pcd_kg_port_disarm_fe);
"""

KG_TAIL_NEW = """\
\t/*
\t * F-310: inverse of arm. Put the scheme back to RSS first so no frame is
\t * dispatched to the CC engine any more, SYNC so FM_CTL tasks already
\t * dispatched through this port's RCCB row finish, and only then clear
\t * RCCB. RCCB 0 under a live AC_CC scheme dispatches into MURAM offset 0.
\t * A failed SYNC leaves RCCB and the scaffold in place for the caller to
\t * retain.
\t */
\terr = fman_pcd_kg_port_detach_cc(pcd, hw_port_id);
\tif (err)
\t\treturn err;
\terr = fman_fpm_extc_sync(fman, &extc, &polls);
\tif (err) {
\t\tpr_err("fman_pcd: F-310 port 0x%02x disarm SYNC failed (%d, fmfp_extc=0x%08x); RCCB kept\\n",
\t\t       hw_port_id, err, extc);
\t\treturn err;
\t}
\terr = fman_port_set_cc_base(rxport, 0);
\tif (err)
\t\treturn err;
\tfman_port_clear_kg_direct_scheme(rxport);
\treturn fman_port_verify_fe_detached(rxport);
}
EXPORT_SYMBOL_GPL(fman_pcd_kg_port_disarm_fe);
"""

PORT_DEL_DECL_OLD = ("/* F-269 fail-closed teardown callers */\n"
                     "static int fman_pcd_fe_port_del(struct fman_pcd *pcd, u8 port_id)\n")
PORT_DEL_DECL_NEW = ("/* F-310: FM_CTL barrier before FE workspace/scaffold reuse. */\n"
                     "static int fman_pcd_fe_teardown_sync(struct fman_pcd *pcd);\n\n"
                     + PORT_DEL_DECL_OLD)

PORT_DEL_FREE_OLD = """\
\t\tpr_err("fman_pcd: FE port 0x%02x depletion reset readback failed\\n", port_id);
\t\treturn -EIO;
\t}

\tfman_pcd_muram_free(pcd, fp->mgmt_off, fp->mgmt_size);
"""

PORT_DEL_FREE_NEW = """\
\t\tpr_err("fman_pcd: FE port 0x%02x depletion reset readback failed\\n", port_id);
\t\treturn -EIO;
\t}

\t/*
\t * F-310: an FM_CTL task that read +0x54 before the detach, or that was
\t * dispatched through this port's scaffold rows before KG disarm, must
\t * finish before that MURAM is freed. The table drain SYNC above does not
\t * run when the port has no table. On failure keep every owner.
\t */
\terr = fman_pcd_fe_teardown_sync(pcd);
\tif (err) {
\t\tpr_err("fman_pcd: F-310 FE port 0x%02x teardown SYNC failed (%d); workspace retained\\n",
\t\t       port_id, err);
\t\treturn err;
\t}

\tfman_pcd_muram_free(pcd, fp->mgmt_off, fp->mgmt_size);
"""

HELPER_ANCHOR = "static int fman_pcd_ehash_recover(struct fman_pcd_ehash_table *t)\n{\n"
HELPER = """\
/*
 * F-310: FM_CTL barrier for FE teardown without an ehash table: INV0 through
 * the F-293 serialized helper, then the F-285 HC barrier. Counted with the
 * ehash barriers; a timeout poisons INV0 until cold boot (F-293).
 */
static int fman_pcd_fe_teardown_sync(struct fman_pcd *pcd)
{
\tstruct fman *fman = pcd ? fman_pcd_get_fman(pcd) : NULL;
\tunsigned int polls = 0;
\tu32 extc = 0;
\tint err;

\tif (!fman)
\t\treturn -ENODEV;
\tdma_wmb();
\terr = fman_fpm_extc_sync(fman, &extc, &polls);
\tif (err) {
\t\tehash_sync_timeouts++;
\t\tif (err == -ETIMEDOUT)
\t\t\tehash_sync_wedged++;
\t\treturn err;
\t}
\treturn fman_pcd_hc_ehash_sync(pcd);
}

"""

DRAIN_START = "static int fman_pcd_fe_port_drain(struct fman_pcd *pcd)\n{\n"
DRAIN_NEW = """\
static int fman_pcd_fe_port_drain(struct fman_pcd *pcd)
{
\tstruct fman_pcd_fe_port *fp, *tmp;

\t/*
\t * F-310 (release only): a port still listed here is armed (RCCB and the
\t * KG scheme still dispatch into its scaffold) or faulted (state
\t * uncertain). Never free its MURAM; release keeps the PCD instead.
\t * Anything else goes through the checked, SYNC-fenced port delete.
\t */
\tlist_for_each_entry_safe(fp, tmp, &pcd->fe_ports, node) {
\t\tint err;

\t\tif (test_bit(fp->port_id, pcd->fe_port_armed) ||
\t\t    test_bit(fp->port_id, pcd->fe_port_faulted)) {
\t\t\tpr_err("fman_pcd: F-310 FE port 0x%02x still %s at release; retained\\n",
\t\t\t       fp->port_id,
\t\t\t       test_bit(fp->port_id, pcd->fe_port_armed) ? "armed" : "faulted");
\t\t\treturn -EBUSY;
\t\t}
\t\terr = fman_pcd_fe_port_del(pcd, fp->port_id);
\t\tif (err)
\t\t\treturn err;
\t}
\treturn 0;
}
"""

RELEASE_OLD = "\"fman_pcd: teardown SYNC failed; retaining PCD and DMA owners (%d)\\n\",\n"
RELEASE_NEW = "\"fman_pcd: teardown incomplete; retaining PCD and DMA owners (%d)\\n\",\n"


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise ValueError(f"{label}: anchor found {n} times (expected 1)")
    return text.replace(old, new)


def main():
    try:
        files = {p: open(p).read() for p in (KG, PCD)}
    except FileNotFoundError as exc:
        print(f"### F-310: FATAL: {exc}")
        return 1
    if MARK in files[KG] and MARK in files[PCD]:
        print("### F-310: already applied")
        return 0
    if MARK in files[KG] or MARK in files[PCD]:
        print("### F-310: FATAL: partially applied")
        return 1
    try:
        files[KG] = replace_once(files[KG], KG_DECL_OLD, KG_DECL_NEW, "KG disarm locals")
        files[KG] = replace_once(files[KG], KG_TAIL_OLD, KG_TAIL_NEW, "KG disarm order")
        files[PCD] = replace_once(files[PCD], PORT_DEL_DECL_OLD, PORT_DEL_DECL_NEW,
                                  "port_del forward declaration")
        files[PCD] = replace_once(files[PCD], PORT_DEL_FREE_OLD, PORT_DEL_FREE_NEW,
                                  "port_del barrier before MURAM free")
        files[PCD] = replace_once(files[PCD], HELPER_ANCHOR, HELPER + HELPER_ANCHOR,
                                  "teardown SYNC helper")
        start = files[PCD].find(DRAIN_START)
        if start < 0 or files[PCD].count(DRAIN_START) != 1:
            raise ValueError("port_drain: anchor not found exactly once")
        end = files[PCD].index("\n}\n", start) + 3
        files[PCD] = files[PCD][:start] + DRAIN_NEW + files[PCD][end:]
        files[PCD] = replace_once(files[PCD], RELEASE_OLD, RELEASE_NEW, "release message")
    except ValueError as exc:
        print(f"### F-310: FATAL: {exc}")
        return 1
    for path, text in files.items():
        with open(path, "w") as stream:
            stream.write(text)
    print("### F-310: FE teardown order (detach->SYNC->RCCB 0; SYNC before workspace free; release drain fails closed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""F-311 (2026-11-09): FE engage order -- RCCB before the AC_CC scheme.

plans/ASK2-MASTER-PLAN.md section 4.9 item T2, the engage-side mirror of
F-310. In the generated source fman_pcd_kg_port_arm_fe() programmed the port's
KeyGen scheme to AC_CC (0x80000006, CCOBASE 0) first and only afterwards
called fman_port_set_cc_base(), which writes FMBM_RCCB, asserts the first-port
FMFP_EXTC[INV0] SYNC (F-168/F-215) and sets NIA_KG_CC_EN. Between the two,
every frame classified on the port was dispatched through RCCB 0 (its boot or
post-disarm value), i.e. into whatever MURAM offset 0 holds, parsed as an
en_exthash_node: the stale-RCCB class F-134 recorded as a bus lockup and that
F-310 removed from disarm. Worse, a failed RX port lookup returned -ENODEV
with the scheme left AC_CC and RCCB still 0.

The new order is configure-before-enable, the SDK FM_PORT_SetPCD order (CC
tree, then FMBM_RCCB, then the KeyGen schemes; see the fman_port_set_cc_base()
comment) and the exact inverse of F-310's disarm:

1. resolve the RX port and the scheme (no hardware touched on failure);
2. write the node's miss FQID (F-186; the node is not reachable yet);
3. fman_port_set_cc_base(): RCCB, first-port SYNC, CC_EN;
4. only then the scheme to AC_CC.

The intermediate state (RSS scheme, RCCB at the node, RFPNE 0x00480200) is
the one F-310's disarm passes through and the one the G4 dispatch probe ran
on .106 on 2026-11-09 (an RSS scheme beside an armed port delivered every
frame to the kernel); with KGSE_CCBS 0 an RSS scheme starts no CC walk.

Failure handling:
- set_cc_base fails (first-port SYNC refused/timed out): the scheme was never
  touched, so RCCB is cleared again (F-310 order with nothing in the CC
  engine) and the caller faults the port as before.
- the scheme vanished or its base FQID changed after the miss FQID was
  committed: RCCB cleared again, -ENODEV/-EBUSY.
- keygen_scheme_setup() fails: its AR commit may have reached the hardware
  (keygen_write_ar_wait timeout), so the scheme may be live AC_CC. RCCB is
  KEPT pointing at the valid node (fail closed, as F-310 keeps RCCB on a
  failed SYNC); the software slot is restored and the caller faults the port
  and stops RX.

*programming_started is set immediately before the first hardware write
(RCCB), so failures before it leave RX untouched exactly as F-291 requires.
The F-211 v6 block is unchanged; it runs after the v4 scheme is live and uses
the same rxport and miss_fqid.

Idempotent (marker); every anchor must match exactly once.
"""

import sys

KG = "drivers/net/ethernet/freescale/fman/fman_pcd_kg.c"
MARK = "F-311"
FUNC = "int fman_pcd_kg_port_arm_fe(struct fman_pcd *pcd, u8 hw_port_id,\n"

DECL_OLD = """\
\tstruct fman *fman;
\tstruct mutex *lock;
\tu8 id;
\tint err;

\tif (!programming_started)
\t\treturn -EINVAL;
"""

DECL_NEW = """\
\tstruct fman *fman;
\tstruct mutex *lock;
\tstruct keygen_scheme saved;\t/* F-311 */
\tu32 miss_fqid;
\tu8 id;
\tint err;

\tif (!programming_started)
\t\treturn -EINVAL;
"""

BODY_OLD = """\
\tlock = fman_pcd_get_lock(pcd);

\tmutex_lock(lock);
\tslot = kg_find_port_scheme(keygen, hw_port_id, &id);
\tif (!slot) {
\t\tmutex_unlock(lock);
\t\treturn -ENODEV;
\t}
\t*programming_started = true;
\tif (ekfc)
\t\tslot->ekfc = ekfc;
\tslot->ekfc_only = fman_pcd_fe_port_ekfc_only(pcd, hw_port_id); /* F-255 */
\t*saved_engine = slot->next_engine;

\t\t/*
\t * F-185(arm-fe-accc): vendor dispatch mode (NXP ASK SDK production
\t * path, .106: 12 schemes 0x8x000006, ccbs=0, 0% loss under load).
\t * E23 (2026-08-12) decoded the 210.10.1 CC dispatch: the
\t * CONT_LOOKUP path is the enhanced external-hash machine parsing
\t * the RCCB-target AD as an en_exthash_node VARIANT B -- F-185's
\t * engage scaffold writes it. F-183's CCBS-implicit + RM-group-AD
\t * form feeds the machine garbage (frames consumed, no
\t * disposition). The historical AC_CC stalls (0118 iter-48,
\t * Path A 08-10) were invalid-CONTENT stalls (bare FE_ENTER =
\t * node with table_base=0, pool=0 -> pool-0 wait), now fixed by
\t * the node. Vendor: EN|NIA_ENG_FM_CTL|AC_CC, CCOBASE=0, ccbs=0.
\t */
\tslot->next_engine    = 3;
\tslot->cc_base_offset = 0;
\tslot->cc_bits_sel    = 0;
\t/* F-186(miss-fqid-capture): the node word3 miss action is a
\t * direct ENQUE to the scheme's OWN base FQID (E25: cross-port
\t * fqb enqueues drop in the dpaa driver -- the FD's buffer
\t * belongs to the frame's own BM pool). Capture under the lock;
\t * committed after mutex_unlock, before the EXTC SYNC.
\t */
\tu32 miss_fqid = slot->base_fqid;
\tslot->used = false;
\terr = keygen_scheme_setup(keygen, id, true);
\tif (err) {
\t\tslot->next_engine = *saved_engine;
\t\tslot->cc_bits_sel = 0;
\t\tslot->used = false;
\t\tmutex_unlock(lock);
\t\treturn err;
\t}
\tmutex_unlock(lock);

\t/* F-186(miss-fqid-commit): complete the vendor node before it
\t * goes live -- with miss_action_type = ENQUE (F-186(enque-miss)),
\t * word3 is the miss FQID: a direct enqueue to the scheme's own
\t * base FQID (slot->base_fqid, e.g. 0x300 for eth4). E25 proved
\t * the F-185 KG-direct NIA form loops forever on 210.10.1 and
\t * cross-port fqb enqueues drop in the dpaa driver. No-op unless
\t * engage built the node form. Lands before
\t * fman_port_set_cc_base() asserts EXTC SYNC.
\t */
\tfman_pcd_fe_node_set_miss_nia(pcd, fe_enter_off, miss_fqid);

\trxport = fman_port_lookup_rx(fman, hw_port_id);
\tif (!rxport)
\t\treturn -ENODEV;
\terr = fman_port_set_cc_base(rxport, fe_enter_off);
\tif (err) {
\t\tmutex_lock(lock);
\t\tslot = kg_find_port_scheme(keygen, hw_port_id, &id);
\t\tif (slot) {
\t\t\tslot->next_engine = *saved_engine;
\t\t\tslot->cc_bits_sel = 0;
\t\t\tslot->used = false;
\t\t\t(void)keygen_scheme_setup(keygen, id, true);
\t\t}
\t\tmutex_unlock(lock);
\t\treturn err;
\t}

"""

BODY_NEW = """\
\tlock = fman_pcd_get_lock(pcd);

\t/*
\t * F-311: configure before enable, the inverse of F-310's disarm and the
\t * SDK FM_PORT_SetPCD order (CC tree, FMBM_RCCB, then KeyGen schemes).
\t * The scheme goes AC_CC only after RCCB points at the node, the
\t * first-port SYNC has completed and CC_EN is set; before, frames on the
\t * port were dispatched through RCCB 0 into MURAM offset 0.
\t */
\trxport = fman_port_lookup_rx(fman, hw_port_id);
\tif (!rxport)
\t\treturn -ENODEV;
\tmutex_lock(lock);
\tslot = kg_find_port_scheme(keygen, hw_port_id, &id);
\tif (!slot) {
\t\tmutex_unlock(lock);
\t\treturn -ENODEV;
\t}
\t/* F-186(miss-fqid-capture): the node word3 miss action is a
\t * direct ENQUE to the scheme's OWN base FQID (E25: cross-port
\t * fqb enqueues drop in the dpaa driver -- the FD's buffer
\t * belongs to the frame's own BM pool). Captured under the lock,
\t * re-checked below before the scheme goes live.
\t */
\tmiss_fqid = slot->base_fqid;
\tmutex_unlock(lock);

\t/* F-186(miss-fqid-commit): complete the vendor node before it
\t * goes live -- with miss_action_type = ENQUE (F-186(enque-miss)),
\t * word3 is the miss FQID: a direct enqueue to the scheme's own
\t * base FQID (slot->base_fqid, e.g. 0x300 for eth4). E25 proved
\t * the F-185 KG-direct NIA form loops forever on 210.10.1 and
\t * cross-port fqb enqueues drop in the dpaa driver. No-op unless
\t * engage built the node form. The node is not reachable until
\t * RCCB below; it lands before fman_port_set_cc_base() asserts
\t * the first-port EXTC SYNC.
\t */
\tfman_pcd_fe_node_set_miss_nia(pcd, fe_enter_off, miss_fqid);

\t/* F-311: first hardware write. RCCB + first-port SYNC + CC_EN while
\t * the scheme is still RSS (KGSE_CCBS 0: no CC walk). On failure the
\t * scheme was never touched, so RCCB is simply cleared again.
\t */
\t*programming_started = true;
\terr = fman_port_set_cc_base(rxport, fe_enter_off);
\tif (err) {
\t\t(void)fman_port_set_cc_base(rxport, 0);
\t\treturn err;
\t}

\tmutex_lock(lock);
\tslot = kg_find_port_scheme(keygen, hw_port_id, &id);
\tif (!slot || slot->base_fqid != miss_fqid) {
\t\terr = slot ? -EBUSY : -ENODEV;
\t\tmutex_unlock(lock);
\t\tpr_err("fman_pcd: F-311 port 0x%02x scheme changed during arm (%d); RCCB cleared\\n",
\t\t       hw_port_id, err);
\t\t(void)fman_port_set_cc_base(rxport, 0);
\t\treturn err;
\t}
\tsaved = *slot;
\tif (ekfc)
\t\tslot->ekfc = ekfc;
\tslot->ekfc_only = fman_pcd_fe_port_ekfc_only(pcd, hw_port_id); /* F-255 */
\t*saved_engine = slot->next_engine;

\t/*
\t * F-185(arm-fe-accc): vendor dispatch mode (NXP ASK SDK production
\t * path, .106: 12 schemes 0x8x000006, ccbs=0, 0% loss under load).
\t * E23 (2026-08-12) decoded the 210.10.1 CC dispatch: the
\t * CONT_LOOKUP path is the enhanced external-hash machine parsing
\t * the RCCB-target AD as an en_exthash_node VARIANT B -- F-185's
\t * engage scaffold writes it. F-183's CCBS-implicit + RM-group-AD
\t * form feeds the machine garbage (frames consumed, no
\t * disposition). The historical AC_CC stalls (0118 iter-48,
\t * Path A 08-10) were invalid-CONTENT stalls (bare FE_ENTER =
\t * node with table_base=0, pool=0 -> pool-0 wait), now fixed by
\t * the node. Vendor: EN|NIA_ENG_FM_CTL|AC_CC, CCOBASE=0, ccbs=0.
\t */
\tslot->next_engine    = 3;
\tslot->cc_base_offset = 0;
\tslot->cc_bits_sel    = 0;
\tslot->used = false;
\terr = keygen_scheme_setup(keygen, id, true);
\tif (err) {
\t\t/*
\t\t * F-311: the AR commit may have reached the hardware, so the
\t\t * scheme may be live AC_CC. Keep RCCB at the valid node (fail
\t\t * closed, as F-310 keeps RCCB on a failed SYNC); the caller
\t\t * faults the port and stops RX.
\t\t */
\t\t*slot = saved;
\t\tmutex_unlock(lock);
\t\tpr_err("fman_pcd: F-311 port 0x%02x AC_CC scheme write failed (%d); RCCB kept\\n",
\t\t       hw_port_id, err);
\t\treturn err;
\t}
\tmutex_unlock(lock);

"""


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise ValueError(f"{label}: anchor found {n} times (expected 1)")
    return text.replace(old, new)


def main():
    try:
        src = open(KG).read()
    except FileNotFoundError as exc:
        print(f"### F-311: FATAL: {exc}")
        return 1
    if MARK in src:
        print("### F-311: already applied")
        return 0
    try:
        if src.count(FUNC) != 1:
            raise ValueError("fman_pcd_kg_port_arm_fe: definition not found exactly once")
        start = src.index(FUNC)
        end = src.index("\n}\n", start) + 3
        func = src[start:end]
        func = replace_once(func, DECL_OLD, DECL_NEW, "arm_fe locals")
        func = replace_once(func, BODY_OLD, BODY_NEW, "arm_fe order")
        src = src[:start] + func + src[end:]
    except ValueError as exc:
        print(f"### F-311: FATAL: {exc}")
        return 1
    with open(KG, "w") as stream:
        stream.write(src)
    print("### F-311: FE engage order (RCCB+SYNC+CC_EN before AC_CC scheme; scheme-write failure keeps RCCB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

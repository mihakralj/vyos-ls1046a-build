"""F-255 (T-M6-2 B1, 2026-10-07): per-port FE key profiles; L2 destination-MAC
profile for bridge offload. Dormant: nothing in-tree engages a non-routed
profile until ask.ko's bridge path does (B3), so routed ports are
byte-identical.

Why: a port has one KeyGen scheme, so one key format and one per-port ehash
table. Until now that format was hardwired: F-225 allocates every per-port
table with key_size 46, and F-224 replaces the EKFC of every AC_CC FE scheme
(next_engine 3) with the 46-byte dual-lane GECs. Bridge offload (and later
ESP/PPPoE/L2-tunnel offloads, ASK2 vendor parity) needs other key formats on
the ports that carry them. A profile is (EKFC, dual-lane GEC on/off, key
size); a port is engaged with one profile.

Profiles:
  ROUTED (0): EKFC 0x801C0006 (non-zero trigger only) + F-224 dual-lane GECs,
              46-byte key. Today's behaviour, unchanged.
  L2_DA  (1): EKFC 0x44000000 = KG_SCH_KN_MACDST | KG_SCH_KN_ETYPE, no GEC,
              8-byte key: the frame's destination MAC (6B, MSB-first bit30)
              followed by its EtherType (2B, bit26). One record per (FDB
              entry, EtherType) pair; the HIT action is a plain ENQUEUE_PKT
              to the egress port's TX FQ (ask.ko passes rx_fqid=TX FQ,
              tx_fqid=0: no L2 rewrite). Per-port tables already isolate the
              ingress port, so no PORT_ID byte (sidesteps its 0x00-vs-hwport
              ambiguity, bridge plan S8 point 1). An EKFC-only key on the
              ehash path is the silicon-proven pre-F-224 form (E25/E26
              HITs, 0x801C0006).
              EtherType was added to the key 2026-10 (plan §13.18) to fix a
              silicon stall: short non-IPv4/IPv6 frames (ARP, 0x88b5, ...)
              hitting a DA-only record permanently wedge the FE-VM hit path
              (plan §13.16). Folding EtherType into the key means a record
              installed for one EtherType (e.g. IPv4 0x0800) structurally
              cannot be hit by a frame of a different EtherType sharing the
              same DA -- ARP/other non-IP frames simply MISS the table and
              fall to the default/kernel FQ, which is the already-intended
              behaviour (plan D5: BUM/ARP/control traffic stays in the
              kernel). This avoids both the LCV/kgse_mv scheme-selection
              mechanism (shown non-viable for IPv4/IPv6 discrimination on
              FE-engaged ports, 2026-08-19 IPv6 family-selection finding)
              and any change to the proven-working HIT action/opcode chain.

Edits:
  include/linux/fsl/fman_pcd.h  enum fman_pcd_fe_profile + engage_profile().
  fman_pcd.c                    profile table; per-port profile array;
                                engage_profile() (engage = ROUTED wrapper);
                                arm_engage sizes the per-port table from the
                                port's profile; disengage resets to ROUTED;
                                accessor for fman_pcd_kg.c.
  fman_pcd_internal.h           accessor prototype.
  fman_keygen_internal.h        keygen_scheme.ekfc_only.
  fman_pcd_kg.c                 kg_port_arm_fe() sets slot->ekfc_only from
                                the port's profile on every arm.
  fman_keygen.c                 F-224 GEC override skipped when ekfc_only.

Engaging an already-armed port with a different profile fails -EBUSY
(disengage first); same profile stays idempotent. Idempotent re-run.
"""

import sys

FM = "drivers/net/ethernet/freescale/fman/"
MARK = "F-255"

EDITS = [
    ("include/linux/fsl/fman_pcd.h",
     """int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id,
			 u32 enq_fqid);
""",
     """int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id,
			 u32 enq_fqid);

/* F-255 (T-M6-2 B1): per-port FE key profile. A port has one KeyGen scheme,
 * hence one key format and one per-port ehash table; the profile selects
 * it. fman_pcd_fe_engage() == ROUTED. Re-engaging an armed port with a
 * different profile returns -EBUSY (disengage first). */
enum fman_pcd_fe_profile {
	FMAN_PCD_FE_PROFILE_ROUTED = 0,	/* 46B dual-lane IPv4/IPv6 5-tuple */
	FMAN_PCD_FE_PROFILE_L2_DA  = 1,	/* 8B dest MAC + EtherType (bridge FDB) */
	FMAN_PCD_FE_PROFILE_NR
};
#define FMAN_PCD_FE_L2_DA_KEY_SIZE	8	/* 2026-10: DA(6)+EtherType(2), plan §13.18 */
int fman_pcd_fe_engage_profile(struct fman *fm, u8 hw_port_id,
			       u32 enq_fqid, enum fman_pcd_fe_profile profile);
"""),
    (FM + "fman_pcd_internal.h",
     """struct mutex *fman_pcd_get_lock(struct fman_pcd *pcd);
""",
     """struct mutex *fman_pcd_get_lock(struct fman_pcd *pcd);
/* F-255: true when @hw_port_id's FE profile keys on EKFC alone (no F-224
 * dual-lane GEC override). */
bool fman_pcd_fe_port_ekfc_only(struct fman_pcd *pcd, u8 hw_port_id);
"""),
    (FM + "fman_keygen_internal.h",
     """	bool gec_dual_lane;""",
     """	bool ekfc_only;		/* F-255: AC_CC FE scheme keys on EKFC alone;
				 * keygen_scheme_setup() skips the F-224
				 * dual-lane GEC override. Set on every
				 * kg_port_arm_fe() from the port's profile. */
	bool gec_dual_lane;"""),
    (FM + "fman_pcd_kg.c",
     """	if (ekfc)
		slot->ekfc = ekfc;
	*saved_engine = slot->next_engine;
""",
     """	if (ekfc)
		slot->ekfc = ekfc;
	slot->ekfc_only = fman_pcd_fe_port_ekfc_only(pcd, hw_port_id); /* F-255 */
	*saved_engine = slot->next_engine;
"""),
    (FM + "fman_keygen.c",
     """		if (scheme->next_engine == 3 ||
		    (scheme->next_engine == 2 && scheme->gec_dual_lane)) {""",
     """		/* F-255: a non-routed FE profile (e.g. L2_DA) keys on its
		 * EKFC alone -- no dual-lane GEC override. */
		if ((scheme->next_engine == 3 && !scheme->ekfc_only) ||
		    (scheme->next_engine == 2 && scheme->gec_dual_lane)) {"""),
    (FM + "fman_pcd.c",
     """struct fman_pcd {
	struct fman *fman;
""",
     """struct fman_pcd {
	struct fman *fman;

	/* F-255: per-RX-port FE key profile (enum fman_pcd_fe_profile),
	 * indexed by hw_port_id (< 0x28). 0 = ROUTED. */
	u8 fe_port_profile[0x28];
"""),
    (FM + "fman_pcd.c",
     """static int fman_pcd_fe_verify_internal(struct fman_pcd *pcd, u8 hw_port_id);

static int __fman_pcd_fe_arm_engage(struct fman_pcd *pcd,
""",
     """static int fman_pcd_fe_verify_internal(struct fman_pcd *pcd, u8 hw_port_id);

/* F-255: FE key profiles. ekfc is what kg_port_arm_fe() programs; for
 * ROUTED it is only the non-zero trigger, F-224 then replaces it with the
 * dual-lane GECs (ekfc_only false). */
static const struct {
	u32  ekfc;
	bool ekfc_only;
	u8   key_size;
} fman_pcd_fe_profiles[FMAN_PCD_FE_PROFILE_NR] = {
	[FMAN_PCD_FE_PROFILE_ROUTED] = { 0x801C0006, false, 46 },
	[FMAN_PCD_FE_PROFILE_L2_DA]  = { 0x44000000 /* KG_SCH_KN_MACDST|KG_SCH_KN_ETYPE */,
					 true, FMAN_PCD_FE_L2_DA_KEY_SIZE },
};

static u8 fman_pcd_fe_port_profile(struct fman_pcd *pcd, u8 hw_port_id)
{
	return (pcd && hw_port_id < ARRAY_SIZE(pcd->fe_port_profile)) ?
		pcd->fe_port_profile[hw_port_id] : FMAN_PCD_FE_PROFILE_ROUTED;
}

bool fman_pcd_fe_port_ekfc_only(struct fman_pcd *pcd, u8 hw_port_id)
{
	return fman_pcd_fe_profiles[fman_pcd_fe_port_profile(pcd, hw_port_id)].ekfc_only;
}

static int __fman_pcd_fe_arm_engage(struct fman_pcd *pcd,
"""),
    (FM + "fman_pcd.c",
     """					    fman_pcd_ehash_table_set(pcd, 0x7FFF, 46, 0) == 0) { /* F-225(perport-46) */""",
     """					    fman_pcd_ehash_table_set(pcd, 0x7FFF,
						fman_pcd_fe_profiles[fman_pcd_fe_port_profile(pcd, (u8)port_id)].key_size,
						0) == 0) { /* F-225(perport-46), F-255 per-profile size */"""),
    (FM + "fman_pcd.c",
     """int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id,
			 u32 enq_fqid)
{
	struct fman_pcd *pcd;
	struct fman_port *rxport;
	unsigned int miss_fqid;
	int err;

	if (!fm || hw_port_id < 0x08 || hw_port_id >= 0x28)
		return -EINVAL;
	pcd = fman_get_pcd(fm);
	if (!pcd)
		return -ENXIO;

	rxport = fman_port_lookup_rx(fm, hw_port_id);
	if (!rxport)
		return -ENODEV;

	/* F-107/F-122: idempotent engage — return success if already armed. */
	if (test_bit(hw_port_id, pcd->fe_port_armed)) {
		pr_info("fman_pcd: FE engage port 0x%02x already armed (idempotent)\\n",
			hw_port_id);
		return 0;
	}
""",
     """int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id,
			 u32 enq_fqid)
{
	return fman_pcd_fe_engage_profile(fm, hw_port_id, enq_fqid,
					  FMAN_PCD_FE_PROFILE_ROUTED);
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_engage);

int fman_pcd_fe_engage_profile(struct fman *fm, u8 hw_port_id,
			       u32 enq_fqid, enum fman_pcd_fe_profile profile)
{
	struct fman_pcd *pcd;
	struct fman_port *rxport;
	unsigned int miss_fqid;
	int err;

	if (!fm || hw_port_id < 0x08 || hw_port_id >= 0x28 ||
	    profile >= FMAN_PCD_FE_PROFILE_NR)
		return -EINVAL;
	pcd = fman_get_pcd(fm);
	if (!pcd)
		return -ENXIO;

	rxport = fman_port_lookup_rx(fm, hw_port_id);
	if (!rxport)
		return -ENODEV;

	/* F-107/F-122: idempotent engage — return success if already armed.
	 * F-255: only for the same profile; a different one needs a
	 * disengage first (the scheme and per-port table are profile-shaped). */
	if (test_bit(hw_port_id, pcd->fe_port_armed)) {
		if (pcd->fe_port_profile[hw_port_id] != profile) {
			pr_warn("fman_pcd: FE engage port 0x%02x profile %u refused: armed with profile %u\\n",
				hw_port_id, profile, pcd->fe_port_profile[hw_port_id]);
			return -EBUSY;
		}
		pr_info("fman_pcd: FE engage port 0x%02x already armed (idempotent)\\n",
			hw_port_id);
		return 0;
	}
	pcd->fe_port_profile[hw_port_id] = profile;	/* read by arm_engage/arm_fe */
"""),
    (FM + "fman_pcd.c",
     """		err = __fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid, 0x801C0006);
	if (err)
		return err;

	err = fman_pcd_fe_port_set(pcd, hw_port_id);
	if (err) {
		__fman_pcd_fe_arm_disengage(pcd, hw_port_id);
		return err;
	}

	pr_info("fman_pcd: FE engage port 0x%02x FQ=0x%x (AC_CC)\\n",
		hw_port_id, miss_fqid);
	return 0;
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_engage);
""",
     """		err = __fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid,
					       fman_pcd_fe_profiles[profile].ekfc); /* F-255 */
	if (err) {
		pcd->fe_port_profile[hw_port_id] = FMAN_PCD_FE_PROFILE_ROUTED;
		return err;
	}

	err = fman_pcd_fe_port_set(pcd, hw_port_id);
	if (err) {
		__fman_pcd_fe_arm_disengage(pcd, hw_port_id);
		pcd->fe_port_profile[hw_port_id] = FMAN_PCD_FE_PROFILE_ROUTED;
		return err;
	}

	pr_info("fman_pcd: FE engage port 0x%02x FQ=0x%x (AC_CC) profile %u key %uB\\n",
		hw_port_id, miss_fqid, profile,
		fman_pcd_fe_profiles[profile].key_size);
	return 0;
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_engage_profile);
"""),
    # Bridge plan §13.2 G6: F-221 falls back to the global template table when
    # the per-port alloc fails. That table is sized for ROUTED, so a non-routed
    # profile would arm its 8B EKFC against a 50B comparator and report
    # success. Fail closed instead. The scaffold is not yet recorded in
    # pcd->fe_scaffold_* here (F-139), so freeing the three blocks is the whole
    # unwind (F-125 pattern).
    (FM + "fman_pcd.c",
     """					if (pcd->fe_arming_v4_table)
						et = pcd->fe_arming_v4_table;
""",
     """					if (pcd->fe_arming_v4_table)
						et = pcd->fe_arming_v4_table;
					else if (fman_pcd_fe_port_profile(pcd, (u8)port_id) !=
						 FMAN_PCD_FE_PROFILE_ROUTED) {	/* F-255 G6 */
						fman_pcd_muram_free(pcd, gro, 256);
						fman_pcd_muram_free(pcd, mto, 64);
						fman_pcd_muram_free(pcd, ato, 32);
						pr_warn("fman_pcd fe_arm: port 0x%02x per-port table alloc failed; profile %u has no fallback\\n",
							port_id,
							fman_pcd_fe_port_profile(pcd, (u8)port_id));
						return -ENOMEM;
					}
"""),
    (FM + "fman_pcd.c",
     """	__fman_pcd_fe_arm_disengage(pcd, hw_port_id);
	/* F-129: Tear down shared FE-VM chain on last port disengage.""",
     """	__fman_pcd_fe_arm_disengage(pcd, hw_port_id);
	if (hw_port_id < ARRAY_SIZE(pcd->fe_port_profile))	/* F-255 */
		pcd->fe_port_profile[hw_port_id] = FMAN_PCD_FE_PROFILE_ROUTED;
	/* F-129: Tear down shared FE-VM chain on last port disengage."""),
]

src = {}
for path, _, _ in EDITS:
    if path not in src:
        with open(path) as f:
            src[path] = f.read()

if all(MARK in s for s in src.values()):
    print("### F-255: already applied")
    sys.exit(0)

for path, old, new in EDITS:
    n = src[path].count(old)
    if n != 1:
        print(f"### F-255: FATAL: anchor in {path} found {n} times (expected 1): {old[:60]!r}")
        sys.exit(1)
    src[path] = src[path].replace(old, new)

for path, s in src.items():
    with open(path, "w") as f:
        f.write(s)
print("### F-255: per-port FE key profiles (ROUTED 46B dual-lane, L2_DA 8B MACDST+EtherType; dormant)")

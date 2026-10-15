// SPDX-License-Identifier: BSD-3-Clause OR GPL-2.0-or-later
/*
 * dpaa_fman_caps.c - FMan PCD capability detection + HW-offload stubs
 *
 * Observability-only stub for spec sec 3.5 (cap detection) and sec 5.4
 * (CC steering API).  All productive paths return -ENOTSUPP; the only
 * runtime knob is the dpaa_fman_caps.force= module parameter which
 * allows developers to simulate "ucode 210 loaded" for unit testing
 * downstream consumers without flashing real ucode.
 *
 * Spec: specs/dpaa1-afxdp-modernization-spec.md v5.0 sec 3.5 + sec 5.4.
 */

#include <linux/bitops.h>
#include <linux/device.h>
#include <linux/errno.h>
#include <linux/export.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/moduleparam.h>
#include <linux/printk.h>
#include <linux/slab.h>
#include <linux/types.h>
#include <linux/delay.h>
#include <linux/etherdevice.h>
#include <linux/list.h>
#include <linux/mutex.h>
#include <linux/refcount.h>

#include <linux/of.h>
#include <linux/string.h>
#include <linux/fsl/fman_pcd.h>

#include "dpaa_fman_caps.h"
#include "fman.h"
#include "fman_port.h"

/* dpaa_fman_caps.force=<u32>
 *
 * Default 0: report "no PCD caps" matching mainline ucode 106 silicon.
 * Operators can set bits per FMAN_CAP_* to simulate ucode 210 loaded
 * for development / CI of downstream consumers.  This will be removed
 * once the productive ucode-version probe lands (planned: parse FMan
 * firmware header from MURAM at fman_load_firmware() time).
 */
static u32 dpaa_fman_caps_force;
module_param_named(force, dpaa_fman_caps_force, uint, 0444);
MODULE_PARM_DESC(force,
 "Force FMan PCD capability bitmask (FMAN_CAP_* bits) for dev/CI. "
 "Default 0 = no PCD offload (mainline ucode 106 behaviour).");

/* Cached after first successful DT probe so subsequent dpaa_eth_probe()
 * calls (one per netdev, 5x on Mono Gateway DK) don't re-walk the DT
 * for the same firmware blob. -1 = not yet probed; >=0 = cached bitmask.
 */
static int dpaa_fman_caps_cached = -1;

/* Map an ASCII version string from the FMan ucode header id field to
 * the FMAN_CAP_* bitmask documented in spec sec 3.5.  The NXP QEF
 * header id is always of the form
 *
 *   "Microcode version <maj>.<min>.<rev> for <SoC> <rev>"
 *
 * with a NUL terminator. We only need <maj> here:
 *   maj >= 210: PCD primitives are present (CC, HM, POL, PARSER)
 *               but NOT host-command dispatch (per PR13 hardware probe
 *               on Mono Gateway DK 2026-05-13 — the standard 210.10.1
 *               QEF ucode does not implement the HC doorbell).
 *   maj == 106 (or anything < 210): no PCD primitives.
 *
 * Returns the bitmask, or 0 if the id string is unparseable.
 */
static u32 dpaa_fman_caps_from_id(const char *id, size_t id_len)
{
	const char prefix[] = "Microcode version ";
	unsigned int major = 0;
	size_t i;

	if (id_len < sizeof(prefix))
		return 0;
	if (memcmp(id, prefix, sizeof(prefix) - 1) != 0)
		return 0;

	i = sizeof(prefix) - 1;
	while (i < id_len && id[i] >= '0' && id[i] <= '9') {
		major = major * 10 + (id[i] - '0');
		if (major > 9999)
			return 0;
		i++;
}
	if (i == sizeof(prefix) - 1 || (id[i] != '.' && id[i] != ' '))
		return 0;

	if (major >= 210)
		return FMAN_CAP_CC_EXACT_MATCH | FMAN_CAP_HM_NODES |
		       FMAN_CAP_POLICER_TRTCM | FMAN_CAP_PARSER_SOFTSEQ;
	return 0;
}

/* Walk the device tree looking for the FMan firmware blob U-Boot loaded
 * into MURAM. Layout (see include/soc/fsl/qe/qe.h struct qe_firmware):
 *   bytes 0..3  __be32 length
 *   bytes 4..6  magic "QEF"
 *   byte  7     version (==1 today)
 *   bytes 8..69 id[62]  NUL-terminated ASCII version string
 *
 * On Mono Gateway DK with U-Boot loading from SPI mtd4, this property
 * is at /soc/fman@1a00000/fman-firmware/fsl,firmware and the id reads
 * exactly "Microcode version 210.10.1 for LS1043 r1.0".
 *
 * Returns the cap bitmask on success, 0 on any DT error / missing blob /
 * unparseable header. Safe to call before any FMan driver probe.
 */
static u32 dpaa_fman_caps_probe_dt(void)
{
	struct device_node *fman_np, *fw_np;
	const u8 *fw;
	int fw_len;
	u32 caps;

	fman_np = of_find_compatible_node(NULL, NULL, "fsl,fman");
	if (!fman_np)
		return 0;

	fw_np = of_get_child_by_name(fman_np, "fman-firmware");
	of_node_put(fman_np);
	if (!fw_np)
		return 0;

	fw = of_get_property(fw_np, "fsl,firmware", &fw_len);
	if (!fw || fw_len < 70 /* header(8) + id[62] */) {
		of_node_put(fw_np);
		return 0;
}

	if (fw[4] != 'Q' || fw[5] != 'E' || fw[6] != 'F') {
		of_node_put(fw_np);
		return 0;
}

	/* id field is bytes 8..69 (62 bytes), NUL-terminated. */
	caps = dpaa_fman_caps_from_id((const char *)&fw[8], 62);
	of_node_put(fw_np);
	return caps;
}

u32 dpaa_fman_get_caps(void)
{
	u32 caps;

	/* Operator override (dev/CI) always wins. */
	if (dpaa_fman_caps_force)
		return dpaa_fman_caps_force & FMAN_CAP_ALL;

	if (dpaa_fman_caps_cached >= 0)
		return (u32)dpaa_fman_caps_cached;

	caps = dpaa_fman_caps_probe_dt();
	dpaa_fman_caps_cached = (int)caps;
	return caps;
}
EXPORT_SYMBOL_GPL(dpaa_fman_get_caps);

void dpaa_fman_caps_log(struct device *dev, u32 caps)
{
static bool logged;

if (logged)
return;
logged = true;

if (!caps) {
dev_info(dev,
 "FMan PCD caps = 0x00 (ucode <210 or DT probe failed; no PCD offload)\n");
return;
}

dev_info(dev,
 "FMan PCD caps = 0x%02x (%s%s%s%s%s)\n",
 caps,
 (caps & FMAN_CAP_CC_EXACT_MATCH) ? "CC "     : "",
 (caps & FMAN_CAP_HM_NODES)       ? "HM "     : "",
 (caps & FMAN_CAP_POLICER_TRTCM)  ? "POL "    : "",
 (caps & FMAN_CAP_HC_DISPATCH)    ? "HC "     : "",
 (caps & FMAN_CAP_PARSER_SOFTSEQ) ? "PARSER"  : "");
}
EXPORT_SYMBOL_GPL(dpaa_fman_caps_log);

/* ------------------------------------------------------------------ *
 * M3-3b stub: CC (Coarse Classification) steering API.
 *
 * Productive implementation replaces these bodies in a follow-up patch
 * per spec sec 5.4.  Until then every entry-point returns -ENOTSUPP so
 * downstream consumers (af_xdp_pool qband-select, ASK2 flowtable
 * bridge, vyos-1x set-system-offload-classify CLI) can wire calls
 * today and gracefully degrade on ucode-106 silicon.
 * ------------------------------------------------------------------ */

/*
 * Translate one host-endian struct fman_cc_key into the neutral, BE-ready
 * struct fman_pcd_cc_hw_key the fman-side installer consumes.  A field
 * participates iff it (or its mask) is non-zero; multi-byte fields are
 * converted host->FMan-big-endian here so the silicon-programming layer can
 * copy them verbatim into MURAM.
 */
static void dpaa_cc_key_to_hw(const struct fman_cc_key *k,
      struct fman_pcd_cc_hw_key *hw)
{
memset(hw, 0, sizeof(*hw));

if (k->is_ipv6)
hw->present |= FMAN_PCD_CC_HW_F_IPV6;

if (k->ethertype) {
hw->ethertype_be = cpu_to_be16(k->ethertype);
hw->present |= FMAN_PCD_CC_HW_F_ETHERTYPE;
}
if (k->proto) {
hw->proto = k->proto;
hw->present |= FMAN_PCD_CC_HW_F_PROTO;
}

if (k->is_ipv6) {
/* IPv6 addresses are already network-order byte arrays. */
memcpy(hw->src_ip6, k->src_ip6, sizeof(hw->src_ip6));
memcpy(hw->dst_ip6, k->dst_ip6, sizeof(hw->dst_ip6));
/* Presence keyed on a non-zero address. */
if (memchr_inv(k->src_ip6, 0, sizeof(k->src_ip6)))
hw->present |= FMAN_PCD_CC_HW_F_SRC_IP;
if (memchr_inv(k->dst_ip6, 0, sizeof(k->dst_ip6)))
hw->present |= FMAN_PCD_CC_HW_F_DST_IP;
} else {
if (k->src_ip || k->src_ip_mask) {
hw->src_ip_be = cpu_to_be32(k->src_ip);
hw->src_ip_mask_be = k->src_ip_mask ? cpu_to_be32(k->src_ip_mask) : cpu_to_be32(0xffffffffu);
hw->present |= FMAN_PCD_CC_HW_F_SRC_IP;
}
if (k->dst_ip || k->dst_ip_mask) {
hw->dst_ip_be = cpu_to_be32(k->dst_ip);
hw->dst_ip_mask_be = k->dst_ip_mask ? cpu_to_be32(k->dst_ip_mask) : cpu_to_be32(0xffffffffu);
hw->present |= FMAN_PCD_CC_HW_F_DST_IP;
}
}

if (k->src_port) {
hw->src_port_be = cpu_to_be16(k->src_port);
hw->present |= FMAN_PCD_CC_HW_F_SRC_PORT;
}
if (k->dst_port) {
hw->dst_port_be = cpu_to_be16(k->dst_port);
hw->present |= FMAN_PCD_CC_HW_F_DST_PORT;
}

hw->target_qband = k->target_qband;
hw->target_fqid = k->target_fqid;
hw->hm_handle = k->hm_handle;
}

/*
 * Productive static CC-tree install (spec sec 5.4).  Gates on
 * FMAN_CAP_CC_EXACT_MATCH (BIT0) BEFORE touching MURAM per spec sec 3.5;
 * on a board without it (mainline ucode 106) returns -ENOTSUPP so the
 * flavor layer fails soft.  On the Mono Gateway DK the 210.10.1 QEF blob
 * lights CC up (caps = 0x17), so this path is live there.
 *
 * Translates the host-endian static tree into the neutral fman-side
 * descriptor and delegates the silicon programming to
 * fman_pcd_cc_static_install() in fsl_dpaa_fman.ko (which owns the MURAM
 * allocator).  add_key/remove_key stay -ENOTSUPP: they require the Host
 * Command doorbell (FMAN_CAP_HC_DISPATCH), which the board's ucode does
 * not implement.
 */
int fman_cc_tree_install(struct fman *fm, u8 port_id,
 const struct fman_cc_static_tree *spec)
{
struct fman_pcd_cc_hw_spec *hw;
struct fman_port *rxport;
struct fman_pcd *pcd;
u32 cc_base;
u16 i;
int err;

if (!fm || !spec)
return -EINVAL;
if (!(dpaa_fman_get_caps() & FMAN_CAP_CC_EXACT_MATCH))
return -ENOTSUPP;
if (spec->num_keys > FMAN_CC_MAX_STATIC_KEYS)
return -EINVAL;

pcd = fman_get_pcd(fm);
if (!pcd)
return -ENXIO;

hw = kzalloc(sizeof(*hw), GFP_KERNEL);
if (!hw)
return -ENOMEM;

hw->num_keys = spec->num_keys;
hw->miss_qband = spec->miss_qband;
hw->miss_fqid = spec->miss_fqid;
hw->miss_fe_off = spec->miss_fe_off;
for (i = 0; i < spec->num_keys; i++)
dpaa_cc_key_to_hw(&spec->keys[i], &hw->keys[i]);

err = fman_pcd_cc_static_install(pcd, port_id, hw);
kfree(hw);
if (err)
return err;

	/*
	 * Wire the datapath, SDK FM_PORT_SetPCD order (0115): bind the CC
	 * root at the RX port (FMBM_RCCB + parser CC-enable bit), THEN flip
	 * the port's RSS KeyGen scheme to CC next-engine (KGSE_MODE NIA =
	 * FM_CTL|AC_CC, CCOBASE = 0, CCBS = 0).  Without both halves the
	 * tree is installed but never walked - the exact silent
	 * non-enforcement failure mode the policer had (BUG 3 steering).
	 */
	err = fman_pcd_cc_static_get_base(pcd, port_id, &cc_base);
	if (!err) {
		rxport = fman_port_lookup_rx(fm, port_id);
		if (!rxport)
			err = -ENODEV;
	}
	if (!err)
		err = fman_pcd_port_ensure_params_page(pcd, rxport);
	if (!err)
		err = fman_port_set_cc_base(rxport, cc_base);
	if (!err) {
		err = fman_pcd_kg_port_attach_cc(pcd, port_id, cc_base);
		if (err)
			(void)fman_port_set_cc_base(rxport, 0);
	}
	if (err) {
		fman_pcd_cc_static_destroy(pcd, port_id);
		return err;
	}
	return 0;
}
EXPORT_SYMBOL_GPL(fman_cc_tree_install);

int fman_cc_tree_add_key(struct fman *fm, u8 port_id,
 const struct fman_cc_key *key, u32 *handle)
{
return -ENOTSUPP;
}
EXPORT_SYMBOL_GPL(fman_cc_tree_add_key);

int fman_cc_tree_remove_key(struct fman *fm, u8 port_id, u32 handle)
{
return -ENOTSUPP;
}
EXPORT_SYMBOL_GPL(fman_cc_tree_remove_key);

void fman_cc_tree_destroy(struct fman *fm, u8 port_id)
{
struct fman_pcd *pcd;

if (!fm)
return;
if (!(dpaa_fman_get_caps() & FMAN_CAP_CC_EXACT_MATCH))
return;
	pcd = fman_get_pcd(fm);
	if (pcd) {
		struct fman_port *rxport;

		/*
		 * Teardown order (0115): detach the KeyGen graft FIRST so no
		 * new frame dispatches into CC, then clear the port's
		 * FMBM_RCCB root (and its parser CC-enable bit), then release
		 * the MURAM tables.
		 */
		(void)fman_pcd_kg_port_detach_cc(pcd, port_id);
		rxport = fman_port_lookup_rx(fm, port_id);
		if (rxport)
			(void)fman_port_set_cc_base(rxport, 0);
	}
	/* F-135/F-136 + R4b wedge: let in-flight FMan tasks drain after
	 * detaching/clearing RCCB before freeing live-walked CC/HMTD MURAM. */
	usleep_range(5000, 6000);
	fman_pcd_cc_static_destroy(pcd, port_id);
}
EXPORT_SYMBOL_GPL(fman_cc_tree_destroy);


/* ------------------------------------------------------------------ *
 * M3-3c stub: HM (Header Manipulation) API.
 *
 * Productive implementation replaces these bodies in a follow-up patch
 * per spec sec 5.5. Until then both entry-points return -ENOTSUPP and
 * bump the dpaa_priv hw_offload_unavailable counter via the
 * downstream caller. Spec sec 5.5.
 * ------------------------------------------------------------------ */

/*
 * Translate the host-endian ordered-op list (struct fman_hm_spec, private
 * to fsl_dpa.ko) into the neutral struct fman_pcd_hm_hw_spec the fman-side
 * installer consumes.  The neutral HM fields carry HOST values (the encoder
 * in fman_pcd_manip.c assembles the inline payload word and writes it
 * big-endian via iowrite32be), so this is a one-to-one field copy: VLAN
 * tpid->tpid_be, vlan_id->vid, pcp->pcp; MPLS name-for-name.  VLAN_STRIP /
 * MPLS_POP carry no params.
 */
static void dpaa_hm_spec_to_hw(const struct fman_hm_spec *spec,
			      struct fman_pcd_hm_hw_spec *hw)
{
	u8 i;

	memset(hw, 0, sizeof(*hw));
	hw->num_ops = spec->num_ops;

	for (i = 0; i < spec->num_ops; i++) {
		const struct fman_hm_op *src = &spec->ops[i];
		struct fman_pcd_hm_hw_op *dst = &hw->ops[i];

		switch (src->type) {
		case FMAN_HM_OP_VLAN_STRIP:
			dst->type = FMAN_PCD_HM_HW_OP_VLAN_STRIP;
			break;
		case FMAN_HM_OP_VLAN_INSERT:
			dst->type = FMAN_PCD_HM_HW_OP_VLAN_INSERT;
			dst->vlan_insert.tpid_be = src->vlan_insert.tpid;
			dst->vlan_insert.vid = src->vlan_insert.vlan_id;
			dst->vlan_insert.pcp = src->vlan_insert.pcp;
			break;
		case FMAN_HM_OP_MPLS_PUSH:
			dst->type = FMAN_PCD_HM_HW_OP_MPLS_PUSH;
			dst->mpls_push.label = src->mpls_push.label;
			dst->mpls_push.tc = src->mpls_push.tc;
			dst->mpls_push.s = src->mpls_push.s;
			dst->mpls_push.ttl = src->mpls_push.ttl;
			break;
		case FMAN_HM_OP_MPLS_POP:
			dst->type = FMAN_PCD_HM_HW_OP_MPLS_POP;
			break;
		case FMAN_HM_OP_RMV_ETHERNET:
			dst->type = FMAN_PCD_HM_HW_OP_RMV_ETHERNET;
			break;
		case FMAN_HM_OP_INSRT_GENERIC:
			dst->type = FMAN_PCD_HM_HW_OP_INSRT_GENERIC;
			dst->insrt_generic.size = src->insrt_generic.size;
			memcpy(dst->insrt_generic.hdr, src->insrt_generic.hdr,
			       sizeof(dst->insrt_generic.hdr));
			break;
		case FMAN_HM_OP_IPV4_FORWARD:
			dst->type = FMAN_PCD_HM_HW_OP_IPV4_FORWARD;
			dst->ipv4_forward.dec_ttl = src->ipv4_forward.dec_ttl;
			dst->ipv4_forward.l4_csum = src->ipv4_forward.l4_csum;
			break;
		case FMAN_HM_OP_IPV6_FORWARD:
			dst->type = FMAN_PCD_HM_HW_OP_IPV6_FORWARD;
			dst->ipv6_forward.dec_hl = src->ipv6_forward.dec_hl;
			break;
		}
	}
}

/*
 * Productive HM node install (spec sec 5.5).  Gates on FMAN_CAP_HM_NODES
 * BEFORE touching MURAM per spec sec 3.5; on a board without it (mainline
 * ucode 106) returns -ENOTSUPP so the flavor layer fails soft.  On the Mono
 * Gateway DK the 210.10.1 QEF blob lights HM up (caps = 0x17), so this path
 * is live there.  Translates the host-endian op list into the neutral
 * fman-side descriptor and delegates the silicon programming to
 * fman_pcd_hm_install() in fsl_dpaa_fman.ko (which owns the MURAM
 * allocator).  On success *handle receives the HMTD MURAM byte offset, the
 * value a CC action carries as hm_handle.
 */
int fman_hm_node_install(struct fman *fm, u8 port_id,
			 const struct fman_hm_spec *spec, u32 *handle)
{
	struct fman_pcd_hm_hw_spec *hw;
	struct fman_pcd *pcd;
	int err;

	if (!fm || !spec || !handle)
		return -EINVAL;
	*handle = 0;
	if (!fman_hm_caps_supported())
		return -ENOTSUPP;
	if (spec->num_ops == 0 || spec->num_ops > FMAN_HM_MAX_OPS)
		return -EINVAL;

	pcd = fman_get_pcd(fm);
	if (!pcd)
		return -ENXIO;

	hw = kzalloc(sizeof(*hw), GFP_KERNEL);
	if (!hw)
		return -ENOMEM;

	dpaa_hm_spec_to_hw(spec, hw);

	err = fman_pcd_hm_install(pcd, port_id, hw, handle);
	kfree(hw);
	return err;
}
EXPORT_SYMBOL_GPL(fman_hm_node_install);

/*
 * Productive HM node destroy (spec sec 5.5).  Gates on FMAN_CAP_HM_NODES,
 * resolves the PCD and delegates to fman_pcd_hm_destroy(), which is
 * idempotent (a handle matching no live node is a no-op).  Returns 0 on a
 * board without HM caps (nothing was ever installed).
 */
int fman_hm_node_destroy(struct fman *fm, u8 port_id, u32 handle)
{
	struct fman_pcd *pcd;

	if (!fm)
		return 0;
	if (!fman_hm_caps_supported())
		return 0;
	pcd = fman_get_pcd(fm);
	if (pcd)
		return fman_pcd_hm_destroy(pcd, port_id, handle);
	return 0;
}
EXPORT_SYMBOL_GPL(fman_hm_node_destroy);

bool fman_hm_caps_supported(void)
{
	return (dpaa_fman_get_caps() & FMAN_CAP_HM_NODES) != 0;
}
EXPORT_SYMBOL_GPL(fman_hm_caps_supported);


/* ------------------------------------------------------------------ *
 * M3-3d stub: Policer (srTCM/trTCM) API.
 *
 * Productive implementation replaces these bodies in a follow-up patch
 * per spec sec 5.6. Until then fman_policer_install() returns -ENOTSUPP
 * and fman_policer_destroy() is an idempotent void no-op; downstream
 * callers bump the dpaa_priv hw_offload_unavailable counter on the
 * install failure. Spec sec 5.6.
 * ------------------------------------------------------------------ */

/*
 * Translate the host-endian struct fman_policer_profile (private to
 * fsl_dpa.ko) into the neutral struct fman_pcd_plcr_hw_profile the
 * fman-side installer consumes.  The neutral fields carry HOST values
 * (bits/sec, bytes); the fman-side encoder does the exp/mant rate-field
 * and 256-byte burst quantisation.  mode TRTCM->trtcm, color AWARE->
 * color_aware; rates/bursts copied verbatim.
 */
static void dpaa_plcr_prof_to_hw(const struct fman_policer_profile *prof,
				 struct fman_pcd_plcr_hw_profile *hw)
{
memset(hw, 0, sizeof(*hw));
hw->trtcm = (prof->mode == FMAN_POLICER_MODE_TRTCM);
hw->color_aware = (prof->color_mode == FMAN_POLICER_COLOR_AWARE);
hw->cir_bps = prof->cir_bps;
hw->cbs_bytes = prof->cbs_bytes;
hw->pir_bps = prof->pir_bps;
hw->pbs_bytes = prof->pbs_bytes;
}

/*
 * Productive policer install (spec sec 5.6).  Gates on
 * FMAN_CAP_POLICER_TRTCM BEFORE touching MURAM per spec sec 3.5; on a
 * board without it (mainline ucode 106) returns -ENOTSUPP so the flavor
 * layer fails soft.  On the Mono Gateway DK the 210.10.1 QEF blob lights
 * the policer up (caps = 0x17), so this path is live there.  Validates
 * the rate/burst inputs (cir_bps == 0 -> -EINVAL; a trTCM profile whose
 * pir_bps < cir_bps -> -ERANGE), translates the host-endian profile into
 * the neutral descriptor, resolves the PCD via fman_get_pcd(fm), and
 * delegates the silicon programming to fman_pcd_plcr_install() in
 * fsl_dpaa_fman.ko (which owns the MURAM allocator + rate encoder).
 */
int fman_policer_install(struct fman *fm, u8 port_id, u8 profile_id,
 const struct fman_policer_profile *prof)
{
struct fman_pcd_plcr_hw_profile *hw;
struct fman_pcd *pcd;
int err;

if (!fm || !prof)
return -EINVAL;
if (!fman_policer_caps_supported())
return -ENOTSUPP;
if (prof->cir_bps == 0)
return -EINVAL;
if (prof->mode == FMAN_POLICER_MODE_TRTCM &&
    prof->pir_bps < prof->cir_bps)
return -ERANGE;

pcd = fman_get_pcd(fm);
if (!pcd)
return -ENXIO;

hw = kzalloc(sizeof(*hw), GFP_KERNEL);
if (!hw)
return -ENOMEM;

dpaa_plcr_prof_to_hw(prof, hw);

err = fman_pcd_plcr_install(pcd, port_id, profile_id, hw);
kfree(hw);
return err;
}
EXPORT_SYMBOL_GPL(fman_policer_install);

/*
 * Productive policer destroy (spec sec 5.6).  Gates on
 * FMAN_CAP_POLICER_TRTCM, resolves the PCD and delegates to the
 * idempotent fman_pcd_plcr_destroy() (a (port, slot) matching no live
 * profile is a no-op).  No-op on a board without policer caps (nothing
 * was ever installed).
 */
void fman_policer_destroy(struct fman *fm, u8 port_id, u8 profile_id)
{
struct fman_pcd *pcd;

if (!fm)
return;
if (!fman_policer_caps_supported())
return;
pcd = fman_get_pcd(fm);
if (pcd)
fman_pcd_plcr_destroy(pcd, port_id, profile_id);
}
EXPORT_SYMBOL_GPL(fman_policer_destroy);

bool fman_policer_caps_supported(void)
{
return (dpaa_fman_get_caps() & FMAN_CAP_POLICER_TRTCM) != 0;
}
EXPORT_SYMBOL_GPL(fman_policer_caps_supported);

/* ------------------------------------------------------------------ *
 * Next-hop HM dedup (spec sec 13.4): one shared HMTD per L3 adjacency.
 *
 * A routed flow's per-key CC action references a header-manip handle that
 * pops the inbound Ethernet header, pushes the egress {dst, src, IPv4}
 * L2 header and decrements the IPv4 TTL.  Installing one HMTD per *flow*
 * exhausts MURAM (the M2 CPU gate observed 327x chain_create -ENOMEM at
 * ~21% load).  Every flow toward the same next hop shares one adjacency
 * (egress_tx_fqid, src_mac, dst_mac), so we cache a single fman_hm_node
 * per adjacency and refcount it: MURAM then scales O(next-hops) instead
 * of O(flows).  Microcode-neutral; mirrors the CDX route/conntrack split.
 *
 * The install path sleeps (fman_hm_node_install does GFP_KERNEL) and
 * callers hold no spinlock, so a plain mutex guards the table.
 * ------------------------------------------------------------------ */
struct fman_hm_nexthop {
	struct list_head	node;
	struct fman		*fm;
	u32			egress_tx_fqid;
	u8			src_mac[ETH_ALEN];
	u8			dst_mac[ETH_ALEN];
	u8			port_id;
	u32			handle;
	refcount_t		refcount;
};

static LIST_HEAD(fman_hm_nexthop_cache);
static DEFINE_MUTEX(fman_hm_nexthop_lock);

static struct fman_hm_nexthop *
fman_hm_nexthop_lookup(struct fman *fm, u32 egress_tx_fqid,
		       const u8 *src_mac, const u8 *dst_mac)
{
	struct fman_hm_nexthop *nh;

	list_for_each_entry(nh, &fman_hm_nexthop_cache, node) {
		if (nh->fm == fm &&
		    nh->egress_tx_fqid == egress_tx_fqid &&
		    ether_addr_equal(nh->src_mac, src_mac) &&
		    ether_addr_equal(nh->dst_mac, dst_mac))
			return nh;
	}
	return NULL;
}

/*
 * fman_hm_nexthop_get - resolve (and refcount) the shared HM node for an
 * L3 next-hop adjacency, installing it on first use.  On success *handle
 * receives the HMTD MURAM offset to embed in a CC FORWARD_FQ_WITH_MANIP
 * action.  Builds a 3-op spec {RMV_ETHERNET, INSRT_GENERIC(14-byte L2),
 * IPV4_FORWARD} the first time an adjacency is seen; later flows to the
 * same next hop share the cached node.  Sleepable; process context only.
 */
int fman_hm_nexthop_get(struct fman *fm, u8 port_id, u32 egress_tx_fqid,
			const u8 *src_mac, const u8 *dst_mac, u32 *handle)
{
	struct fman_hm_nexthop *nh;
	struct fman_hm_spec spec;
	struct fman_hm_op *op;
	int err;

	if (!fm || !src_mac || !dst_mac || !handle)
		return -EINVAL;
	*handle = 0;
	if (!fman_hm_caps_supported())
		return -ENOTSUPP;

	mutex_lock(&fman_hm_nexthop_lock);

	nh = fman_hm_nexthop_lookup(fm, egress_tx_fqid, src_mac, dst_mac);
	if (nh) {
		refcount_inc(&nh->refcount);
		*handle = nh->handle;
		mutex_unlock(&fman_hm_nexthop_lock);
		return 0;
	}

	nh = kzalloc(sizeof(*nh), GFP_KERNEL);
	if (!nh) {
		mutex_unlock(&fman_hm_nexthop_lock);
		return -ENOMEM;
	}

	memset(&spec, 0, sizeof(spec));
	spec.num_ops = 3;
	/* op 0: strip the inbound Ethernet header */
	spec.ops[0].type = FMAN_HM_OP_RMV_ETHERNET;
	/* op 1: push the egress {dst, src, ethertype=IPv4} L2 header */
	op = &spec.ops[1];
	op->type = FMAN_HM_OP_INSRT_GENERIC;
	op->insrt_generic.size = 2 * ETH_ALEN + 2;
	memcpy(&op->insrt_generic.hdr[0], dst_mac, ETH_ALEN);
	memcpy(&op->insrt_generic.hdr[ETH_ALEN], src_mac, ETH_ALEN);
	op->insrt_generic.hdr[2 * ETH_ALEN]     = 0x08; /* ETH_P_IP >> 8 */
	op->insrt_generic.hdr[2 * ETH_ALEN + 1] = 0x00; /* ETH_P_IP & 0xff */
	/* op 2: routed-frame IPv4 edits (decrement TTL, recompute cksums) */
	op = &spec.ops[2];
	op->type = FMAN_HM_OP_IPV4_FORWARD;
	op->ipv4_forward.dec_ttl = 1;
	op->ipv4_forward.l4_csum = 1;

	err = fman_hm_node_install(fm, port_id, &spec, &nh->handle);
	if (err) {
		kfree(nh);
		mutex_unlock(&fman_hm_nexthop_lock);
		return err;
	}

	nh->fm = fm;
	nh->egress_tx_fqid = egress_tx_fqid;
	nh->port_id = port_id;
	ether_addr_copy(nh->src_mac, src_mac);
	ether_addr_copy(nh->dst_mac, dst_mac);
	refcount_set(&nh->refcount, 1);
	list_add(&nh->node, &fman_hm_nexthop_cache);

	*handle = nh->handle;
	mutex_unlock(&fman_hm_nexthop_lock);
	return 0;
}
EXPORT_SYMBOL_GPL(fman_hm_nexthop_get);

/*
 * fman_hm_nexthop_put - drop a reference taken by fman_hm_nexthop_get().
 * When the last flow using an adjacency goes away the shared HM node is
 * destroyed and its MURAM reclaimed.  @handle is the value returned by the
 * matching _get().  Idempotent for an unknown handle (returns 0).
 */
int fman_hm_nexthop_put(struct fman *fm, u8 port_id, u32 handle)
{
	struct fman_hm_nexthop *nh, *found = NULL;
	int err = 0;

	if (!fm)
		return 0;

	mutex_lock(&fman_hm_nexthop_lock);
	list_for_each_entry(nh, &fman_hm_nexthop_cache, node) {
		if (nh->fm == fm && nh->handle == handle) {
			found = nh;
			break;
		}
	}
	if (found && refcount_dec_and_test(&found->refcount)) {
		list_del(&found->node);
		err = fman_hm_node_destroy(found->fm, found->port_id,
					   found->handle);
		kfree(found);
	}
	mutex_unlock(&fman_hm_nexthop_lock);
	return err;
}
EXPORT_SYMBOL_GPL(fman_hm_nexthop_put);

/* ------------------------------------------------------------------ *
 * VLAN HM dedup (T-M6-8 R2): one shared HMTD per VLAN tag-edit adjacency.
 *
 * A VLAN flow's per-key CC action references a header-manip handle that
 * either strips the outermost ingress VLAN tag (POP) or inserts one egress
 * tag (PUSH).  As with the L3 next-hop dedup, installing one HMTD per *flow*
 * would exhaust MURAM, so every flow that needs the same tag edit toward the
 * same egress FQ shares one node keyed by
 * (is_push, vid, tpid, pcp, egress_tx_fqid) and refcounted.
 *
 * The install path sleeps (fman_hm_node_install does GFP_KERNEL) and callers
 * hold no spinlock, so a plain mutex guards the table.
 * ------------------------------------------------------------------ */
struct fman_hm_vlan_ent {
	struct list_head	node;
	struct fman		*fm;
	u32			egress_tx_fqid;
	u16			vid;
	u16			tpid;
	u8			pcp;
	bool			is_push;
	u8			port_id;
	u32			handle;
	refcount_t		refcount;
};

static LIST_HEAD(fman_hm_vlan_cache);
static DEFINE_MUTEX(fman_hm_vlan_lock);

static struct fman_hm_vlan_ent *
fman_hm_vlan_lookup(struct fman *fm, bool is_push, u16 vid, u16 tpid, u8 pcp,
		    u32 egress_tx_fqid)
{
	struct fman_hm_vlan_ent *v;

	list_for_each_entry(v, &fman_hm_vlan_cache, node) {
		if (v->fm == fm &&
		    v->is_push == is_push &&
		    v->egress_tx_fqid == egress_tx_fqid &&
		    (!is_push ||
		     (v->vid == vid && v->tpid == tpid && v->pcp == pcp)))
			return v;
	}
	return NULL;
}

/*
 * fman_hm_vlan_get - resolve (and refcount) the shared VLAN HM node for a
 * tag-edit adjacency, installing it on first use.  @is_push selects INSERT
 * (push @vid/@tpid/@pcp) vs STRIP (pop the outermost tag).  On success
 * *handle receives the HMTD MURAM offset to embed as a CC leaf hm_handle.
 */
int fman_hm_vlan_get(struct fman *fm, u8 port_id, bool is_push,
		     u16 vid, u16 tpid, u8 pcp, u32 egress_tx_fqid,
		     u32 *handle)
{
	struct fman_hm_vlan_ent *v;
	struct fman_hm_spec spec;
	struct fman_hm_op *op;
	int err;

	if (!fm || !handle)
		return -EINVAL;
	*handle = 0;
	if (!fman_hm_caps_supported())
		return -ENOTSUPP;
	if (is_push && (vid == 0 || vid > 4094))
		return -EINVAL;

	mutex_lock(&fman_hm_vlan_lock);

	v = fman_hm_vlan_lookup(fm, is_push, vid, tpid, pcp, egress_tx_fqid);
	if (v) {
		refcount_inc(&v->refcount);
		*handle = v->handle;
		mutex_unlock(&fman_hm_vlan_lock);
		return 0;
	}

	v = kzalloc(sizeof(*v), GFP_KERNEL);
	if (!v) {
		mutex_unlock(&fman_hm_vlan_lock);
		return -ENOMEM;
	}

	memset(&spec, 0, sizeof(spec));
	spec.num_ops = 1;
	op = &spec.ops[0];
	if (is_push) {
		op->type = FMAN_HM_OP_VLAN_INSERT;
		op->vlan_insert.vlan_id = vid;
		op->vlan_insert.tpid = tpid ? tpid : 0x8100;
		op->vlan_insert.pcp = pcp & 0x7;
	} else {
		op->type = FMAN_HM_OP_VLAN_STRIP;
	}

	err = fman_hm_node_install(fm, port_id, &spec, &v->handle);
	if (err) {
		kfree(v);
		mutex_unlock(&fman_hm_vlan_lock);
		return err;
	}

	v->fm = fm;
	v->egress_tx_fqid = egress_tx_fqid;
	v->vid = vid;
	v->tpid = tpid ? tpid : 0x8100;
	v->pcp = pcp & 0x7;
	v->is_push = is_push;
	v->port_id = port_id;
	refcount_set(&v->refcount, 1);
	list_add(&v->node, &fman_hm_vlan_cache);

	*handle = v->handle;
	mutex_unlock(&fman_hm_vlan_lock);
	return 0;
}
EXPORT_SYMBOL_GPL(fman_hm_vlan_get);

/*
 * fman_hm_vlan_put - drop a reference taken by fman_hm_vlan_get().  Destroys
 * the shared HM node and reclaims its MURAM when the last user goes away.
 * Idempotent for an unknown handle (returns 0).
 */
int fman_hm_vlan_put(struct fman *fm, u8 port_id, u32 handle)
{
	struct fman_hm_vlan_ent *v, *found = NULL;
	int err = 0;

	if (!fm)
		return 0;

	mutex_lock(&fman_hm_vlan_lock);
	list_for_each_entry(v, &fman_hm_vlan_cache, node) {
		if (v->fm == fm && v->handle == handle) {
			found = v;
			break;
		}
	}
	if (found && refcount_dec_and_test(&found->refcount)) {
		list_del(&found->node);
		err = fman_hm_node_destroy(found->fm, found->port_id,
					   found->handle);
		kfree(found);
	}
	mutex_unlock(&fman_hm_vlan_lock);
	return err;
}
EXPORT_SYMBOL_GPL(fman_hm_vlan_put);

/* ------------------------------------------------------------------ *
 * VLAN+route HM dedup (T-M6-8 R4a): one shared HMTD that does BOTH the
 * VLAN tag edit AND the L3 next-hop rewrite for a routed-VLAN flow.
 *
 * R3b proved a VLAN-only strip HMTD sustains, but a real routed-VLAN flow
 * must, in one HMTD chain, edit the tag AND rebuild L2 (dst=next-hop MAC,
 * src=egress MAC) + decrement the IPv4 TTL - the CC enqueue itself rewrites
 * nothing.  This composes R2's VLAN op with the next-hop op-set (0119/0120):
 *   POP+route :  {VLAN_STRIP, RMV_ETHERNET, INSRT_GENERIC(L2), IPV4_FORWARD}
 *   PUSH+route:  {RMV_ETHERNET, INSRT_GENERIC(L2), IPV4_FORWARD, VLAN_INSERT}
 * (VLAN_INSERT last so the tag rides the rebuilt L2 header.)
 *
 * Refcounted like the other dedup caches, keyed by the full
 * (is_push, vid, tpid, pcp, dst_mac, src_mac, egress_tx_fqid) adjacency, so
 * MURAM stays O(routed-VLAN adjacencies) not O(flows).  Sleepable; process
 * context only.  For a *bridged* (non-routed) VLAN flow keep using the
 * VLAN-only fman_hm_vlan_get().
 * ------------------------------------------------------------------ */
struct fman_hm_vlan_route_ent {
	struct list_head	node;
	struct fman		*fm;
	u32			egress_tx_fqid;
	u16			vid;
	u16			tpid;
	u8			pcp;
	bool			do_pop;
	bool			do_push;
	bool			is_v6;
	u8			src_mac[ETH_ALEN];
	u8			dst_mac[ETH_ALEN];
	u8			port_id;
	u32			handle;
	refcount_t		refcount;
};

static LIST_HEAD(fman_hm_vlan_route_cache);
static DEFINE_MUTEX(fman_hm_vlan_route_lock);

static struct fman_hm_vlan_route_ent *
fman_hm_vlan_route_lookup(struct fman *fm, bool do_pop, bool do_push,
			  bool is_v6, u16 vid, u16 tpid, u8 pcp,
			  const u8 *src_mac, const u8 *dst_mac,
			  u32 egress_tx_fqid)
{
	struct fman_hm_vlan_route_ent *v;

	list_for_each_entry(v, &fman_hm_vlan_route_cache, node) {
		if (v->fm == fm &&
		    v->do_pop == do_pop &&
		    v->do_push == do_push &&
		    v->is_v6 == is_v6 &&
		    v->egress_tx_fqid == egress_tx_fqid &&
		    ether_addr_equal(v->src_mac, src_mac) &&
		    ether_addr_equal(v->dst_mac, dst_mac) &&
		    (!do_push ||
		     (v->vid == vid && v->tpid == tpid && v->pcp == pcp)))
			return v;
	}
	return NULL;
}

/*
 * Ethertype and L3-forward op differ by family; hop-limit decrement (v6)
 * needs no checksum recompute (no header checksum, hop limit is outside the
 * L4 pseudo-header) unlike TTL decrement (v4, l4_csum re-covers the pseudo-
 * header). NXP SDK fm_manip.c BuildHmct, HMCD_OPCODE_IPV6_UPDATE branch.
 */
static void fman_hm_vlan_route_l3(struct fman_hm_spec *spec, unsigned int i,
				  const u8 *dst_mac, const u8 *src_mac,
				  bool is_v6)
{
	struct fman_hm_op *op;

	spec->ops[i].type = FMAN_HM_OP_RMV_ETHERNET;
	op = &spec->ops[i + 1];
	op->type = FMAN_HM_OP_INSRT_GENERIC;
	op->insrt_generic.size = 2 * ETH_ALEN + 2;
	memcpy(&op->insrt_generic.hdr[0], dst_mac, ETH_ALEN);
	memcpy(&op->insrt_generic.hdr[ETH_ALEN], src_mac, ETH_ALEN);
	if (is_v6) {
		op->insrt_generic.hdr[2 * ETH_ALEN]     = 0x86; /* ETH_P_IPV6 >> 8 */
		op->insrt_generic.hdr[2 * ETH_ALEN + 1] = 0xdd; /* ETH_P_IPV6 & 0xff */
	} else {
		op->insrt_generic.hdr[2 * ETH_ALEN]     = 0x08; /* ETH_P_IP >> 8 */
		op->insrt_generic.hdr[2 * ETH_ALEN + 1] = 0x00; /* ETH_P_IP & 0xff */
	}
	op = &spec->ops[i + 2];
	if (is_v6) {
		op->type = FMAN_HM_OP_IPV6_FORWARD;
		op->ipv6_forward.dec_hl = 1;
	} else {
		op->type = FMAN_HM_OP_IPV4_FORWARD;
		op->ipv4_forward.dec_ttl = 1;
		op->ipv4_forward.l4_csum = 1;
	}
}

/*
 * fman_hm_vlan_route_get - resolve (and refcount) the shared combined
 * VLAN-edit + L3-forward HM node for a routed-VLAN adjacency, installing it on
 * first use.  @do_pop and @do_push are independent: POP-only strips the
 * ingress tag before the L2/L3 rebuild; PUSH-only inserts (@vid/@tpid/@pcp)
 * after it; BOTH set builds a same-port VID-to-VID TRANSLATE chain (strip the
 * old tag, rebuild L2/L3, insert the new tag last) -- e.g. two 802.1Q vifs on
 * one physical port routing between each other. At least one of @do_pop/
 * @do_push must be set. @is_v6 selects the L3 rebuild family (IPV4_FORWARD
 * vs IPV6_FORWARD, and the rebuilt L2 EtherType) and is part of the dedup
 * key. @dst_mac/@src_mac are the egress next-hop and egress-port MACs. On
 * success *handle receives the HMTD MURAM offset for a CC leaf hm_handle.
 * Sleepable; process context only.
 */
int fman_hm_vlan_route_get(struct fman *fm, u8 port_id,
			   bool do_pop, bool do_push, bool is_v6,
			   u16 vid, u16 tpid, u8 pcp,
			   const u8 *src_mac, const u8 *dst_mac,
			   u32 egress_tx_fqid, u32 *handle)
{
	struct fman_hm_vlan_route_ent *v;
	struct fman_hm_spec spec;
	struct fman_hm_op *op;
	unsigned int n;
	int err;

	if (!fm || !src_mac || !dst_mac || !handle)
		return -EINVAL;
	*handle = 0;
	if (!fman_hm_caps_supported())
		return -ENOTSUPP;
	if (!do_pop && !do_push)
		return -EINVAL;
	if (do_push && (vid == 0 || vid > 4094))
		return -EINVAL;

	mutex_lock(&fman_hm_vlan_route_lock);

	v = fman_hm_vlan_route_lookup(fm, do_pop, do_push, is_v6, vid, tpid, pcp,
				      src_mac, dst_mac, egress_tx_fqid);
	if (v) {
		refcount_inc(&v->refcount);
		*handle = v->handle;
		mutex_unlock(&fman_hm_vlan_route_lock);
		return 0;
	}

	v = kzalloc(sizeof(*v), GFP_KERNEL);
	if (!v) {
		mutex_unlock(&fman_hm_vlan_route_lock);
		return -ENOMEM;
	}

	/*
	 * Op order matches the vendor fill_actions convention (strip before,
	 * insert after the L2/L3 rebuild): STRIP (if popping) -> RMV_ETHERNET
	 * + INSRT_GENERIC(L2) + IPV4_FORWARD -> INSERT (if pushing). A
	 * translate chain is simply both ends present; the L3 rebuild in the
	 * middle is identical to the single-edit cases. Max 5 of
	 * FMAN_HM_MAX_OPS(8).
	 */
	memset(&spec, 0, sizeof(spec));
	n = 0;
	if (do_pop)
		spec.ops[n++].type = FMAN_HM_OP_VLAN_STRIP;

	fman_hm_vlan_route_l3(&spec, n, dst_mac, src_mac, is_v6);
	n += 3;

	if (do_push) {
		op = &spec.ops[n++];
		op->type = FMAN_HM_OP_VLAN_INSERT;
		op->vlan_insert.vlan_id = vid;
		op->vlan_insert.tpid = tpid ? tpid : 0x8100;
		op->vlan_insert.pcp = pcp & 0x7;
	}
	spec.num_ops = n;

	err = fman_hm_node_install(fm, port_id, &spec, &v->handle);
	if (err) {
		kfree(v);
		mutex_unlock(&fman_hm_vlan_route_lock);
		return err;
	}

	v->fm = fm;
	v->egress_tx_fqid = egress_tx_fqid;
	v->vid = vid;
	v->tpid = tpid ? tpid : 0x8100;
	v->pcp = pcp & 0x7;
	v->do_pop = do_pop;
	v->do_push = do_push;
	v->is_v6 = is_v6;
	v->port_id = port_id;
	ether_addr_copy(v->src_mac, src_mac);
	ether_addr_copy(v->dst_mac, dst_mac);
	refcount_set(&v->refcount, 1);
	list_add(&v->node, &fman_hm_vlan_route_cache);

	*handle = v->handle;
	mutex_unlock(&fman_hm_vlan_route_lock);
	return 0;
}
EXPORT_SYMBOL_GPL(fman_hm_vlan_route_get);

/*
 * fman_hm_vlan_route_put - drop a reference taken by fman_hm_vlan_route_get().
 * Destroys the shared HM node and reclaims its MURAM when the last user goes
 * away.  Idempotent for an unknown handle (returns 0).
 */
int fman_hm_vlan_route_put(struct fman *fm, u8 port_id, u32 handle)
{
	struct fman_hm_vlan_route_ent *v, *found = NULL;
	int err = 0;

	if (!fm)
		return 0;

	mutex_lock(&fman_hm_vlan_route_lock);
	list_for_each_entry(v, &fman_hm_vlan_route_cache, node) {
		if (v->fm == fm && v->handle == handle) {
			found = v;
			break;
		}
	}
	if (found && refcount_dec_and_test(&found->refcount)) {
		list_del(&found->node);
		err = fman_hm_node_destroy(found->fm, found->port_id,
					   found->handle);
		kfree(found);
	}
	mutex_unlock(&fman_hm_vlan_route_lock);
	return err;
}
EXPORT_SYMBOL_GPL(fman_hm_vlan_route_put);

MODULE_LICENSE("Dual BSD/GPL");
MODULE_DESCRIPTION("DPAA1 FMan PCD capability detection + HW-offload stubs");